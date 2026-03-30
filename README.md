# Canvas Zoom Course Shell Setup Tool
This tool configures existing Canvas course shells in batch from a single CSV. For each course row, it:
1. Copies content from the mapped master course into the live course.
2. Enrolls the listed coordinators with the Canvas `Coordinator` role.
3. Creates a recurring Zoom meeting and associates it to the course via Zoom LTI Pro.
4. Replaces homepage placeholders with the meeting join link and passcode.
It includes retries, timeout handling, pagination support, per-course parallelism, and a per-run CSV report.
---
## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Setup](#setup)
3. [CSV Format](#csv-format)
4. [Placeholder Setup](#placeholder-setup)
5. [Running the Script](#running-the-script)
6. [Output](#output)
7. [Error Codes](#error-codes)
8. [Operational Notes](#operational-notes)
9. [Testing](#testing)
10. [Suggested Rollout Process](#suggested-rollout-process)
---
## Prerequisites
- **Python 3.11+**
- **Canvas API token** with permission to:
  - Read courses
  - Create content migrations
  - Manage enrollments
  - Read and update pages
  - Read roles in the target account (if using role-label lookup)
- **Zoom LTI Pro credentials** (`ZOOM_LTI_KEY`, `ZOOM_LTI_SECRET`, `ZOOM_LTI_HOST_USER_ID`)
- **Zoom Server-to-Server OAuth app** (see [Setup](#setup) step 4 for how to create one)
---
## Setup
### 1. Install dependencies
Create and activate a virtual environment if desired, then install:
```powershell
python -m pip install -r requirements.txt
```
### 2. Create your `.env` file
Create a `.env` file in the repository root. Copy this starter template and fill in your values:
```dotenv
# Required
CSV_FILE_PATH=canvas_zoom_import_courses.csv
CANVAS_BASE_URL=https://your-canvas-domain.instructure.com
CANVAS_API_TOKEN=replace_me
ZOOM_LTI_KEY=replace_me
ZOOM_LTI_SECRET=replace_me
ZOOM_LTI_HOST_USER_ID=replace_me
ZOOM_LTI_TOOL_ID=
ZOOM_OAUTH_CLIENT_ID=replace_me
ZOOM_OAUTH_CLIENT_SECRET=replace_me
ZOOM_OAUTH_ACCOUNT_ID=replace_me
# Common optional overrides
LTI_LAUNCH_PAYLOADS_DIRECTORY=
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
ZOOM_LTI_SIGNATURE_USE_URLSAFE_BASE64=true
ZOOM_LTI_SIGNATURE_STRIP_PADDING=true
ZOOM_LTI_SIGNATURE_PARAM_ORDER=key,timestamp,userId
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
### 3. Set up a Zoom Server-to-Server OAuth app
The script uses Zoom OAuth to read meeting details (join URL, passcode) immediately after creation.
1. In the Zoom App Marketplace, create a **Server-to-Server OAuth** app (not a user-managed OAuth app).
2. Install and authorize it for the same Zoom account that owns the meeting host user.
3. Grant scopes that allow reading meeting details (`GET /meetings/{meetingId}` or `GET /users/{userId}/meetings`) and user lookup (`GET /users/{email}`).
4. Copy the **Client ID**, **Client Secret**, and **Account ID** into `ZOOM_OAUTH_CLIENT_ID`, `ZOOM_OAUTH_CLIENT_SECRET`, and `ZOOM_OAUTH_ACCOUNT_ID` in your `.env`.
### 4. Prepare your CSV
Use the default filename `canvas_zoom_import_courses.csv` or set `CSV_FILE_PATH` in `.env`. See [CSV Format](#csv-format) below.
**If Canvas omits `lti_context_id` for your courses**, optionally set `LTI_LAUNCH_PAYLOADS_DIRECTORY` to a folder containing per-course LTI launch payload files:
- Supported filenames: `<canvas_course_id>.json`, `<canvas_course_id>.jwt`, `<live_course_id>.json`, or `<live_course_id>.jwt`
- For JSON payloads, the script reads `context.id` from the LTI 1.3 claim `https://purl.imsglobal.org/spec/lti/claim/context`.
- For JWT payloads, the script decodes the payload section (without signature verification) and reads the same claim.
### 5. Run a dry run first
```powershell
python -m canvas_zoom_course_setup --dry-run
```
---
## CSV Format
Default file: `canvas_zoom_import_courses.csv`
**Required columns:**
| Column | Notes |
|---|---|
| `Live Course ID` | Canvas course ID for the target course |
| `Master Course ID` | Canvas course ID to copy content from |
| `Course Start Date` | `YYYY-MM-DD` format strongly recommended |
| `Course End Date` | `YYYY-MM-DD` format strongly recommended |
| `Course Days` | e.g. `Mon/Wed`, `Tue/Thur`, `Tuesday Thursday`, or `Mon,Wed` |
| `Coordinator User IDs` | Comma, semicolon, or pipe-separated |
**Optional columns:**
| Column | Notes |
|---|---|
| `Meeting Start Time` | Overrides `DEFAULT_MEETING_START_TIME` |
| `Meeting Duration Minutes` | Overrides `DEFAULT_MEETING_DURATION_MINUTES` |
| `Meeting Timezone` | IANA name, e.g. `America/Denver` |
| `LTI Context ID` | Fallback if all automatic methods fail |
| `Meeting Topic` | Overrides `MEETING_TOPIC_TEMPLATE` |
| `Zoom Host User ID` | Email preferred; resolved to Zoom user ID automatically |
**Additional notes:**
- Canvas course IDs can be internal IDs. SIS-style identifiers like `sis_course_id:ABC123` are also supported — the script resolves them to real Canvas IDs before proceeding.
- `Zoom Host User ID` accepts either a Zoom user ID or an email. Email is preferred; the result is cached for the duration of the run.
---
## Placeholder Setup
The homepage replacement looks for these placeholders in the live course homepage by default:
- `{{ZOOM_MEETING_LINK}}`
- `{{ZOOM_MEETING_PASSCODE}}`
You can change the placeholder strings in `.env`:
```dotenv
CANVAS_HOMEPAGE_LINK_PLACEHOLDER={{ZOOM_MEETING_LINK}}
CANVAS_HOMEPAGE_PASSCODE_PLACEHOLDER={{ZOOM_MEETING_PASSCODE}}
```
The rest of the page body is preserved exactly as returned by Canvas. Note that the homepage must be a classic HTML page — Canvas block-editor pages are not supported.
---
## Running the Script
**Dry run** (no changes made):
```powershell
python -m canvas_zoom_course_setup --dry-run
```
**Live run:**
```powershell
python -m canvas_zoom_course_setup
```
**Useful flags:**
| Flag | Description |
|---|---|
| `--csv .\my-other-import-file.csv` | Override `CSV_FILE_PATH` |
| `--env-file .env` | Specify a different env file |
| `--workers 6` | Override `MAX_WORKERS` |
| `--report-path .\reports\my-run.csv` | Override default report path |
| `--log-level DEBUG` | Verbose logging |
| `--zoom-only` | Skip Canvas content copy and coordinator enrollment (useful for testing Zoom/LTI + homepage updates in isolation) |
**LTI signature troubleshooting helper:**
```powershell
python -m canvas_zoom_course_setup.lti_signature_debug_helper --key <LTI_KEY> --timestamp <TIMESTAMP_MS> --user-id <USER_ID> --secret <LTI_SECRET>
```
---
## Output
Each run writes a CSV report to `reports/course-shell-setup-YYYYMMDD-HHMMSS.csv` (configurable via `REPORT_DIRECTORY` or `--report-path`).
Report columns include:
- Row number and source identifiers
- Resolved Canvas course IDs and course name
- Success or failure status
- Created Zoom meeting ID, join URL, and passcode
- Warnings, error code, and error message
- Elapsed time per course
---
## Error Codes
| Prefix | Area |
|---|---|
| `CFG...` | Configuration problem in `.env` |
| `CSV...` | Input CSV validation problem |
| `ROL...` | Coordinator role lookup problem |
| `CNV404` | Canvas course not found |
| `CNV409` | Canvas content migration failed |
| `CNV410` | Canvas content migration timed out |
| `CNV411` | Coordinator enrollment failed |
| `ZLT...` | Zoom LTI meeting creation or association failed |
| `ZOM401` | Zoom OAuth token request failed |
| `ZOM404` | Zoom meeting lookup failed |
| `ZOM409` | Zoom host email could not be resolved to a Zoom user ID |
| `PGE...` | Homepage / front-page issue |
---
## Operational Notes
These are important behaviors to be aware of before running against real courses.
- **Zoom LTI Pro `createAndAssociate`** creates the meeting and ties it to the LMS course so it appears in the LTI tool and creates Canvas calendar items. Zoom documents that LMS calendar creation is asynchronous — the account owner or admin may need to launch LTI Pro in the course as the instructor before calendar items become visible.
- **The script is not fully idempotent for Zoom meetings.** If you rerun it for the same course, it will create a new meeting unless you intervene manually.
- **Homepage replacement is string-based.** The imported course homepage must contain the configured placeholders and must be a classic HTML page (not a Canvas block-editor page).
- **`lti_context_id` resolution order.** Canvas does not guarantee `lti_context_id` appears in every course API response. The script tries in this order:
  1. `include[]=lti_context_id` on the Canvas API response
  2. A Canvas sessionless launch of the Zoom LTI tool to auto-generate the missing context ID
  3. The `context.id` field from an LTI 1.3 launch payload file (if `LTI_LAUNCH_PAYLOADS_DIRECTORY` is set)
  4. The `LTI Context ID` column in the CSV as a last resort
- **`ZOOM_LTI_TOOL_ID` is optional but recommended.** When set, the script skips external-tool discovery and can trigger `lti_context_id` generation faster for brand-new course shells.
---
## Testing
The repository includes a unit test suite covering CSV parsing and homepage/scheduling helpers:
```powershell
python -m unittest discover -s tests
```
---
## Suggested Rollout Process
Before running against your full course list:
1. Test with one sandbox course and one coordinator.
2. Verify the course copy completed correctly.
3. Verify the coordinator role landed with the expected permissions.
4. Verify the Zoom meeting appears in LTI Pro and the Canvas calendar.
5. Verify the homepage placeholders were replaced.
