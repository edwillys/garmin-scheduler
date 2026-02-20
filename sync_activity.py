#!/usr/bin/env python3
"""Sync Garmin activities to Google Drive."""

import base64
import hashlib
import io
import json
import os
import tempfile
import tomllib
from pathlib import Path

import garth
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload


def load_dotenv(dotenv_path: Path) -> dict[str, str]:
    """Load a simple .env file into a dictionary."""
    values: dict[str, str] = {}
    if not dotenv_path.exists():
        return values

    with dotenv_path.open("r", encoding="utf-8") as file_obj:
        for raw_line in file_obj:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if value and value[0] in {'"', "'"} and value[-1] == value[0]:
                value = value[1:-1]

            values[key] = value

    return values


DOTENV_VALUES = load_dotenv(Path(__file__).with_name(".env"))


def load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


SYNC_SETTINGS = load_toml(Path(__file__).with_name("sync_settings.toml"))


def get_config(name: str, default: str | None = None) -> str:
    """Read config from .env first, then os.environ, then default."""
    if name in DOTENV_VALUES:
        return DOTENV_VALUES[name]
    if name in os.environ:
        return os.environ[name]
    if default is not None:
        return default
    raise KeyError(name)


GDRIVE_FOLDER_ID = get_config("GDRIVE_FOLDER_ID")
GDRIVE_CREDENTIALS = get_config("GDRIVE_CREDENTIALS")
GARMIN_SESSION = get_config("GARMIN_SESSION")
GDRIVE_TOKEN_JSON = DOTENV_VALUES.get("GDRIVE_TOKEN_JSON") or os.environ.get(
    "GDRIVE_TOKEN_JSON"
)

GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def get_sync_setting(name: str, default):
    sync = SYNC_SETTINGS.get("sync", {})
    return sync.get(name, default)


ACTIVITY_TYPE_KEY = str(get_sync_setting("activity_type", "lap_swimming"))
NUM_LAST_ACTIVITIES = int(get_sync_setting("num_last_activities", 5))
DETAIL_LEVEL = str(get_sync_setting("detail_level", "summary")).lower().strip()


def garmin_authenticate() -> None:
    """Resume a Garmin session from the GARMIN_SESSION env variable."""
    session_bytes = base64.b64decode(GARMIN_SESSION)
    with tempfile.TemporaryDirectory() as tmpdir:
        garth_dir = os.path.join(tmpdir, ".garth")
        os.makedirs(garth_dir)
        # garth stores tokens as individual files; the env var packs a tar archive
        tar_path = os.path.join(tmpdir, "session.tar")
        with open(tar_path, "wb") as f:
            f.write(session_bytes)
        import tarfile

        with tarfile.open(tar_path) as tar:
            members = tar.getmembers()
            for member in members:
                # Prevent path traversal: reject absolute paths and ".." components
                if os.path.isabs(member.name) or ".." in member.name.split("/"):
                    raise ValueError(f"Unsafe path in session archive: {member.name}")
            tar.extractall(garth_dir, members=members)
        garth.resume(garth_dir)


def fetch_activities(activity_type_key: str) -> list[dict]:
    """Fetch the last NUM_LAST_ACTIVITIES activities and filter by type key."""
    activities = garth.connectapi(
        "/activitylist-service/activities/search/activities",
        params={"limit": NUM_LAST_ACTIVITIES, "start": 0},
    )
    return [
        a
        for a in activities
        if a.get("activityType", {}).get("typeKey") == activity_type_key
    ]


def activity_filename(activity: dict) -> str:
    """Return the Drive filename for the activity."""
    activity_id = activity["activityId"]
    start_time = activity.get("startTimeLocal", "unknown")
    date_part = start_time[:10] if len(start_time) >= 10 else start_time

    # Backward-compat: keep the old prefix for lap swimming.
    activity_type_key = (
        activity.get("activityType", {}).get("typeKey") or ACTIVITY_TYPE_KEY
    )
    if activity_type_key == "lap_swimming":
        prefix = "lap_swim"
    else:
        prefix = "activity_" + "".join(
            ch if (ch.isalnum() or ch in "-_ ") else "_"
            for ch in str(activity_type_key)
        ).strip().replace(" ", "_")

    return f"{prefix}_{date_part}_{activity_id}.json"


def compute_checksum(data: bytes) -> str:
    """Return a SHA-256 hex digest for the given bytes."""
    return hashlib.sha256(data).hexdigest()


