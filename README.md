# Canvas Zoom Course Shell Setup Tool

This project creates and configures existing Canvas course shells from a single CSV. For each live course row, it:

1. Copies content from the mapped master course into the live course.
2. Enrolls the listed coordinators with the Canvas `Coordinator` role.
3. Creates a recurring Zoom meeting and associates it to the course through Zoom LTI Pro.
4. Replaces homepage placeholders with the meeting join link and passcode.

The script is designed for batch work and includes retries, timeout handling, pagination support, per-course parallelism, and a results report.

## Files

- Main entry point: `python -m canvas_zoom_course_setup`
- Environment file: `.env` (create this file manually in the repo root)
- Default input CSV: `canvas_zoom_import_courses.csv`

## Prerequisites

- Python 3.11+ recommended.
- Canvas API token with access to:
  - read courses
  - create content migrations
  - manage enrollments
  - read and update pages
  - read roles in the target account if you use role-label lookup
- Zoom LTI Pro credentials:
  - `ZOOM_LTI_KEY`
  - `ZOOM_LTI_SECRET`
  - a host `userId` or email for meeting creation

## Important operational notes

- Zoom LTI Pro `createAndAssociate` creates the Zoom meeting and ties it to the LMS course so it can appear correctly in the LTI tool and create Canvas calendar items.
- Zoom documents that LMS calendar creation is asynchronous, and the account owner or admin used for LTI calendar creation may need to launch LTI Pro in the course as the instructor before calendar items are visible.
- The homepage replacement logic is string-based. The imported course homepage must contain the configured placeholders, and it should be a classic HTML page rather than a Canvas block-editor page.
- The script is not fully idempotent for Zoom meetings. If you rerun it for the same course, it will create a new meeting unless you intervene manually.
- Some Canvas environments expose `lti_context_id` via `GET /api/v1/courses/:id?include[]=lti_context_id`. If yours does not, add an `LTI Context ID` column to the CSV for each course row.

## Setup

1. Create and activate a virtual environment if you want one.
2. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

3. Create a `.env` file in the repository root. You can copy/paste this starter template and then fill in your real values:

```dotenv
# Required
CSV_FILE_PATH=canvas_zoom_import_courses.csv
CANVAS_BASE_URL=https://your-canvas-domain.instructure.com
CANVAS_API_TOKEN=replace_me
ZOOM_LTI_KEY=replace_me
ZOOM_LTI_SECRET=replace_me
ZOOM_LTI_HOST_USER_ID=replace_me
ZOOM_OAUTH_CLIENT_ID=replace_me
ZOOM_OAUTH_CLIENT_SECRET=replace_me
ZOOM_OAUTH_ACCOUNT_ID=replace_me

# Common optional overrides
CANVAS_ROLE_ACCOUNT_ID=self
CANVAS_COORDINATOR_ROLE_ID=
CANVAS_COORDINATOR_ROLE_LABEL=Coordinator
CANVAS_COORDINATOR_BASE_ROLE_TYPE=TeacherEnrollment
CANVAS_HOMEPAGE_LINK_PLACEHOLDER={{ZOOM_MEETING_LINK}}
CANVAS_HOMEPAGE_PASSCODE_PLACEHOLDER={{ZOOM_MEETING_PASSCODE}}
CANVAS_REQUEST_TIMEOUT_SECONDS=45
CANVAS_CONTENT_COPY_TIMEOUT_MINUTES=30
CANVAS_POLL_INTERVAL_SECONDS=10
CANVAS_NOTIFY_COORDINATORS=false
CANVAS_NOTIFY_HOMEPAGE_UPDATE=false
ZOOM_LTI_BASE_URL=https://applications.zoom.us/api/v1/lti/rich
ZOOM_LTI_DEBUG_SIGNATURE_BASE_STRING=false
ZOOM_OAUTH_BASE_URL=https://zoom.us
ZOOM_API_BASE_URL=https://api.zoom.us/v2
DEFAULT_MEETING_START_TIME=18:00
DEFAULT_MEETING_DURATION_MINUTES=120
DEFAULT_MEETING_TIMEZONE=America/Denver
MEETING_TOPIC_TEMPLATE={course_code} Live Class Session
ZOOM_MEETING_SETTINGS_JSON={"host_video":true,"participant_video":true,"join_before_host":false,"mute_upon_entry":true,"waiting_room":true}
MAX_WORKERS=4
MAX_RETRIES=5
MAX_BACKOFF_SECONDS=30
CANVAS_PER_PAGE=100
REPORT_DIRECTORY=reports
```

