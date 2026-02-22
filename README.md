# garmin-scheduler

Uploads Garmin activities to Google Drive on a schedule.

This repo also supports creating and scheduling Garmin workouts from a plan file.
The workflow is inspired by: https://github.com/yeekang-0311/garmin_planner

**Configuration (non-secret)**

Edit [sync_settings.toml](sync_settings.toml):

- `sync.activity_type`: Garmin activity type key to sync (e.g. `lap_swimming`, `swimming`, `running`).
- `sync.num_last_activities`: how many recent activities to inspect.
- `sync.detail_level`: `summary` or `detailed` (includes laps/splits/metrics when available).

Workout import settings:

- `workouts.deleteSameNameWorkout`: if true, deletes existing workouts with the same name before creating.

**Secrets / env vars**

Required:

- `GARMIN_SESSION`: base64 encoded tar of `.garth` (see below)
- `GDRIVE_FOLDER_ID_ACTIVITY_DATA`: Drive folder id where activity JSON files are stored
- `GDRIVE_FOLDER_ID_WORKOUTS`: Drive folder id containing workout plan YAML files
- `GDRIVE_CREDENTIALS`: Google credentials JSON (either OAuth client JSON or service account JSON)

OAuth (recommended for personal My Drive) uses:

- `GDRIVE_TOKEN_JSON`: contents of the OAuth token JSON (from a local run)

Service account note:

- Service accounts cannot upload to **My Drive** because they have no storage quota.
  Use a **Shared Drive** instead, or use OAuth.

## Install

`pip install .`

Dev tools:

`pip install .[dev]`

## Local OAuth bootstrap (one-time)

1) In Google Cloud Console:
   - Enable **Google Drive API**.
   - Create an OAuth Client ID of type **Desktop app**.
   - Download the client JSON.
2) Set `GDRIVE_CREDENTIALS` to the downloaded client JSON (as a JSON string).
3) Run `python -m garmin_scheduler.sync_activity` once locally.
   - A browser window opens for consent.
   - The script prints a JSON token; copy it into `GDRIVE_TOKEN_JSON`.

## sync_activity.py

Sync mode (default): fetch recent activities and upload JSON to Drive:

- `python -m garmin_scheduler.sync_activity`

List mode (read-only; does not touch Drive):

- `python -m garmin_scheduler.sync_activity --list`
- `python -m garmin_scheduler.sync_activity --list --start-date 2026-02-01 --end-date 2026-02-21`

Date range filtering also applies to sync mode:

- `python -m garmin_scheduler.sync_activity --start-date 2026-02-01 --end-date 2026-02-21`

## GitHub Actions (no browser)

Actions cannot complete the interactive OAuth step, so it must use an existing refresh token.

Add these repository secrets:

- `GARMIN_SESSION`
- `GDRIVE_FOLDER_ID_ACTIVITY_DATA`
- `GDRIVE_CREDENTIALS` (OAuth client JSON)
- `GDRIVE_TOKEN_JSON` (OAuth token JSON)

The workflow in [.github/workflows/daily_sync.yml](.github/workflows/daily_sync.yml) reads these secrets and runs `sync_activity.py` headlessly.
The workflow runs `python -m garmin_scheduler.sync_activity` headlessly.

## Formatting / linting

- Format: `python -m black .`
- Lint: `python -m flake8`

## Tests

- `python -m unittest discover -s tests -p "test_*.py"`

Optional read-only Garmin validation (skipped by default):

- `set RUN_GARMIN_VALIDATE_TESTS=1`
- `set GARMIN_VALIDATE_MAX_FETCH=25`
- `python -m unittest tests.integration_garmin_readonly_validation`

## Pre-commit

Install the git hooks once:

- `pip install .[dev]`
- `pre-commit install`

Then on each commit, Black and Flake8 will run automatically.

## tools/generate_garmin_session.py

Helper for first-time setup:

- Log in once locally: `python -c "import garth; garth.login('EMAIL','PASS'); garth.save('.garth')"`
- Run `python tools/generate_garmin_session.py` (optionally `--garth-dir <path>`).
- Copy the printed base64 string into your repo's `GARMIN_SESSION` secret.

## schedule_workout

Schedules an existing Garmin workout onto your Garmin Connect calendar (so it can sync to your watch).

Required env var:

- `GARMIN_SESSION`

Examples:

- List workouts: `python schedule_workout.py --list`
- Schedule by name: `python schedule_workout.py --workout-name "ga_30min" --date 2026-02-22`
- Schedule by id: `python schedule_workout.py --workout-id 1234567890 --date 2026-02-22`

All examples above also work as:

- `python -m garmin_scheduler.schedule_workout ...`

## sync_workouts

Creates workouts on Garmin Connect and schedules them on your Garmin calendar using a YAML plan file.

Run:

- `python -m garmin_scheduler.sync_workouts workout_plan_swim.yaml`
- `python -m garmin_scheduler.sync_workouts workout_plan_run.yaml`

Drive mode (sync all YAML plans in the folder):

- `python -m garmin_scheduler.sync_workouts --from-drive`

List mode:

- List plan filenames in Drive: `python -m garmin_scheduler.sync_workouts --from-drive --list-plans`
- List the computed schedule (includes outdated entries): `python -m garmin_scheduler.sync_workouts --list-schedule workout_plan_swim.yaml`
- List the computed schedule for all Drive plans: `python -m garmin_scheduler.sync_workouts --from-drive --list-schedule`

### Workout Plan YAML spec

See [docs/workout-yaml-spec.md](docs/workout-yaml-spec.md).
