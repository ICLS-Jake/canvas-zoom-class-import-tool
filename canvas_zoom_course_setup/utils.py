from __future__ import annotations

import copy
import json
import hashlib
import hmac
import re
from html import escape
from base64 import b64encode, urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import AppConfig
from .errors import AppError
from .models import CanvasCourse, CourseShellRow, MeetingPlan

ZOOM_WEEKDAY_MAP = {
    0: "2",  # Monday
    1: "3",
    2: "4",
    3: "5",
    4: "6",
    5: "7",
    6: "1",  # Sunday
}

WEEKDAY_PLURAL_NAMES = {
    0: "Mondays",
    1: "Tuesdays",
    2: "Wednesdays",
    3: "Thursdays",
    4: "Fridays",
    5: "Saturdays",
    6: "Sundays",
}


def build_meeting_plan(row: CourseShellRow, course: CanvasCourse, config: AppConfig) -> MeetingPlan:
    timezone_name = row.meeting_timezone or config.default_meeting_timezone
    meeting_zone = _get_timezone(timezone_name)
    start_time = row.meeting_start_time or config.default_meeting_start_time
    duration_minutes = row.meeting_duration_minutes or config.default_meeting_duration_minutes
    first_date = _find_first_occurrence(row.course_start_date, row.course_end_date, row.course_days)
    first_occurrence = datetime.combine(first_date, start_time, tzinfo=meeting_zone)
    recurrence_end = datetime.combine(row.course_end_date, time(23, 59, 59), tzinfo=meeting_zone)
    weekly_days = ",".join(ZOOM_WEEKDAY_MAP[day] for day in row.course_days)
    topic = row.meeting_topic or _render_topic(config.meeting_topic_template, row, course)

    payload = {
        "topic": topic,
        "type": 8,
        "start_time": first_occurrence.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration": duration_minutes,
        "timezone": timezone_name,
        "recurrence": {
            "type": 2,
            "repeat_interval": 1,
            "weekly_days": weekly_days,
            "end_date_time": recurrence_end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "settings": copy.deepcopy(config.zoom_meeting_settings),
    }

    return MeetingPlan(
        topic=topic,
        timezone=timezone_name,
        duration_minutes=duration_minutes,
        first_occurrence_at=first_occurrence,
        weekly_days=weekly_days,
        payload=payload,
    )


def replace_homepage_placeholders(
    body: str,
    meeting_link: str,
    passcode: str,
    link_placeholder: str | None,
    passcode_placeholder: str | None,
    schedule_url: str | None = None,
    schedule_placeholder: str | None = None,
    course_days_text: str | None = None,
    course_days_placeholder: str | None = None,
) -> str:
    updated = body
    if link_placeholder:
        if link_placeholder not in updated:
            raise AppError("PGE001", f"Homepage link placeholder '{link_placeholder}' was not found.")
        safe_link = escape(meeting_link, quote=True)
        hyperlink = f'<a href="{safe_link}">{safe_link}</a>'
        updated = updated.replace(link_placeholder, hyperlink)
    if passcode_placeholder:
        if passcode_placeholder not in updated:
            raise AppError("PGE002", f"Homepage passcode placeholder '{passcode_placeholder}' was not found.")
        updated = updated.replace(passcode_placeholder, passcode)
    if schedule_placeholder and schedule_url:
        if schedule_placeholder not in updated:
            raise AppError("PGE003", f"Homepage schedule placeholder '{schedule_placeholder}' was not found.")
        safe_url = escape(schedule_url, quote=True)
        updated = updated.replace(schedule_placeholder, f'<a href="{safe_url}">Course Schedule</a>')
    if course_days_placeholder and course_days_text:
        if course_days_placeholder not in updated:
            raise AppError("PGE004", f"Homepage course days placeholder '{course_days_placeholder}' was not found.")
        updated = updated.replace(course_days_placeholder, course_days_text)
    return updated


def replace_teacher_placeholders(
    body: str,
    teacher_name: str | None,
    teacher_shortname: str | None,
    teacher_email: str | None,
) -> str:
    _TEACHER_NAME_PH = "{{TEACHER_NAME}}"
    _TEACHER_NAME_SHORT_PH = "{{TEACHER_NAME_SHORT}}"
    _TEACHER_EMAIL_PH = "{{TEACHER_EMAIL}}"

    updated = body

    if teacher_name is None:
        print(f"  Skipping {_TEACHER_NAME_PH}: no value available.")
    elif _TEACHER_NAME_PH not in updated:
        print(f"  Skipping {_TEACHER_NAME_PH}: placeholder not found in page.")
    else:
        updated = updated.replace(_TEACHER_NAME_PH, teacher_name)
        print(f"  Replacing {_TEACHER_NAME_PH} with: {teacher_name}")

    if teacher_shortname is None:
        print(f"  Skipping {_TEACHER_NAME_SHORT_PH}: no value available.")
    elif _TEACHER_NAME_SHORT_PH not in updated:
        print(f"  Skipping {_TEACHER_NAME_SHORT_PH}: placeholder not found in page.")
    else:
        updated = updated.replace(_TEACHER_NAME_SHORT_PH, teacher_shortname)
        print(f"  Replacing {_TEACHER_NAME_SHORT_PH} with: {teacher_shortname}")

    if teacher_email is None:
        print(f"  Skipping {_TEACHER_EMAIL_PH}: no value available.")
    elif _TEACHER_EMAIL_PH not in updated:
        print(f"  Skipping {_TEACHER_EMAIL_PH}: placeholder not found in page.")
    else:
        safe_email = escape(teacher_email, quote=True)
        email_link = f'<a href="mailto:{safe_email}">{safe_email}</a>'
        updated = updated.replace(_TEACHER_EMAIL_PH, email_link)
        print(f"  Replacing {_TEACHER_EMAIL_PH} with: {teacher_email}")

    return updated


def strip_nbsp_after_instructor_heading(body: str) -> str:
    """Remove the trailing &nbsp;-only <span> Canvas inserts inside the Instructor and Contact Information heading."""
    return re.sub(
        r'(Instructor and Contact Information</span>)\s*<span[^>]*>(?:\s*&nbsp;\s*)+</span>',
        r'\1',
        body,
        flags=re.IGNORECASE,
    )


def format_course_days(weekdays: tuple[int, ...]) -> str:
    names = [WEEKDAY_PLURAL_NAMES[day] for day in weekdays]
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def build_lti_signature(
    secret: str,
    parts: list[tuple[str, str]],
    *,
    use_urlsafe_base64: bool = True,
    strip_padding: bool = True,
) -> str:
    base_string = build_lti_signature_base_string(parts)
    digest = build_lti_signature_digest(secret, base_string)
    signature = (
        urlsafe_b64encode(digest).decode("utf-8")
        if use_urlsafe_base64
        else b64encode(digest).decode("utf-8")
    )
    return signature.rstrip("=") if strip_padding else signature


def build_lti_signature_base_string(parts: list[tuple[str, str]]) -> str:
    return "&".join(f"{key}={value}" for key, value in parts)


def build_lti_signature_digest(secret: str, base_string: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1).digest()


def build_lti_signature_parts(key: str, timestamp: str, user_id: str, param_order: str) -> list[tuple[str, str]]:
    if param_order == "key,userId,timestamp":
        return [("key", key), ("userId", user_id), ("timestamp", timestamp)]
    return [("key", key), ("timestamp", timestamp), ("userId", user_id)]


def extract_lti_context_id_from_launch_payload_text(payload_text: str) -> str | None:
    raw = payload_text.strip()
    if not raw:
        return None

    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return _extract_lti_context_id_from_payload_object(payload)

    if "." in raw:
        jwt_payload = _decode_unverified_jwt_payload(raw)
        if jwt_payload is not None:
            return _extract_lti_context_id_from_payload_object(jwt_payload)
    return None


def summarize_migration_issues(issues: list[dict]) -> str:
    summaries: list[str] = []
    for issue in issues:
        state = issue.get("workflow_state") or "unknown"
        issue_type = issue.get("issue_type") or "issue"
        description = (issue.get("description") or "").strip()
        if description:
            summaries.append(f"{issue_type}/{state}: {description}")
    return " | ".join(summaries)


def _extract_lti_context_id_from_payload_object(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None

    context_claim = payload.get("https://purl.imsglobal.org/spec/lti/claim/context")
    if isinstance(context_claim, dict):
        context_id = str(context_claim.get("id", "")).strip()
        if context_id:
            return context_id

    context = payload.get("context")
    if isinstance(context, dict):
        context_id = str(context.get("id", "")).strip()
        if context_id:
            return context_id

    id_token = payload.get("id_token")
    if isinstance(id_token, str):
        nested_payload = _decode_unverified_jwt_payload(id_token)
        if nested_payload is not None:
            return _extract_lti_context_id_from_payload_object(nested_payload)

    return None


def _decode_unverified_jwt_payload(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload_segment = parts[1]
    padding = "=" * (-len(payload_segment) % 4)
    try:
        decoded = urlsafe_b64decode((payload_segment + padding).encode("utf-8")).decode("utf-8")
        loaded = json.loads(decoded)
    except (ValueError, UnicodeDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _render_topic(template: str, row: CourseShellRow, course: CanvasCourse) -> str:
    values = {
        "course_id": course.canvas_id,
        "course_name": course.name,
        "course_code": course.course_code or course.name,
        "live_course_id": row.live_course_id,
        "master_course_id": row.master_course_id,
        "start_date": row.course_start_date.isoformat(),
        "end_date": row.course_end_date.isoformat(),
    }

    class SafeFormatDict(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    rendered = template.format_map(SafeFormatDict(values)).strip()
    return rendered or course.name


def _find_first_occurrence(start_date: date, end_date: date, weekdays: tuple[int, ...]) -> date:
    for offset in range((end_date - start_date).days + 1):
        current = start_date + timedelta(days=offset)
        if current.weekday() in weekdays:
            return current
    raise AppError("SCH001", "No meeting occurrence falls between the provided start and end dates.")


def _get_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise AppError(
            "SCH002",
            f"Unknown IANA timezone '{timezone_name}'. If this is a Windows environment, install the 'tzdata' package.",
        ) from exc
