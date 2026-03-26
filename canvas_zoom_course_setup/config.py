from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import AppError


DEFAULT_MEETING_SETTINGS = {
    "host_video": True,
    "participant_video": True,
    "join_before_host": False,
    "mute_upon_entry": True,
    "waiting_room": True,
}

ALLOWED_BASE_ROLE_TYPES = {
    "TeacherEnrollment",
    "StudentEnrollment",
    "TaEnrollment",
    "ObserverEnrollment",
    "DesignerEnrollment",
}


@dataclass(frozen=True)
class AppConfig:
    csv_file_path: str
    lti_launch_payloads_directory: str | None
    canvas_base_url: str
    canvas_domain: str
    canvas_api_token: str
    canvas_role_account_id: str
    canvas_coordinator_role_id: str | None
    canvas_coordinator_role_label: str | None
    canvas_coordinator_base_role_type: str
    canvas_homepage_link_placeholder: str | None
    canvas_homepage_passcode_placeholder: str | None
    canvas_request_timeout_seconds: float
    canvas_content_copy_timeout_minutes: float
    canvas_poll_interval_seconds: float
    canvas_notify_coordinators: bool
    canvas_notify_homepage_update: bool
    zoom_lti_base_url: str
    zoom_lti_key: str
    zoom_lti_secret: str
    zoom_lti_host_user_id: str
    zoom_lti_debug_signature_base_string: bool
    zoom_oauth_base_url: str
    zoom_api_base_url: str
    zoom_oauth_client_id: str
    zoom_oauth_client_secret: str
    zoom_oauth_account_id: str
    default_meeting_start_time: time
    default_meeting_duration_minutes: int
    default_meeting_timezone: str
    meeting_topic_template: str
    zoom_meeting_settings: dict[str, Any]
    max_workers: int
    max_retries: int
    max_backoff_seconds: float
    canvas_per_page: int
    report_directory: Path


