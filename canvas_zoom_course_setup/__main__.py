from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from .config import load_config
from .csv_loader import load_course_rows
from .errors import AppError
from .service import CourseShellSetupService, write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Set up existing Canvas course shells by copying content, enrolling coordinators, creating Zoom meetings through LTI Pro, and updating the course homepage."
    )
    parser.add_argument("--csv", required=True, help="Path to the course setup CSV file.")
    parser.add_argument("--env-file", default=".env", help="Path to the .env file. Defaults to .env in the current folder.")
    parser.add_argument("--workers", type=int, help="Override the number of worker threads.")
    parser.add_argument("--report-path", help="Optional explicit CSV report path.")
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration and inputs without writing changes.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
    )

    try:
        config = load_config(Path(args.env_file))
        rows = load_course_rows(Path(args.csv))
        workers = args.workers or config.max_workers
        if workers <= 0:
            raise AppError("CLI001", "--workers must be greater than zero.")

        report_path = (
            Path(args.report_path)
            if args.report_path
            else config.report_directory / f"course-shell-setup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        )

        service = CourseShellSetupService(config)
        results = service.run(rows, workers=workers, dry_run=args.dry_run)
        write_report(report_path, results)

        success_count = sum(1 for result in results if result.status == "success")
        dry_run_count = sum(1 for result in results if result.status == "dry_run")
        failed_count = sum(1 for result in results if result.status == "failed")

        print(f"Processed {len(results)} course row(s).")
        print(f"Successful: {success_count}")
        print(f"Dry run: {dry_run_count}")
        print(f"Failed: {failed_count}")
        if failed_count:
            print("Failure details:")
            for result in results:
                if result.status != "failed":
                    continue
                error_code = result.error_code or "UNKNOWN"
                error_message = result.error_message or "No error message provided."
                print(f"- Row {result.row_number} [{error_code}] {error_message}")
        print(f"Report: {report_path}")

        return 1 if failed_count else 0
    except AppError as exc:
        logging.error("%s", exc)
        print(f"Error: [{exc.code}] {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
