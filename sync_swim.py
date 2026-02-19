#!/usr/bin/env python3
"""Sync Garmin swimming activities to Google Drive."""

import base64
import hashlib
import json
import os
import tempfile

import garth
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

NUM_LAST_ACTIVITIES = int(os.environ.get("NUM_LAST_ACTIVITIES", "30"))
GDRIVE_FOLDER_ID = os.environ["GDRIVE_FOLDER_ID"]
GDRIVE_CREDENTIALS = os.environ["GDRIVE_CREDENTIALS"]
GARMIN_SESSION = os.environ["GARMIN_SESSION"]

GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


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


def fetch_swim_activities() -> list[dict]:
    """Fetch the last NUM_LAST_ACTIVITIES activities and filter for swimming."""
    activities = garth.connectapi(
        "/activitylist-service/activities/search/activities",
        params={"limit": NUM_LAST_ACTIVITIES, "start": 0},
    )
    return [
        a
        for a in activities
        if a.get("activityType", {}).get("typeKey") == "swimming"
    ]


def activity_filename(activity: dict) -> str:
    """Return the Drive filename for the activity."""
    activity_id = activity["activityId"]
    start_time = activity.get("startTimeLocal", "unknown")
    date_part = start_time[:10] if len(start_time) >= 10 else start_time
    return f"swim_{date_part}_{activity_id}.json"


def compute_checksum(data: bytes) -> str:
    """Return a SHA-256 hex digest for the given bytes."""
    return hashlib.sha256(data).hexdigest()


def gdrive_service():  # returns googleapiclient.discovery.Resource
    """Build and return an authenticated Google Drive service."""
    credentials_info = json.loads(GDRIVE_CREDENTIALS)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info, scopes=GDRIVE_SCOPES
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


def upload_file(service, local_path: str, filename: str, checksum: str) -> None:
    """Upload a file to Google Drive, setting checksum in description."""
    file_metadata = {
        "name": filename,
        "parents": [GDRIVE_FOLDER_ID],
        "description": checksum,
    }
    media = MediaFileUpload(local_path, mimetype="application/json")
    service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    print(f"Uploaded: {filename}")


def update_file(
    service, file_id: str, local_path: str, filename: str, checksum: str
) -> None:
    """Update an existing file on Google Drive."""
    file_metadata = {"description": checksum}
    media = MediaFileUpload(local_path, mimetype="application/json")
    service.files().update(
        fileId=file_id,
        body=file_metadata,
        media_body=media,
        fields="id",
    ).execute()
    print(f"Updated: {filename}")


def fetch_activity_json(activity_id: int | str) -> bytes:
    """Fetch the full JSON data for a single activity."""
    data = garth.connectapi(f"/activity-service/activity/{activity_id}")
    return json.dumps(data, indent=2).encode()


def main() -> None:
    print("Authenticating with Garmin Connect...")
    garmin_authenticate()

    print(f"Fetching last {NUM_LAST_ACTIVITIES} activities...")
    swim_activities = fetch_swim_activities()
    if not swim_activities:
        print("No swimming activities found.")
        return
    print(f"Found {len(swim_activities)} swimming activity(ies).")

    print("Connecting to Google Drive...")
    service = gdrive_service()
    drive_files = list_drive_files(service)

    with tempfile.TemporaryDirectory() as tmpdir:
        for activity in swim_activities:
            filename = activity_filename(activity)
            activity_id = activity["activityId"]

            print(f"Processing activity {activity_id} -> {filename}")
            activity_data = fetch_activity_json(activity_id)
            checksum = compute_checksum(activity_data)

            local_path = os.path.join(tmpdir, filename)
            with open(local_path, "wb") as f:
                f.write(activity_data)

            if filename in drive_files:
                existing = drive_files[filename]
                if existing.get("description") == checksum:
                    print(f"  Skipping (unchanged): {filename}")
                    continue
                update_file(service, existing["id"], local_path, filename, checksum)
            else:
                upload_file(service, local_path, filename, checksum)

    print("Sync complete.")


if __name__ == "__main__":
    main()