def load_config(env_file: Path | None) -> AppConfig:
    if env_file is not None:
        if env_file.exists():
            _load_env_file(env_file)
        elif env_file.name != ".env":
            raise AppError("CFG001", f"Requested env file does not exist: {env_file}")
    else:
        default_env = Path(".env")
        if default_env.exists():
            _load_env_file(default_env)

    canvas_base_url = _normalize_url(_require("CANVAS_BASE_URL"))
    canvas_domain = _extract_origin(canvas_base_url)

    role_id = _blank_to_none(_optional("CANVAS_COORDINATOR_ROLE_ID"))
    role_label = _blank_to_none(_optional("CANVAS_COORDINATOR_ROLE_LABEL", "Coordinator"))
    base_role_type = _optional("CANVAS_COORDINATOR_BASE_ROLE_TYPE", "TeacherEnrollment")
    if base_role_type not in ALLOWED_BASE_ROLE_TYPES:
        raise AppError(
            "CFG002",
            "CANVAS_COORDINATOR_BASE_ROLE_TYPE must be one of TeacherEnrollment, StudentEnrollment, TaEnrollment, ObserverEnrollment, DesignerEnrollment.",
        )
    if role_id is None and role_label is None:
        raise AppError("CFG003", "Set CANVAS_COORDINATOR_ROLE_ID or CANVAS_COORDINATOR_ROLE_LABEL.")

    meeting_settings = DEFAULT_MEETING_SETTINGS.copy()
    settings_json = _blank_to_none(_optional("ZOOM_MEETING_SETTINGS_JSON"))
    if settings_json:
        try:
            parsed = json.loads(settings_json)
        except json.JSONDecodeError as exc:
            raise AppError("CFG004", "ZOOM_MEETING_SETTINGS_JSON must be valid JSON.", cause=exc) from exc
        if not isinstance(parsed, dict):
            raise AppError("CFG005", "ZOOM_MEETING_SETTINGS_JSON must be a JSON object.")
        meeting_settings.update(parsed)

    return AppConfig(
        csv_file_path=_optional("CSV_FILE_PATH", "canvas_zoom_import_courses.csv"),
        lti_launch_payloads_directory=_blank_to_none(_optional("LTI_LAUNCH_PAYLOADS_DIRECTORY")),
        canvas_base_url=canvas_base_url,
        canvas_domain=canvas_domain,
        canvas_api_token=_require("CANVAS_API_TOKEN"),
        canvas_role_account_id=_optional("CANVAS_ROLE_ACCOUNT_ID", "self"),
        canvas_coordinator_role_id=role_id,
        canvas_coordinator_role_label=role_label,
        canvas_coordinator_base_role_type=base_role_type,
        canvas_homepage_link_placeholder=_blank_to_none(
            _optional("CANVAS_HOMEPAGE_LINK_PLACEHOLDER", "{{ZOOM_MEETING_LINK}}")
        ),
        canvas_homepage_passcode_placeholder=_blank_to_none(
            _optional("CANVAS_HOMEPAGE_PASSCODE_PLACEHOLDER", "{{ZOOM_MEETING_PASSCODE}}")
        ),
        canvas_request_timeout_seconds=_as_float("CANVAS_REQUEST_TIMEOUT_SECONDS", 45.0, minimum=1.0),
        canvas_content_copy_timeout_minutes=_as_float("CANVAS_CONTENT_COPY_TIMEOUT_MINUTES", 30.0, minimum=1.0),
        canvas_poll_interval_seconds=_as_float("CANVAS_POLL_INTERVAL_SECONDS", 10.0, minimum=1.0),
        canvas_notify_coordinators=_as_bool("CANVAS_NOTIFY_COORDINATORS", False),
        canvas_notify_homepage_update=_as_bool("CANVAS_NOTIFY_HOMEPAGE_UPDATE", False),
        zoom_lti_base_url=_normalize_url(_optional("ZOOM_LTI_BASE_URL", "https://applications.zoom.us/api/v1/lti/rich")),
        zoom_lti_key=_require("ZOOM_LTI_KEY"),
        zoom_lti_secret=_require("ZOOM_LTI_SECRET"),
        zoom_lti_host_user_id=_require("ZOOM_LTI_HOST_USER_ID"),
        zoom_lti_debug_signature_base_string=_as_bool("ZOOM_LTI_DEBUG_SIGNATURE_BASE_STRING", False),
        zoom_oauth_base_url=_normalize_url(_optional("ZOOM_OAUTH_BASE_URL", "https://zoom.us")),
        zoom_api_base_url=_normalize_url(_optional("ZOOM_API_BASE_URL", "https://api.zoom.us/v2")),
        zoom_oauth_client_id=_require("ZOOM_OAUTH_CLIENT_ID"),
        zoom_oauth_client_secret=_require("ZOOM_OAUTH_CLIENT_SECRET"),
        zoom_oauth_account_id=_require("ZOOM_OAUTH_ACCOUNT_ID"),
        default_meeting_start_time=_parse_time(_optional("DEFAULT_MEETING_START_TIME", "18:00")),
        default_meeting_duration_minutes=_as_int("DEFAULT_MEETING_DURATION_MINUTES", 120, minimum=1),
        default_meeting_timezone=_optional("DEFAULT_MEETING_TIMEZONE", "America/Denver"),
        meeting_topic_template=_optional("MEETING_TOPIC_TEMPLATE", "{course_code} Live Class Session"),
        zoom_meeting_settings=meeting_settings,
        max_workers=_as_int("MAX_WORKERS", 4, minimum=1),
        max_retries=_as_int("MAX_RETRIES", 5, minimum=1),
        max_backoff_seconds=_as_float("MAX_BACKOFF_SECONDS", 30.0, minimum=0.1),
        canvas_per_page=_as_int("CANVAS_PER_PAGE", 100, minimum=1),
        report_directory=Path(_optional("REPORT_DIRECTORY", "reports")),
    )


def _load_env_file(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise AppError("CFG006", f"Missing required configuration value: {name}")
    return value


def _optional(name: str, default: str | None = None) -> str:
    value = os.getenv(name)
    if value is None:
        return "" if default is None else default
    return value.strip()


def _as_bool(name: str, default: bool) -> bool:
    value = _optional(name, str(default).lower()).lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise AppError("CFG007", f"{name} must be a boolean value.")


def _as_int(name: str, default: int, minimum: int | None = None) -> int:
    value = _optional(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise AppError("CFG008", f"{name} must be an integer.") from exc
    if minimum is not None and parsed < minimum:
        raise AppError("CFG009", f"{name} must be at least {minimum}.")
    return parsed


def _as_float(name: str, default: float, minimum: float | None = None) -> float:
    value = _optional(name, str(default))
    try:
        parsed = float(value)
    except ValueError as exc:
        raise AppError("CFG010", f"{name} must be a number.") from exc
    if minimum is not None and parsed < minimum:
        raise AppError("CFG011", f"{name} must be at least {minimum}.")
    return parsed


def _parse_time(value: str) -> time:
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise AppError("CFG012", "DEFAULT_MEETING_START_TIME must look like 18:00 or 6:00 PM.")


def _normalize_url(value: str) -> str:
    return value.rstrip("/")


def _extract_origin(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        raise AppError("CFG013", f"Invalid URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}"


def _blank_to_none(value: str) -> str | None:
    return value if value else None
