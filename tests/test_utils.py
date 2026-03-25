from __future__ import annotations

import unittest
from datetime import date, time
from pathlib import Path

from canvas_zoom_course_setup.config import AppConfig
from canvas_zoom_course_setup.models import CanvasCourse, CourseShellRow
from canvas_zoom_course_setup.utils import build_lti_signature, build_meeting_plan, replace_homepage_placeholders


class UtilsTests(unittest.TestCase):
    def test_replace_homepage_placeholders(self) -> None:
        body = "<p>Join here: {{ZOOM_MEETING_LINK}}</p><p>Passcode: {{ZOOM_MEETING_PASSCODE}}</p>"
        updated = replace_homepage_placeholders(
            body,
            "https://zoom.example/j/123",
            "abc123",
            "{{ZOOM_MEETING_LINK}}",
            "{{ZOOM_MEETING_PASSCODE}}",
        )
        self.assertIn("https://zoom.example/j/123", updated)
        self.assertIn("abc123", updated)

    def test_build_meeting_plan_uses_first_matching_day(self) -> None:
        config = AppConfig(
            canvas_base_url="https://canvas.example.edu",
            canvas_domain="https://canvas.example.edu",
            canvas_api_token="token",
            canvas_role_account_id="self",
            canvas_coordinator_role_id="1",
            canvas_coordinator_role_label="Coordinator",
            canvas_coordinator_base_role_type="TeacherEnrollment",
            canvas_homepage_link_placeholder="{{ZOOM_MEETING_LINK}}",
            canvas_homepage_passcode_placeholder="{{ZOOM_MEETING_PASSCODE}}",
            canvas_request_timeout_seconds=45,
            canvas_content_copy_timeout_minutes=30,
            canvas_poll_interval_seconds=10,
            canvas_notify_coordinators=False,
            canvas_notify_homepage_update=False,
            zoom_lti_base_url="https://applications.zoom.us/api/v1/lti/rich",
            zoom_lti_key="key",
            zoom_lti_secret="secret",
            zoom_lti_host_user_id="teacher@example.edu",
            zoom_oauth_base_url="https://zoom.us",
            zoom_api_base_url="https://api.zoom.us/v2",
            zoom_oauth_client_id="client",
            zoom_oauth_client_secret="secret",
            zoom_oauth_account_id="account",
            default_meeting_start_time=time(18, 0),
            default_meeting_duration_minutes=120,
            default_meeting_timezone="America/Denver",
            meeting_topic_template="{course_code} Live Class Session",
            zoom_meeting_settings={"waiting_room": True},
            max_workers=4,
            max_retries=5,
            max_backoff_seconds=30,
            canvas_per_page=100,
            report_directory=Path("reports"),
        )
        row = CourseShellRow(
            row_number=2,
            live_course_id="123",
            master_course_id="456",
            course_start_date=date(2026, 5, 5),
            course_end_date=date(2026, 8, 12),
            course_days=(0, 2),
            coordinator_user_ids=("2001",),
        )
        course = CanvasCourse("123", 123, "English 101", "ENG-101", "ctx")

        plan = build_meeting_plan(row, course, config)

        self.assertEqual(plan.first_occurrence_at.date(), date(2026, 5, 6))
        self.assertEqual(plan.weekly_days, "2,4")
        self.assertEqual(plan.payload["topic"], "ENG-101 Live Class Session")

    def test_lti_signature_is_url_safe(self) -> None:
        signature = build_lti_signature("secret", [("key", "abc"), ("timestamp", "123"), ("userId", "teacher@example.edu")])
        self.assertNotIn("+", signature)
        self.assertNotIn("/", signature)


if __name__ == "__main__":
    unittest.main()
