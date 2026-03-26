from __future__ import annotations

import csv
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .clients import CanvasClient, ZoomClient, ZoomLTIClient
from .config import AppConfig
from .errors import AppError
from .models import CanvasCourse, CourseResult, CourseShellRow
from .utils import (
    build_meeting_plan,
    extract_lti_context_id_from_launch_payload_text,
    replace_homepage_placeholders,
    summarize_migration_issues,
)

LOGGER = logging.getLogger(__name__)


class CourseShellSetupService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.canvas = CanvasClient(config)
        self.zoom = ZoomClient(config)
        self.zoom_lti = ZoomLTIClient(config)

    def run(self, rows: list[CourseShellRow], *, workers: int, dry_run: bool) -> list[CourseResult]:
        results: list[CourseResult] = []
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="course-shell") as executor:
            future_map = {executor.submit(self.process_row, row, dry_run): row for row in rows}
            for future in as_completed(future_map):
                row = future_map[future]
                try:
                    result = future.result()
                except AppError as exc:
                    LOGGER.error("Row %s failed with %s", row.row_number, exc)
                    result = CourseResult(
                        row_number=row.row_number,
                        live_course_identifier=row.live_course_id,
                        master_course_identifier=row.master_course_id,
                        status="failed",
                        error_code=exc.code,
                        error_message=exc.message,
                        meeting_id=str(exc.details.get("meeting_id", "")),
                    )
                except Exception as exc:  # pragma: no cover
                    LOGGER.exception("Row %s failed with an unexpected error.", row.row_number)
                    result = CourseResult(
                        row_number=row.row_number,
                        live_course_identifier=row.live_course_id,
                        master_course_identifier=row.master_course_id,
                        status="failed",
                        error_code="UNEXPECTED",
                        error_message=str(exc),
                    )
                results.append(result)
        return sorted(results, key=lambda item: item.row_number)

    def process_row(self, row: CourseShellRow, dry_run: bool) -> CourseResult:
        started_at = time.perf_counter()
        live_course = self.canvas.get_course(row.live_course_id, include_lti_context_id=True)
        master_course = self.canvas.get_course(row.master_course_id, include_lti_context_id=False)
        role = self.canvas.resolve_coordinator_role()
        lti_context_id = self._resolve_lti_context_id(live_course, row)
        if not lti_context_id:
            raise AppError(
                "ZLTCTX",
                f"Course {live_course.canvas_id} does not have an LTI context ID available from Canvas course data, launch payload fallback, or CSV override.",
            )

        meeting_plan = build_meeting_plan(row, live_course, self.config)

        if dry_run:
            elapsed = time.perf_counter() - started_at
            return CourseResult(
                row_number=row.row_number,
                live_course_identifier=row.live_course_id,
                master_course_identifier=row.master_course_id,
                live_canvas_course_id=str(live_course.canvas_id),
                master_canvas_course_id=str(master_course.canvas_id),
                course_name=live_course.name,
                status="dry_run",
                warnings=f"Would copy content, enroll {len(row.coordinator_user_ids)} coordinator(s), create Zoom meeting starting {meeting_plan.first_occurrence_at.isoformat()}",
                elapsed_seconds=elapsed,
            )

        LOGGER.info("Row %s: starting content copy for Canvas course %s", row.row_number, live_course.canvas_id)
        migration = self.canvas.start_content_copy(live_course.canvas_id, master_course.canvas_id)

        for coordinator_user_id in row.coordinator_user_ids:
            self.canvas.enroll_user(live_course.canvas_id, coordinator_user_id, role)

        migration_id = int(migration["id"])
        issues = self.canvas.wait_for_content_copy(
            live_course.canvas_id,
            migration_id,
            str(migration["progress_url"]),
        )
        warnings = summarize_migration_issues(issues)

        meeting_id = self.zoom_lti.create_and_associate(
            host_user_id=row.zoom_host_user_id or self.config.zoom_lti_host_user_id,
            context_id=lti_context_id,
            domain=self.config.canvas_domain,
            course_id=live_course.canvas_id,
            meeting_info=meeting_plan.payload,
        )
        meeting = self.zoom.get_meeting(meeting_id)
        join_url = str(meeting.get("join_url", "")).strip()
        passcode = str(meeting.get("password", "")).strip()
        if not join_url:
            raise AppError("ZOM406", f"Zoom meeting {meeting_id} did not include a join URL.", details={"meeting_id": meeting_id})
        if not passcode:
            raise AppError(
                "ZOM407",
                f"Zoom meeting {meeting_id} did not include a passcode. Check the account's meeting passcode settings.",
                details={"meeting_id": meeting_id},
            )

        front_page = self.canvas.get_front_page(live_course.canvas_id)
        if front_page.get("editor") == "block_editor":
            raise AppError(
                "PGE006",
                f"Course {live_course.canvas_id} uses a block editor front page. The placeholder replacement flow expects classic HTML page content.",
                details={"meeting_id": meeting_id},
            )
        updated_body = replace_homepage_placeholders(
            str(front_page.get("body") or ""),
            join_url,
            passcode,
            self.config.canvas_homepage_link_placeholder,
            self.config.canvas_homepage_passcode_placeholder,
        )
        self.canvas.update_front_page(live_course.canvas_id, updated_body)

        elapsed = time.perf_counter() - started_at
        return CourseResult(
            row_number=row.row_number,
            live_course_identifier=row.live_course_id,
            master_course_identifier=row.master_course_id,
            live_canvas_course_id=str(live_course.canvas_id),
            master_canvas_course_id=str(master_course.canvas_id),
            course_name=live_course.name,
            status="success",
            meeting_id=meeting_id,
            join_url=join_url,
            passcode=passcode,
            warnings=warnings,
            elapsed_seconds=elapsed,
        )

    def _resolve_lti_context_id(self, course: CanvasCourse, row: CourseShellRow) -> str | None:
        if course.lti_context_id:
            return course.lti_context_id

        from_launch = self._lti_context_id_from_launch_payload(course, row)
        if from_launch:
            return from_launch

        return row.lti_context_id

    def _lti_context_id_from_launch_payload(self, course: CanvasCourse, row: CourseShellRow) -> str | None:
        directory = self.config.lti_launch_payloads_directory
        if not directory:
            return None
        base_path = Path(directory)
        if not base_path.exists():
            return None

        candidates = [
            base_path / f"{course.canvas_id}.json",
            base_path / f"{course.canvas_id}.jwt",
            base_path / f"{row.live_course_id}.json",
            base_path / f"{row.live_course_id}.jwt",
        ]
        for candidate in candidates:
            if not candidate.exists() or not candidate.is_file():
                continue
            context_id = extract_lti_context_id_from_launch_payload_text(candidate.read_text(encoding="utf-8"))
            if context_id:
                LOGGER.debug("Resolved LTI context ID for course %s from launch payload file %s", course.canvas_id, candidate)
                return context_id
        return None


def write_report(report_path: Path, results: list[CourseResult]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "row_number",
                "live_course_identifier",
                "master_course_identifier",
                "live_canvas_course_id",
                "master_canvas_course_id",
                "course_name",
                "status",
                "meeting_id",
                "join_url",
                "passcode",
                "warnings",
                "error_code",
                "error_message",
                "elapsed_seconds",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(result.to_record())