4. Configure a Zoom Server-to-Server OAuth app for post-creation meeting reads:
   - In Zoom App Marketplace, create a **Server-to-Server OAuth** app (not a user-managed OAuth app).
   - Install/authorize it for the same Zoom account that owns the meeting host user.
   - Grant API scopes that allow this tool to fetch meeting details immediately after `createAndAssociate` (for example, read access to meeting endpoints such as `GET /meetings/{meetingId}` or `GET /users/{userId}/meetings`).
   - This follow-up read is how the script captures join URL and passcode for homepage placeholder replacement and reporting.
5. Prepare your CSV (default file: `canvas_zoom_import_courses.csv`).
6. Run a dry run first.

## CSV format

Required columns:

- `Live Course ID`
- `Master Course ID`
- `Course Start Date`
- `Course End Date`
- `Course Days`
- `Coordinator User IDs`

Supported optional columns:

- `Meeting Start Time`
- `Meeting Duration Minutes`
- `Meeting Timezone`
- `LTI Context ID`
- `Meeting Topic`
- `Zoom Host User ID`

Notes:

- `Course Days` accepts values like `Mon/Wed`, `Tue/Thur`, `Tuesday Thursday`, or `Mon,Wed`.
- `Coordinator User IDs` can be separated with commas, semicolons, or pipes.
- Dates support `YYYY-MM-DD`, but using `YYYY-MM-DD` is strongly recommended.
- Timezones should use IANA names such as `America/Denver`.
- Canvas course identifiers can be internal IDs. If your Canvas instance allows SIS-style identifiers such as `sis_course_id:ABC123`, the script resolves the course first and then uses the real Canvas course ID for downstream actions.

## Placeholder setup

By default, the homepage replacement expects:

- `{{ZOOM_MEETING_LINK}}`
- `{{ZOOM_MEETING_PASSCODE}}`

You can change those in `.env`:

- `CANVAS_HOMEPAGE_LINK_PLACEHOLDER`
- `CANVAS_HOMEPAGE_PASSCODE_PLACEHOLDER`

The rest of the page body is preserved exactly as returned by Canvas.

## Running the script

Dry run:

```powershell
python -m canvas_zoom_course_setup --dry-run
```

Live run:

```powershell
python -m canvas_zoom_course_setup
```

Useful flags:

- `--csv .\my-other-import-file.csv` (overrides `CSV_FILE_PATH`)
- `--env-file .env`
- `--workers 6`
- `--report-path .\reports\my-run.csv`
- `--log-level DEBUG`

## Output

Each run writes a CSV report with:

- row number
- source identifiers
- resolved Canvas course IDs
- course name
- success or failure status
- created Zoom meeting ID
- join URL and passcode
- warnings
- error code and message
- elapsed time

Default report location: `reports/course-shell-setup-YYYYMMDD-HHMMSS.csv`

## Error codes you are most likely to see

- `CFG...`: configuration problem in `.env`
- `CSV...`: input CSV validation problem
- `ROL...`: coordinator role lookup problem
- `CNV404`: Canvas course not found
- `CNV409`: Canvas content migration failed
- `CNV410`: Canvas content migration timed out
- `CNV411`: coordinator enrollment failed
- `ZLT...`: Zoom LTI meeting creation or association failed
- `ZOM401`: Zoom OAuth token request failed
- `ZOM404`: Zoom meeting lookup failed
- `PGE...`: homepage/front-page issue

## Testing

The repository includes a small unit-test set for CSV parsing and homepage or scheduling helpers:

```powershell
python -m unittest discover -s tests
```

## Suggested rollout process

1. Test with one sandbox course and one coordinator.
2. Verify the course copy completed correctly.
3. Verify the coordinator role landed with the expected permissions.
4. Verify the Zoom meeting appears in LTI Pro and the Canvas calendar.
5. Verify the homepage placeholders were replaced.
6. Run the batch on the real CSV.
