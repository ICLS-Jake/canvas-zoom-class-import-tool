from __future__ import annotations

import json
import logging
import sys
import threading
import time
from typing import Any
from urllib.parse import quote, urljoin

import requests

from .config import AppConfig
from .errors import ApiError, AppError
from .models import CanvasCourse, RoleSpec
from .utils import (
    build_lti_signature,
    build_lti_signature_base_string,
    build_lti_signature_digest,
    build_lti_signature_parts,
    summarize_migration_issues,
)

LOGGER = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class HttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        source_name: str,
        timeout_seconds: float,
        max_retries: int,
        max_backoff_seconds: float,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.source_name = source_name
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_backoff_seconds = max_backoff_seconds
        self.default_headers = default_headers or {}
        self._local = threading.local()

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        data: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_status: set[int] | None = None,
        timeout_seconds: float | None = None,
        auth: tuple[str, str] | None = None,
    ) -> requests.Response:
        url = path_or_url if path_or_url.startswith("http") else urljoin(self.base_url + "/", path_or_url.lstrip("/"))
        timeout = timeout_seconds or self.timeout_seconds
        expected_status = expected_status or {200}
        session = self._session()

        for attempt in range(1, self.max_retries + 1):
            try:
                response = session.request(
                    method=method,
                    url=url,
                    params=params,
                    data=data,
                    json=json_body,
                    headers=headers,
                    timeout=timeout,
                    auth=auth,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt >= self.max_retries:
                    raise ApiError(
                        code="NET001",
                        message=f"{self.source_name} request failed after retries: {exc}",
                        source=self.source_name,
                        cause=exc,
                    ) from exc
                self._sleep_before_retry(attempt, None)
                continue

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                self._sleep_before_retry(attempt, response)
                continue

            if response.status_code not in expected_status:
                raise ApiError(
                    code="API001",
                    message=_extract_error_message(response),
                    source=self.source_name,
                    status_code=response.status_code,
                    response_body=_safe_json(response),
                )
            return response

        raise ApiError(code="NET002", message=f"{self.source_name} request exhausted retries.", source=self.source_name)

    def list_paginated(self, path_or_url: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = path_or_url
        next_params: dict[str, Any] | None = params

        while next_url:
            response = self.request("GET", next_url, params=next_params)
            page_data = _safe_json(response)
            if not isinstance(page_data, list):
                raise ApiError(
                    code="API002",
                    message=f"Expected a list response from {self.source_name} pagination.",
                    source=self.source_name,
                    response_body=page_data,
                )
            items.extend(page_data)
            next_url = _parse_link_header(response.headers.get("Link", "")).get("next")
            next_params = None
        return items

    def _session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            session = requests.Session()
            session.headers.update(self.default_headers)
            session.headers.setdefault("User-Agent", "canvas-zoom-course-setup/1.0")
            self._local.session = session
        return self._local.session

    def _sleep_before_retry(self, attempt: int, response: requests.Response | None) -> None:
        retry_after = None
        if response is not None:
            header_value = response.headers.get("Retry-After", "").strip()
            if header_value.isdigit():
                retry_after = float(header_value)
        delay = retry_after if retry_after is not None else min(2 ** (attempt - 1), self.max_backoff_seconds)
        LOGGER.warning("%s request retrying in %.1f seconds (attempt %s).", self.source_name, delay, attempt)
        time.sleep(delay)


class CanvasClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.http = HttpClient(
            base_url=config.canvas_base_url,
            source_name="Canvas",
            timeout_seconds=config.canvas_request_timeout_seconds,
            max_retries=config.max_retries,
            max_backoff_seconds=config.max_backoff_seconds,
            default_headers={"Authorization": f"Bearer {config.canvas_api_token}"},
        )
        self._course_cache: dict[tuple[str, bool], CanvasCourse] = {}
        self._course_cache_lock = threading.Lock()
        self._zoom_tool_id_cache: dict[int, int | None] = {}
        self._zoom_tool_id_cache_lock = threading.Lock()
        self._role_cache: RoleSpec | None = None
        self._role_lock = threading.Lock()

    def get_course(self, course_identifier: str, include_lti_context_id: bool, force_refresh: bool = False) -> CanvasCourse:
        cache_key = (course_identifier, include_lti_context_id)
        if not force_refresh:
            with self._course_cache_lock:
                cached = self._course_cache.get(cache_key)
            if cached is not None:
                return cached

        params: list[tuple[str, Any]] = []
        if include_lti_context_id:
            params.append(("include[]", "lti_context_id"))
        try:
            response = self.http.request("GET", f"/api/v1/courses/{course_identifier}", params=params)
        except ApiError as exc:
            if exc.status_code == 404:
                raise AppError("CNV404", f"Canvas course '{course_identifier}' was not found.", cause=exc) from exc
            raise

        payload = _require_dict(_safe_json(response), "CNV405", "Canvas course response was not an object.")
        course = CanvasCourse(
            source_identifier=course_identifier,
            canvas_id=int(payload["id"]),
            name=str(payload.get("name", "")),
            course_code=str(payload.get("course_code") or payload.get("name") or payload["id"]),
            lti_context_id=payload.get("lti_context_id"),
        )
        with self._course_cache_lock:
            self._course_cache[cache_key] = course
        return course

    def find_zoom_lti_tool_id(self, course_id: int) -> int | None:
        with self._zoom_tool_id_cache_lock:
            if course_id in self._zoom_tool_id_cache:
                return self._zoom_tool_id_cache[course_id]

        tools = self.http.list_paginated(
            f"/api/v1/courses/{course_id}/external_tools",
            params={"per_page": self.config.canvas_per_page},
        )
        best_match: tuple[int, int] | None = None
        for tool in tools:
            tool_id = tool.get("id")
            if tool_id is None:
                continue
            try:
                parsed_tool_id = int(tool_id)
            except (TypeError, ValueError):
                continue

            name = str(tool.get("name") or "")
            url = str(tool.get("url") or "")
            domain = str(tool.get("domain") or "")
            lti_version = str(tool.get("lti_version") or "")
            normalized_haystack = f"{name} {url} {domain}".lower()
            if "zoom" not in normalized_haystack:
                continue

            score = 1
            lowered_domain = domain.lower()
            lowered_url = url.lower()
            if "applications.zoom.us" in lowered_domain or "applications.zoom.us" in lowered_url:
                score += 2
            if lti_version == "1.3":
                score += 1

            if best_match is None or score > best_match[0]:
                best_match = (score, parsed_tool_id)

        resolved = best_match[1] if best_match else None
        with self._zoom_tool_id_cache_lock:
            self._zoom_tool_id_cache[course_id] = resolved
        return resolved

    def trigger_lti_context_id_generation(self, course_id: int, tool_id: int) -> None:
        try:
            self.http.request(
                "GET",
                f"/api/v1/courses/{course_id}/external_tools/sessionless_launch",
                params={"id": tool_id},
                expected_status={200},
            )
        except ApiError as exc:
            LOGGER.warning(
                "Sessionless launch failed for course %s, tool %s. Will try fallback paths. Error: %s",
                course_id,
                tool_id,
                exc,
            )

    def resolve_coordinator_role(self) -> RoleSpec:
        with self._role_lock:
            if self._role_cache is not None:
                return self._role_cache

            if self.config.canvas_coordinator_role_id:
                try:
                    response = self.http.request(
                        "GET",
                        f"/api/v1/accounts/{self.config.canvas_role_account_id}/roles/{self.config.canvas_coordinator_role_id}",
                    )
                except ApiError as exc:
                    raise AppError(
                        "ROL001",
                        f"Canvas role ID '{self.config.canvas_coordinator_role_id}' could not be loaded.",
                        cause=exc,
                    ) from exc
                payload = _require_dict(_safe_json(response), "ROL002", "Canvas role response was not an object.")
                role = RoleSpec(
                    role_id=str(payload.get("id", self.config.canvas_coordinator_role_id)),
                    base_role_type=str(payload.get("base_role_type") or self.config.canvas_coordinator_base_role_type),
                    label=str(payload.get("label") or payload.get("role") or self.config.canvas_coordinator_role_id),
                )
            else:
                roles = self.http.list_paginated(
                    f"/api/v1/accounts/{self.config.canvas_role_account_id}/roles",
                    params={"show_inherited": "true", "state[]": "active"},
                )
                target = (self.config.canvas_coordinator_role_label or "").strip().lower()
                matched = next(
                    (
                        role
                        for role in roles
                        if str(role.get("label", "")).lower() == target or str(role.get("role", "")).lower() == target
                    ),
                    None,
                )
                if matched is None:
                    raise AppError(
                        "ROL404",
                        f"Canvas role label '{self.config.canvas_coordinator_role_label}' was not found in account '{self.config.canvas_role_account_id}'.",
                    )
                role = RoleSpec(
                    role_id=str(matched["id"]),
                    base_role_type=str(matched.get("base_role_type") or self.config.canvas_coordinator_base_role_type),
                    label=str(matched.get("label") or matched.get("role") or target),
                )

            self._role_cache = role
            return role

    def start_content_copy(self, destination_course_id: int, source_course_id: int) -> dict[str, Any]:
        try:
            response = self.http.request(
                "POST",
                f"/api/v1/courses/{destination_course_id}/content_migrations",
                data={
                    "migration_type": "course_copy_importer",
                    "settings[source_course_id]": str(source_course_id),
                },
            )
        except ApiError as exc:
            raise AppError(
                "CNV406",
                f"Could not start content copy from {source_course_id} to {destination_course_id}: {exc.message}",
                cause=exc,
            ) from exc
        return _require_dict(_safe_json(response), "CNV407", "Canvas content migration response was not an object.")

    def wait_for_content_copy(self, course_id: int, migration_id: int, progress_url: str) -> list[dict[str, Any]]:
        deadline = time.monotonic() + (self.config.canvas_content_copy_timeout_minutes * 60)
        while time.monotonic() < deadline:
            progress_response = self.http.request("GET", progress_url)
            progress_payload = _require_dict(_safe_json(progress_response), "CNV408", "Canvas progress response was not an object.")
            state = str(progress_payload.get("workflow_state") or "").lower()
            if state == "completed":
                return self.list_migration_issues(course_id, migration_id)
            if state == "failed":
                issues = self.list_migration_issues(course_id, migration_id)
                summary = summarize_migration_issues(issues)
                message = "Canvas content migration failed."
                if summary:
                    message = f"{message} {summary}"
                raise AppError("CNV409", message, details={"issues": issues})
            time.sleep(self.config.canvas_poll_interval_seconds)
        raise AppError("CNV410", f"Canvas content migration timed out after {self.config.canvas_content_copy_timeout_minutes} minutes.")

    def list_migration_issues(self, course_id: int, migration_id: int) -> list[dict[str, Any]]:
        return self.http.list_paginated(f"/api/v1/courses/{course_id}/content_migrations/{migration_id}/migration_issues")

    def enroll_user(self, course_id: int, user_id: str, role: RoleSpec) -> None:
        try:
            self.http.request(
                "POST",
                f"/api/v1/courses/{course_id}/enrollments",
                data={
                    "enrollment[user_id]": user_id,
                    "enrollment[type]": role.base_role_type,
                    "enrollment[role_id]": role.role_id,
                    "enrollment[enrollment_state]": "active",
                    "enrollment[notify]": str(self.config.canvas_notify_coordinators).lower(),
                },
            )
        except ApiError as exc:
            message = (exc.message or "").lower()
            if exc.status_code in {400, 409, 422} and "already" in message and "enroll" in message:
                LOGGER.info("Canvas user %s is already enrolled in course %s.", user_id, course_id)
                return
            raise AppError(
                "CNV411",
                f"Could not enroll coordinator '{user_id}' into course {course_id}: {exc.message}",
                cause=exc,
            ) from exc

    def get_front_page(self, course_id: int) -> dict[str, Any]:
        try:
            response = self.http.request("GET", f"/api/v1/courses/{course_id}/front_page")
        except ApiError as exc:
            raise AppError("PGE003", f"Could not load the front page for Canvas course {course_id}.", cause=exc) from exc
        return _require_dict(_safe_json(response), "PGE004", "Canvas front page response was not an object.")

    def update_front_page(self, course_id: int, updated_body: str) -> None:
        try:
            self.http.request(
                "PUT",
                f"/api/v1/courses/{course_id}/front_page",
                data={
                    "wiki_page[body]": updated_body,
                    "wiki_page[notify_of_update]": str(self.config.canvas_notify_homepage_update).lower(),
                },
            )
        except ApiError as exc:
            raise AppError("PGE005", f"Could not update the front page for Canvas course {course_id}.", cause=exc) from exc


class ZoomLTIClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.http = HttpClient(
            base_url=config.zoom_lti_base_url,
            source_name="Zoom LTI Pro",
            timeout_seconds=config.canvas_request_timeout_seconds,
            max_retries=config.max_retries,
            max_backoff_seconds=config.max_backoff_seconds,
        )

    def create_and_associate(
        self,
        *,
        host_user_id: str,
        context_id: str,
        domain: str,
        course_id: int,
        meeting_info: dict[str, Any],
    ) -> str:
        timestamp = str(int(time.time() * 1000))
        signature_parts = build_lti_signature_parts(
            self.config.zoom_lti_key,
            timestamp,
            host_user_id,
            self.config.zoom_lti_signature_param_order,
        )
        signature_base_string = build_lti_signature_base_string(signature_parts)
        signature = build_lti_signature(
            self.config.zoom_lti_secret,
            signature_parts,
            use_urlsafe_base64=self.config.zoom_lti_signature_use_urlsafe_base64,
            strip_padding=self.config.zoom_lti_signature_strip_padding,
        )
        user_id_in_signature = next((value for key, value in signature_parts if key == "userId"), "")
        LOGGER.debug(
            "Zoom LTI signing inputs: base_url=%s key=%s timestamp=%s userId=%s userId_format=%s base_string=%s",
            self.config.zoom_lti_base_url,
            self.config.zoom_lti_key,
            timestamp,
            host_user_id,
            "email" if _looks_like_email(host_user_id) else "zoom_user_id",
            signature_base_string,
        )
        if self.config.zoom_lti_debug_signature_base_string:
            print(f"Zoom LTI signature base string: {signature_base_string}", file=sys.stderr)
        LOGGER.debug(
            "Zoom LTI signature generated (suppressed): length=%s urlsafe_base64=%s padding_stripped=%s param_order=%s userId_matches_body=%s",
            len(signature),
            self.config.zoom_lti_signature_use_urlsafe_base64,
            self.config.zoom_lti_signature_strip_padding,
            self.config.zoom_lti_signature_param_order,
            user_id_in_signature == host_user_id,
        )
        response = self.http.request(
            "POST",
            "/meeting/createAndAssociate",
            params={"key": self.config.zoom_lti_key, "timestamp": timestamp, "userId": host_user_id},
            headers={"X-Lti-Signature": signature},
            json_body={
                "userId": host_user_id,
                "contextId": context_id,
                "domain": domain,
                "courseId": str(course_id),
                "meetingInfo": meeting_info,
            },
        )
        payload = _require_dict(_safe_json(response), "ZLT001", "Zoom LTI response was not an object.")
        if payload.get("status") is True:
            result = _require_dict(payload.get("result", {}), "ZLT002", "Zoom LTI result was missing.")
            meeting_id = str(result.get("id", "")).strip()
            if not meeting_id:
                raise AppError("ZLT003", "Zoom LTI reported success but did not return a meeting ID.")
            return meeting_id

        result = payload.get("result") or {}
        meeting_id = str(result.get("id", "")).strip()
        error_code = str(payload.get("errorCode", ""))
        error_message = str(payload.get("errorMessage", "Unknown LTI error"))
        if "2203" in error_message and "verify lti signature failed" in error_message.lower():
            error_message = (
                f"{error_message} Verify ZOOM_LTI_KEY and ZOOM_LTI_SECRET are from Zoom LTI Pro "
                "(not Zoom OAuth credentials), verify ZOOM_LTI_HOST_USER_ID format, and ensure the system clock is accurate."
            )
            if LOGGER.isEnabledFor(logging.DEBUG):
                digest_hex = build_lti_signature_digest(self.config.zoom_lti_secret, signature_base_string).hex()
                LOGGER.debug(
                    "Zoom LTI 2203 debug details: base_url=%s key=%s timestamp=%s userId=%s base_string=%s urlsafe_base64=%s padding_stripped=%s param_order=%s digest_sha1=%s",
                    self.config.zoom_lti_base_url,
                    self.config.zoom_lti_key,
                    timestamp,
                    host_user_id,
                    signature_base_string,
                    self.config.zoom_lti_signature_use_urlsafe_base64,
                    self.config.zoom_lti_signature_strip_padding,
                    self.config.zoom_lti_signature_param_order,
                    digest_hex,
                )
        raise AppError(
            f"ZLT{error_code or '000'}",
            f"Zoom LTI createAndAssociate failed: {error_message}",
            details={"meeting_id": meeting_id},
        )


class ZoomClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.http = HttpClient(
            base_url=config.zoom_api_base_url,
            source_name="Zoom",
            timeout_seconds=config.canvas_request_timeout_seconds,
            max_retries=config.max_retries,
            max_backoff_seconds=config.max_backoff_seconds,
        )
        self._token_lock = threading.Lock()
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0
        self._user_id_cache_lock = threading.Lock()
        self._user_id_cache: dict[str, str] = {}

    def get_meeting(self, meeting_id: str) -> dict[str, Any]:
        token = self._access_token()
        try:
            response = self.http.request(
                "GET",
                f"/meetings/{meeting_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        except ApiError as exc:
            raise AppError("ZOM404", f"Could not retrieve Zoom meeting {meeting_id}.", cause=exc) from exc
        return _require_dict(_safe_json(response), "ZOM405", "Zoom meeting response was not an object.")

    def resolve_lti_host_user_id(self, host_value: str) -> str:
        candidate = host_value.strip()
        if not candidate:
            raise AppError("ZOM408", "Zoom LTI host user value is empty.")

        if "@" not in candidate:
            LOGGER.info("Zoom LTI host source: explicit Zoom user ID.")
            return candidate

        with self._user_id_cache_lock:
            cached = self._user_id_cache.get(candidate.lower())
        if cached:
            LOGGER.info("Zoom LTI host source: resolved from email cache (%s).", candidate)
            return cached

        token = self._access_token()
        try:
            response = self.http.request(
                "GET",
                f"/users/{quote(candidate, safe='')}",
                headers={"Authorization": f"Bearer {token}"},
            )
        except ApiError as exc:
            if exc.status_code == 404:
                raise AppError("ZOM409", f"Zoom host email '{candidate}' could not be resolved to a Zoom user ID.", cause=exc) from exc
            raise AppError("ZOM410", f"Could not look up Zoom host email '{candidate}'.", cause=exc) from exc

        payload = _require_dict(_safe_json(response), "ZOM411", "Zoom user lookup response was not an object.")
        user_id = str(payload.get("id", "")).strip()
        if not user_id:
            raise AppError("ZOM412", f"Zoom user lookup for '{candidate}' did not return an ID.")

        with self._user_id_cache_lock:
            self._user_id_cache[candidate.lower()] = user_id
        LOGGER.info("Zoom LTI host source: resolved from email (%s).", candidate)
        return user_id

    def _access_token(self) -> str:
        with self._token_lock:
            if self._cached_token and time.time() < self._token_expires_at - 60:
                return self._cached_token

            try:
                response = self.http.request(
                    "POST",
                    f"{self.config.zoom_oauth_base_url}/oauth/token",
                    params={"grant_type": "account_credentials", "account_id": self.config.zoom_oauth_account_id},
                    auth=(self.config.zoom_oauth_client_id, self.config.zoom_oauth_client_secret),
                )
            except ApiError as exc:
                raise AppError("ZOM401", "Could not obtain a Zoom OAuth access token.", cause=exc) from exc

            payload = _require_dict(_safe_json(response), "ZOM402", "Zoom OAuth response was not an object.")
            access_token = str(payload.get("access_token", "")).strip()
            expires_in = int(payload.get("expires_in", 3600))
            if not access_token:
                raise AppError("ZOM403", "Zoom OAuth did not return an access token.")

            self._cached_token = access_token
            self._token_expires_at = time.time() + expires_in
            return access_token


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _require_dict(payload: Any, code: str, message: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AppError(code, message)
    return payload


def _extract_error_message(response: requests.Response) -> str:
    payload = _safe_json(response)
    if isinstance(payload, dict):
        if payload.get("errorMessage"):
            return str(payload["errorMessage"])
        if payload.get("message"):
            return str(payload["message"])
        if payload.get("errors"):
            return _flatten_errors(payload["errors"])
        return json.dumps(payload)
    if isinstance(payload, list):
        return json.dumps(payload)
    return str(payload).strip() or response.reason


def _flatten_errors(errors: Any) -> str:
    if isinstance(errors, str):
        return errors
    if isinstance(errors, list):
        return " | ".join(_flatten_errors(item) for item in errors)
    if isinstance(errors, dict):
        parts: list[str] = []
        for key, value in errors.items():
            parts.append(f"{key}: {_flatten_errors(value)}")
        return " | ".join(parts)
    return str(errors)


def _parse_link_header(link_header: str) -> dict[str, str]:
    links: dict[str, str] = {}
    for part in link_header.split(","):
        part = part.strip()
        if ";" not in part:
            continue
        url_part, rel_part = part.split(";", 1)
        url = url_part.strip().strip("<>")
        rel_tokens = rel_part.strip().split("=")
        if len(rel_tokens) != 2:
            continue
        rel = rel_tokens[1].strip().strip('"')
        links[rel] = url
    return links


def _looks_like_email(value: str) -> bool:
    return "@" in value and "." in value.split("@")[-1]
