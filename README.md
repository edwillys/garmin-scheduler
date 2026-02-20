# garmin-scheduler

Uploads Garmin activities to Google Drive on a schedule.

**Configuration (non-secret)**

Edit [sync_settings.toml](sync_settings.toml):

- `sync.activity_type`: Garmin activity type key to sync (e.g. `lap_swimming`, `swimming`, `running`).
- `sync.num_last_activities`: how many recent activities to inspect.

**Secrets / env vars**

Required:

- `GARMIN_SESSION`: base64 encoded tar of `.garth` (see below)
- `GDRIVE_FOLDER_ID`: target Drive folder id
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
3) Run `python sync_swim.py` once locally.
   - A browser window opens for consent.
   - The script will write a token file `.gdrive_token.json` (unless you already provided `GDRIVE_TOKEN_JSON`).
4) Copy the contents of `.gdrive_token.json` into `GDRIVE_TOKEN_JSON` for headless runs.

## GitHub Actions (no browser)

Actions cannot complete the interactive OAuth step, so it must use an existing refresh token.

Add these repository secrets:

- `GARMIN_SESSION`
- `GDRIVE_FOLDER_ID`
- `GDRIVE_CREDENTIALS` (OAuth client JSON)
- `GDRIVE_TOKEN_JSON` (OAuth token JSON)

The workflow in [.github/workflows/daily_sync.yml](.github/workflows/daily_sync.yml) reads these secrets and runs `sync_swim.py` headlessly.

## Formatting / linting

- Format: `python -m black .`
- Lint: `python -m flake8`

## Pre-commit

Install the git hooks once:

- `pip install .[dev]`
- `pre-commit install`

Then on each commit, Black and Flake8 will run automatically.

## generate_garmin_session.py

Helper for first-time setup:

- Log in once locally: `python -c "import garth; garth.login('EMAIL','PASS'); garth.save('.garth')"`
- Run `python generate_garmin_session.py` (optionally `--garth-dir <path>`).
- Copy the printed base64 string into your repo's `GARMIN_SESSION` secret.
