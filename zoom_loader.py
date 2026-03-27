"""
trigger_lti_context.py

Logs into Canvas and clicks the Zoom LTI nav link for each course in the
import CSV. This causes Canvas to generate and persist the lti_context_id
for each course, so the main canvas_zoom_course_setup script can retrieve
it via include[]=lti_context_id afterward.

Usage:
    pip install playwright python-dotenv
    playwright install chromium
    python trigger_lti_context.py

Optional flags:
    --csv path/to/your.csv          (overrides CSV_FILE_PATH in .env)
    --env-file path/to/.env         (default: .env in cwd)
    --headed                        (show browser window, useful for debugging)
    --timeout 30                    (per-action timeout in seconds, default 30)
    --delay 2                       (seconds to wait on Zoom page before moving on, default 2)
"""

import argparse
import csv
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Trigger Canvas LTI context_id generation via browser.")
    parser.add_argument("--csv", dest="csv_path", default=None)
    parser.add_argument("--env-file", dest="env_file", default=".env")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    parser.add_argument("--timeout", type=int, default=30, help="Per-action timeout in seconds")
    parser.add_argument("--delay", type=int, default=2, help="Seconds to wait on Zoom page before moving to next course")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def load_course_ids(csv_path: str) -> list[str]:
    """Read Live Course IDs from the import CSV, skipping blank/header rows."""
    course_ids = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            course_id = row.get("Live Course ID", "").strip()
            if not course_id:
                logger.debug("Row %d: blank Live Course ID, skipping.", i)
                continue
            course_ids.append(course_id)
    return course_ids


def run(args):
    logging.getLogger().setLevel(args.log_level)

    load_dotenv(args.env_file)

    canvas_base_url = os.environ.get("CANVAS_BASE_URL", "").rstrip("/")
    canvas_username = os.environ.get("CANVAS_USERNAME", "")
    canvas_password = os.environ.get("CANVAS_PASSWORD", "")
    zoom_tool_id = os.environ.get("ZOOM_LTI_TOOL_ID", "")
    csv_path = args.csv_path or os.environ.get("CSV_FILE_PATH", "canvas_zoom_import_courses.csv")

    if not canvas_base_url:
        logger.error("CANVAS_BASE_URL is not set.")
        sys.exit(1)
    if not canvas_username or not canvas_password:
        logger.error("CANVAS_USERNAME and CANVAS_PASSWORD must be set in .env")
        sys.exit(1)
    if not zoom_tool_id:
        logger.error("ZOOM_LTI_TOOL_ID is not set in .env")
        sys.exit(1)
    if not Path(csv_path).exists():
        logger.error("CSV file not found: %s", csv_path)
        sys.exit(1)

    course_ids = load_course_ids(csv_path)
    if not course_ids:
        logger.error("No course IDs found in CSV.")
        sys.exit(1)

    logger.info("Found %d course(s) to process.", len(course_ids))
    timeout_ms = args.timeout * 1000

    success = []
    failed = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(timeout_ms)

        # --- Login ---
        logger.info("Logging into Canvas at %s", canvas_base_url)
        page.goto(f"{canvas_base_url}/login/canvas", wait_until="domcontentloaded")
        page.wait_for_load_state("domcontentloaded")
        logger.debug("Login page URL: %s", page.url)

        page.fill('input[type="email"], input[type="text"], input[name*="unique_id"]', canvas_username)
        page.fill('input[type="password"]', canvas_password)

        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout_ms):
                page.click('button[type="submit"], input[type="submit"]')

            page.wait_for_selector(
                '#global_nav_dashboard_link, #header .ic-app-header__menu-list, nav.ic-app-header__menu-list-item',
                timeout=timeout_ms,
            )
            logger.info("Login successful. Current URL: %s", page.url)
        except PlaywrightTimeoutError:
            logger.error(
                "Login timed out or failed. Still on: %s — check credentials.", page.url
            )
            browser.close()
            sys.exit(1)

        # --- Per-course: click Zoom nav link ---
        for course_id in course_ids:
            course_url = f"{canvas_base_url}/courses/{course_id}"
            zoom_nav_url = f"{course_url}/external_tools"

            logger.info("Course %s: navigating directly to Zoom tool.", course_id)
            try:
                # Navigate directly to the Zoom LTI nav item URL rather than
                # the course page, bypassing Canvas's heavy dashboard JS.
                # Canvas course nav LTI links follow this pattern.
                zoom_tool_url = f"{canvas_base_url}/courses/{course_id}/external_tools/{zoom_tool_id}"
                
                # Fire the navigation but don't wait for it to "complete" —
                # Canvas will start the LTI launch regardless.
                try:
                    page.goto(zoom_tool_url, wait_until="commit", timeout=timeout_ms)
                except PlaywrightTimeoutError:
                    pass  # commit-level timeout is fine, launch may still have fired

                # Wait for either the LTI iframe, course nav, or a Canvas 404
                # page — whichever comes first.
                matched = page.wait_for_selector(
                    'iframe[src*="zoom"], iframe[title*="Zoom"], iframe[name*="tool_content"], nav#section-tabs, #content .not_found_page_message, #content h1',
                    timeout=timeout_ms,
                )

                # Check if we landed on a 404 instead of the tool.
                page_heading = page.locator('#content h1, .not_found_page_message').first
                if page_heading.count() > 0:
                    heading_text = page_heading.inner_text().strip().lower()
                    if "not found" in heading_text or "page not found" in heading_text or "doesn't exist" in heading_text or "unauthorized" in heading_text or "access denied" in heading_text:
                        logger.warning("Course %s: Canvas returned an error page (%s) — course ID may be invalid or inaccessible. Skipping.", course_id, heading_text)
                        failed.append(course_id)
                        continue

                logger.info("Course %s: Zoom tool loaded. context_id should now be persisted.", course_id)

                # Brief pause so Canvas has time to fully commit the record
                # before we move on.
                if args.delay > 0:
                    time.sleep(args.delay)

                success.append(course_id)

            except PlaywrightTimeoutError as e:
                logger.warning("Course %s: timed out waiting for Zoom tool. It may still have loaded. Error: %s", course_id, e)
                failed.append(course_id)
            except Exception as e:
                logger.error("Course %s: unexpected error: %s", course_id, e)
                failed.append(course_id)

        browser.close()

    # --- Summary ---
    print("\n--- Results ---")
    print(f"Succeeded : {len(success)}")
    print(f"Failed    : {len(failed)}")
    if failed:
        print("Failed course IDs:")
        for cid in failed:
            print(f"  {cid}")
    print("\nRun canvas_zoom_course_setup when complete.")


if __name__ == "__main__":
    run(parse_args())