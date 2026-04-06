from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any


@dataclass(frozen=True)
class RoleSpec:
    role_id: str
    base_role_type: str
    label: str


@dataclass(frozen=True)
class CanvasCourse:
    source_identifier: str
    canvas_id: int
    name: str
    course_code: str
    lti_context_id: str | None = None


@dataclass(frozen=True)
class CourseShellRow:
    row_number: int
    live_course_id: str
    master_course_id: str
    course_start_date: date
    course_end_date: date
    course_days: tuple[int, ...]
    coordinator_user_ids: tuple[str, ...]
    meeting_start_time: time | None = None
    meeting_duration_minutes: int | None = None
    meeting_timezone: str | None = None
    lti_context_id: str | None = None
    meeting_topic: str | None = None
    zoom_host_user_id: str | None = None
    schedule_url: str | None = None
    teacher_name: str | None = None
    teacher_shortname: str | None = None
    raw_values: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MeetingPlan:
    topic: str
    timezone: str
    duration_minutes: int
    first_occurrence_at: datetime
    weekly_days: str
    payload: dict[str, Any]


@dataclass
class CourseResult:
    row_number: int
    live_course_identifier: str
    master_course_identifier: str
    live_canvas_course_id: str = ""
    master_canvas_course_id: str = ""
    course_name: str = ""
    status: str = "failed"
    meeting_id: str = ""
    join_url: str = ""
    passcode: str = ""
    warnings: str = ""
    error_code: str = ""
    error_message: str = ""
    elapsed_seconds: float = 0.0

    def to_record(self) -> dict[str, str]:
        return {
            "row_number": str(self.row_number),
            "live_course_identifier": self.live_course_identifier,
            "master_course_identifier": self.master_course_identifier,
            "live_canvas_course_id": self.live_canvas_course_id,
            "master_canvas_course_id": self.master_canvas_course_id,
            "course_name": self.course_name,
            "status": self.status,
            "meeting_id": self.meeting_id,
            "join_url": self.join_url,
            "passcode": self.passcode,
            "warnings": self.warnings,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "elapsed_seconds": f"{self.elapsed_seconds:.2f}",
        }
