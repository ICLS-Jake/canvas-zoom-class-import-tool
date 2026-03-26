from __future__ import annotations

import copy
import hashlib
import hmac
from base64 import urlsafe_b64encode
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
) -> str:
    updated = body
    if link_placeholder:
        if link_placeholder not in updated:
            raise AppError("PGE001", f"Homepage link placeholder '{link_placeholder}' was not found.")
        updated = updated.replace(link_placeholder, meeting_link)
    if passcode_placeholder:
        if passcode_placeholder not in updated:
            raise AppError("PGE002", f"Homepage passcode placeholder '{passcode_placeholder}' was not found.")
        updated = updated.replace(passcode_placeholder, passcode)
    return updated


def build_lti_signature(secret: str, parts: list[tuple[str, str]]) -> str:
    base_string = build_lti_signature_base_string(parts)
    digest = hmac.new(secret.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1).digest()
    return urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def build_lti_signature_base_string(parts: list[tuple[str, str]]) -> str:
    return "&".join(f"{key}={value}" for key, value in parts)


def summarize_migration_issues(issues: list[dict]) -> str:
    summaries: list[str] = []
    for issue in issues:
        state = issue.get("workflow_state") or "unknown"
        issue_type = issue.get("issue_type") or "issue"
        description = (issue.get("description") or "").strip()
        if description:
            summaries.append(f"{issue_type}/{state}: {description}")
    return " | ".join(summaries)


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
