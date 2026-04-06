from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from .errors import AppError
from .models import CourseShellRow


FIELD_ALIASES = {
    "live_course_id": {"live course id", "live_course_id", "live course", "course id", "live canvas course id"},
    "master_course_id": {"master course id", "master_course_id", "master course", "blueprint course id"},
    "course_start_date": {"course start date", "course_start_date", "start date", "start_date"},
    "course_end_date": {"course end date", "course_end_date", "end date", "end_date"},
    "course_days": {"course days", "course_days", "days", "meeting days"},
    "coordinator_user_ids": {
        "coordinator user ids",
        "coordinator_user_ids",
        "coordinator ids",
        "coordinators",
    },
    "meeting_start_time": {"meeting start time", "meeting_start_time", "start time"},
    "meeting_duration_minutes": {"meeting duration minutes", "meeting_duration_minutes", "duration minutes"},
    "meeting_timezone": {"meeting timezone", "meeting_timezone", "timezone"},
    "lti_context_id": {"lti context id", "lti_context_id", "context id"},
    "meeting_topic": {"meeting topic", "meeting_topic", "topic"},
    "zoom_host_user_id": {"zoom host user id", "zoom_host_user_id", "host user id"},
    "schedule_url": {"schedule", "schedule url", "schedule_url", "course schedule url", "course schedule link"},
    "teacher_name": {"teacher name", "teacher_name"},
    "teacher_shortname": {"teacher shortname", "teacher_shortname", "teacher short name"},
}

WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "weds": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


def load_course_rows(csv_path: Path) -> list[CourseShellRow]:
    if not csv_path.exists():
        raise AppError("CSV001", f"CSV file does not exist: {csv_path}")

    rows: list[CourseShellRow] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise AppError("CSV002", "CSV file is empty or missing a header row.")

        for index, raw_row in enumerate(reader, start=2):
            normalized = {_normalize_header(key): (value or "").strip() for key, value in raw_row.items() if key}
            if not any(normalized.values()):
                continue

            live_course_id = _required_field(normalized, "live_course_id", index)
            master_course_id = _required_field(normalized, "master_course_id", index)
            start_date = _parse_date(_required_field(normalized, "course_start_date", index), index, "course start date")
            end_date = _parse_date(_required_field(normalized, "course_end_date", index), index, "course end date")
            if end_date < start_date:
                raise AppError("CSV003", f"Row {index}: course end date cannot be before start date.")

            course_days = tuple(sorted(_parse_course_days(_required_field(normalized, "course_days", index), index)))
            coordinator_ids = tuple(_parse_list(_required_field(normalized, "coordinator_user_ids", index)))
            if not coordinator_ids:
                raise AppError("CSV004", f"Row {index}: coordinator user IDs cannot be empty.")

            rows.append(
                CourseShellRow(
                    row_number=index,
                    live_course_id=live_course_id,
                    master_course_id=master_course_id,
                    course_start_date=start_date,
                    course_end_date=end_date,
                    course_days=course_days,
                    coordinator_user_ids=coordinator_ids,
                    meeting_start_time=_parse_optional_time(_field(normalized, "meeting_start_time"), index),
                    meeting_duration_minutes=_parse_optional_int(
                        _field(normalized, "meeting_duration_minutes"), index, "meeting duration minutes"
                    ),
                    meeting_timezone=_blank_to_none(_field(normalized, "meeting_timezone")),
                    lti_context_id=_blank_to_none(_field(normalized, "lti_context_id")),
                    meeting_topic=_blank_to_none(_field(normalized, "meeting_topic")),
                    zoom_host_user_id=_blank_to_none(_field(normalized, "zoom_host_user_id")),
                    schedule_url=_blank_to_none(_field(normalized, "schedule_url")),
                    teacher_name=_blank_to_none(_field(normalized, "teacher_name")),
                    teacher_shortname=_blank_to_none(_field(normalized, "teacher_shortname")),
                    raw_values=normalized,
                )
            )

    if not rows:
        raise AppError("CSV005", "No course rows were found in the CSV.")
    return rows


def _field(row: dict[str, str], key: str) -> str:
    for alias in FIELD_ALIASES[key]:
        value = row.get(alias)
        if value is not None:
            return value
    return ""


def _required_field(row: dict[str, str], key: str, row_number: int) -> str:
    value = _field(row, key)
    if not value:
        raise AppError("CSV006", f"Row {row_number}: missing required value for '{key}'.")
    return value


def _parse_date(value: str, row_number: int, label: str):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise AppError("CSV007", f"Row {row_number}: invalid {label} '{value}'. Use YYYY-MM-DD when possible.")


def _parse_optional_time(value: str, row_number: int):
    value = value.strip()
    if not value:
        return None
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise AppError("CSV008", f"Row {row_number}: invalid meeting start time '{value}'.")


def _parse_optional_int(value: str, row_number: int, label: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise AppError("CSV009", f"Row {row_number}: invalid {label} '{value}'.") from exc
    if parsed <= 0:
        raise AppError("CSV010", f"Row {row_number}: {label} must be greater than zero.")
    return parsed


def _parse_course_days(value: str, row_number: int) -> list[int]:
    tokens = [token for token in re.split(r"[^A-Za-z]+", value.lower()) if token]
    if not tokens:
        raise AppError("CSV011", f"Row {row_number}: course days cannot be empty.")
    day_values: list[int] = []
    for token in tokens:
        if token not in WEEKDAY_ALIASES:
            raise AppError("CSV012", f"Row {row_number}: unsupported day token '{token}'.")
        day_value = WEEKDAY_ALIASES[token]
        if day_value not in day_values:
            day_values.append(day_value)
    return day_values


def _parse_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"\s*[;,|]\s*", value.strip()) if item.strip()]


def _normalize_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", header.lower()).strip()


def _blank_to_none(value: str) -> str | None:
    return value if value else None
