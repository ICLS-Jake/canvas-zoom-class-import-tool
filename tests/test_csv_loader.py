from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from canvas_zoom_course_setup.csv_loader import load_course_rows


class CsvLoaderTests(unittest.TestCase):
    def test_loads_required_and_optional_fields(self) -> None:
        csv_text = """Live Course ID,Master Course ID,Course Start Date,Course End Date,Course Days,Coordinator User IDs,Meeting Start Time,Meeting Duration Minutes,Meeting Timezone,LTI Context ID,Meeting Topic,Zoom Host User ID
123,456,2026-05-04,2026-08-12,Mon/Wed,"2001;2002",18:00,120,America/Denver,abc123,Topic,instructor@example.edu
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "courses.csv"
            csv_path.write_text(csv_text, encoding="utf-8")
            rows = load_course_rows(csv_path)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.live_course_id, "123")
        self.assertEqual(row.master_course_id, "456")
        self.assertEqual(row.course_days, (0, 2))
        self.assertEqual(row.coordinator_user_ids, ("2001", "2002"))
        self.assertEqual(row.meeting_duration_minutes, 120)
        self.assertEqual(row.meeting_timezone, "America/Denver")
        self.assertEqual(row.lti_context_id, "abc123")


if __name__ == "__main__":
    unittest.main()
