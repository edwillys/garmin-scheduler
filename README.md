# garmin-scheduler
Uploads data from Garmin to Google Drive according to a schedule

Install the dependencies

pip install garth google-api-python-client google-auth google-auth-oauthlib

**sync_swim.py**

The main sync script. Key behaviour:

    Garmin auth – decodes GARMIN_SESSION (a base64-encoded .garth tar archive), extracts it safely to a temp directory (path-traversal protection), and calls garth.resume().
    Activity fetch – hits /activitylist-service/activities/search/activities for the last NUM_LAST_ACTIVITIES (default 30) entries and keeps only those with activityType.typeKey == "swimming".
    De-duplication / checksums – before uploading, the script lists existing Drive files in the folder and compares their description field (which stores the SHA-256 of the JSON payload) against the freshly fetched data. Files are skipped if unchanged, updated if the checksum changed, or uploaded if new.
     Google Drive auth – uses GDRIVE_CREDENTIALS and GDRIVE_FOLDER_ID.
        - Service Account JSON works only with Shared Drives (service accounts have no My Drive quota).
        - OAuth Client JSON (Installed App) works with personal My Drive folders.

OAuth (recommended for personal My Drive)

1) In Google Cloud Console:
    - Enable the Google Drive API.
    - Create an OAuth Client ID of type "Desktop app".
    - Download the client JSON.
2) Put the downloaded JSON contents into GDRIVE_CREDENTIALS (as a JSON string).
3) Run sync_swim.py once interactively.
    - A browser window will open for you to authorize.
    - The script will write a refreshable token to .gdrive_token.json next to the script.
4) Future runs (including scheduled runs) will refresh the token automatically.

GitHub Actions (no browser)

GitHub Actions cannot complete the interactive OAuth browser step.

To use OAuth in Actions:

1) Run sync_swim.py once on your computer to generate the token file (.gdrive_token.json).
2) Copy the contents of that token file into a GitHub secret named GDRIVE_TOKEN_JSON.
3) Pass that secret as an env var when running the script.

Example snippet:

        - name: Sync
            env:
                GDRIVE_TOKEN_JSON: ${{ secrets.GDRIVE_TOKEN_JSON }}
                GDRIVE_CREDENTIALS: ${{ secrets.GDRIVE_CREDENTIALS }}
                GDRIVE_FOLDER_ID: ${{ secrets.GDRIVE_FOLDER_ID }}
                GARMIN_SESSION: ${{ secrets.GARMIN_SESSION }}
            run: python sync_swim.py

**.github/workflows/daily_sync.yml**

    Triggers on cron: "0 6 * * *" (06:00 UTC) and workflow_dispatch.
    Uses Python 3.12, installs garth, google-api-python-client, google-auth.
    Runs sync_swim.py with GARMIN_SESSION, GDRIVE_CREDENTIALS, and GDRIVE_FOLDER_ID passed as environment variables from repository secrets.
    Sets permissions: contents: read (least-privilege for GITHUB_TOKEN).

**generate_garmin_session.py**

Helper for first-time setup:

    Log in once locally: `python -c "import garth; garth.login('EMAIL','PASS'); garth.save('.garth')"`
    Run `python generate_garmin_session.py` (optionally --garth-dir <path>).
    Copy the printed base64 string into your repo's GARMIN_SESSION secret.