def gdrive_service():  # returns googleapiclient.discovery.Resource
    """Build and return an authenticated Google Drive service."""
    credentials_info = json.loads(GDRIVE_CREDENTIALS)

    if credentials_info.get("type") == "service_account":
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info, scopes=GDRIVE_SCOPES
        )
    else:
        credentials: Credentials | None = None

        if GDRIVE_TOKEN_JSON:
            credentials = Credentials.from_authorized_user_info(
                json.loads(GDRIVE_TOKEN_JSON), scopes=GDRIVE_SCOPES
            )

        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                try:
                    from google_auth_oauthlib.flow import InstalledAppFlow
                except ImportError as exc:
                    raise RuntimeError(
                        "No valid/refreshable OAuth token found in GDRIVE_TOKEN_JSON, "
                        "and interactive login is required, but google-auth-oauthlib is not installed. "
                        "Install it with: pip install google-auth-oauthlib"
                    ) from exc

                if (
                    "installed" not in credentials_info
                    and "web" not in credentials_info
                ):
                    raise ValueError(
                        "Unrecognized OAuth client JSON. Expected top-level key 'installed' or 'web'."
                    )
                flow = InstalledAppFlow.from_client_config(
                    credentials_info, GDRIVE_SCOPES
                )
                credentials = flow.run_local_server(port=0)

                print("\nOAuth token generated.")
                print(
                    "Set GDRIVE_TOKEN_JSON to the following value (keep it secret):\n"
                    + credentials.to_json()
                )

    return build("drive", "v3", credentials=credentials)


def list_drive_files(service) -> dict[str, dict]:
    """Return a mapping of filename -> file metadata for files in the folder."""
    files: dict[str, dict] = {}
    page_token = None
    query = f"'{GDRIVE_FOLDER_ID}' in parents and trashed = false"
    while True:
        response = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name, description)",
                pageToken=page_token,
            )
            .execute()
        )
        for item in response.get("files", []):
            files[item["name"]] = item
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def upload_file(service, file_bytes: bytes, filename: str, checksum: str) -> None:
    """Upload a file to Google Drive, setting checksum in description."""
    file_metadata = {
        "name": filename,
        "parents": [GDRIVE_FOLDER_ID],
        "description": checksum,
    }
    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype="application/json",
        resumable=False,
    )
    service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    print(f"Uploaded: {filename}")


def update_file(
    service, file_id: str, file_bytes: bytes, filename: str, checksum: str
) -> None:
    """Update an existing file on Google Drive."""
    file_metadata = {"description": checksum}
    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype="application/json",
        resumable=False,
    )
    service.files().update(
        fileId=file_id,
        body=file_metadata,
        media_body=media,
        fields="id",
    ).execute()
    print(f"Updated: {filename}")


def explain_drive_quota_error(err: HttpError) -> None:
    """Print a clearer action message for service-account Drive quota errors."""
    details = ""
    if hasattr(err, "error_details") and err.error_details:
        details = json.dumps(err.error_details)
    message = str(err)
    if (
        "Service Accounts do not have storage quota" in message
        or "storageQuotaExceeded" in details
    ):
        print(
            "Google Drive rejected the upload: service accounts have no My Drive quota."
        )
        print(
            "Sharing a My Drive folder with the service account grants permission, not storage quota."
        )
        print("Use one of these options:")
        print(
            "  1) Upload to a Shared Drive folder where this service account is a member."
        )
        print("  2) Switch to OAuth user credentials for My Drive uploads.")


def fetch_activity_json(activity_id: int | str) -> bytes:
    """Fetch JSON data for a single activity.

    detail levels:
      - summary: /activity-service/activity/{id}
      - detailed: adds /activity-service/activity/{id}/details when available
    """

    summary = garth.connectapi(f"/activity-service/activity/{activity_id}")

    if DETAIL_LEVEL == "summary":
        return json.dumps(summary, indent=2).encode()

    payload: dict[str, object] = {"summary": summary}

    def try_fetch(name: str, path: str, params: dict | None = None) -> None:
        try:
            payload[name] = garth.connectapi(path, params=params)
        except Exception:
            return

    # This endpoint typically includes laps/splits/metrics depending on activity type.
    try_fetch("details", f"/activity-service/activity/{activity_id}/details")

    return json.dumps(payload, indent=2).encode()


def main() -> None:
    print("Authenticating with Garmin Connect...")
    garmin_authenticate()

    print(f"Fetching last {NUM_LAST_ACTIVITIES} activities...")
    activities = fetch_activities(ACTIVITY_TYPE_KEY)
    if not activities:
        print(f"No activities found for type: {ACTIVITY_TYPE_KEY}")
        return
    print(f"Found {len(activities)} activity(ies) of type {ACTIVITY_TYPE_KEY}.")

    print("Connecting to Google Drive...")
    service = gdrive_service()
    drive_files = list_drive_files(service)

    try:
        for activity in activities:
            filename = activity_filename(activity)
            activity_id = activity["activityId"]

            print(f"Processing activity {activity_id} -> {filename}")
            activity_data = fetch_activity_json(activity_id)
            checksum = compute_checksum(activity_data)

            if filename in drive_files:
                existing = drive_files[filename]
                if existing.get("description") == checksum:
                    print(f"  Skipping (unchanged): {filename}")
                    continue
                update_file(service, existing["id"], activity_data, filename, checksum)
            else:
                upload_file(service, activity_data, filename, checksum)
    except HttpError as err:
        explain_drive_quota_error(err)
        raise

    print("Sync complete.")


if __name__ == "__main__":
    main()
