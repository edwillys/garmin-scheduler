"""Shared helpers for this repo.

Centralizes:
- dotenv-style env loading
- Garmin session restore (GARMIN_SESSION)
- Google Drive auth + common file operations
- small TOML/config helpers

This keeps the CLI scripts focused on their specific workflows.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import os
import tarfile
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import garth
from garth.exc import GarthHTTPError


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


def get_config(name: str, default: str | None = None) -> str:
    """Read config from .env first, then os.environ, then default."""
    if name in DOTENV_VALUES:
        return DOTENV_VALUES[name]
    if name in os.environ:
        return os.environ[name]
    if default is not None:
        return default
    raise KeyError(name)


def load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def get_toml_setting(cfg: dict[str, Any], section: str, name: str, default: Any) -> Any:
    section_value = cfg.get(section, {})
    if not isinstance(section_value, dict):
        return default
    return section_value.get(name, default)


def compute_checksum(data: bytes) -> str:
    """Return a SHA-256 hex digest for the given bytes."""
    return hashlib.sha256(data).hexdigest()


def garmin_authenticate() -> None:
    """Resume a Garmin session from the GARMIN_SESSION env variable."""
    session_b64 = get_config("GARMIN_SESSION")
    session_bytes = base64.b64decode(session_b64)

    with tempfile.TemporaryDirectory() as tmpdir:
        garth_dir = os.path.join(tmpdir, ".garth")
        os.makedirs(garth_dir)

        tar_path = os.path.join(tmpdir, "session.tar")
        with open(tar_path, "wb") as file_obj:
            file_obj.write(session_bytes)

        with tarfile.open(tar_path) as tar:
            members = tar.getmembers()
            for member in members:
                if os.path.isabs(member.name) or ".." in member.name.split("/"):
                    raise ValueError(f"Unsafe path in session archive: {member.name}")
            tar.extractall(garth_dir, members=members)

        garth.resume(garth_dir)


def garmin_list_workouts() -> list[dict[str, Any]]:
    workouts = garth.connectapi(
        "/workout-service/workouts",
        params={
            "start": 1,
            "limit": 999,
            "myWorkoutsOnly": True,
            "sharedWorkoutsOnly": False,
            "orderBy": "WORKOUT_NAME",
            "orderSeq": "ASC",
            "includeAtp": False,
        },
    )
    if not isinstance(workouts, list):
        raise TypeError(f"Unexpected workouts response type: {type(workouts)!r}")
    return workouts


def garmin_delete_workout(workout_id: int) -> None:
    garth.connectapi(f"/workout-service/workout/{workout_id}", method="DELETE")


def garmin_import_workout(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return garth.connectapi(
            "/workout-service/workout",
            method="POST",
            headers={"Content-Type": "application/json"},
            json=payload,
        )
    except GarthHTTPError as exc:
        resp = getattr(exc.error, "response", None)
        if resp is None:
            raise

        status = getattr(resp, "status_code", "?")
        body = getattr(resp, "text", "")
        body = (body or "").strip()
        tail = body if len(body) <= 1500 else body[:1500] + "..."
        raise SystemExit(
            f"Garmin workout create failed: HTTP {status}. Response: {tail or '<empty>'}"
        ) from exc


def garmin_schedule_workout(workout_id: int, date_iso: str) -> dict[str, Any]:
    """Schedule a workout by id on a specific date (YYYY-MM-DD)."""
    return garth.connectapi(
        f"/workout-service/schedule/{workout_id}",
        method="POST",
        headers={"Content-Type": "application/json"},
        json={"date": date_iso},
    )


def _garmin_get_all_day_events(date_iso: str) -> Any:
    """Return Garmin Connect daily events for a date.

    This endpoint is known to exist across accounts/regions and is used as a
    fallback for listing scheduled workouts.
    """

    return garth.connectapi(
        "/wellness-service/wellness/dailyEvents",
        params={"calendarDate": date_iso},
    )


def _walk_json_objects(root: Any, *, max_nodes: int = 5_000) -> list[dict[str, Any]]:
    """Return all dict objects reachable from a JSON-ish structure."""

    out: list[dict[str, Any]] = []
    stack: list[Any] = [root]
    seen = 0
    while stack and seen < max_nodes:
        cur = stack.pop()
        seen += 1

        if isinstance(cur, dict):
            out.append(cur)
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)

    return out


def _normalize_scheduled_workout_item(
    item: dict[str, Any], *, fallback_date: str
) -> dict[str, Any]:
    date_iso = (
        item.get("date")
        or item.get("workoutDate")
        or item.get("calendarDate")
        or fallback_date
    )

    workout_id = item.get("workoutId")
    if workout_id is None:
        workout = item.get("workout")
        if isinstance(workout, dict):
            workout_id = workout.get("workoutId")

    workout_name = item.get("workoutName")
    if workout_name is None:
        workout = item.get("workout")
        if isinstance(workout, dict):
            workout_name = workout.get("workoutName")

    workout_schedule_id = item.get("workoutScheduleId") or item.get(
        "scheduledWorkoutId"
    )

    return {
        **item,
        "date": date_iso,
        "workoutId": workout_id,
        "workoutName": workout_name,
        "workoutScheduleId": workout_schedule_id,
    }


def garmin_list_scheduled_workouts(
    *, start_date_iso: str, end_date_iso: str
) -> list[dict[str, Any]]:
    """List scheduled workouts in a date range.

    Garmin's public/undocumented endpoints vary.

    Strategy:
    1) Try a small set of possible "range" endpoints.
    2) If those fail, fall back to scanning daily events
       ("/wellness-service/wellness/dailyEvents?calendarDate=YYYY-MM-DD") and
       extracting scheduled-workout items.
    """

    candidates = [
        "/workout-service/scheduledWorkouts",
        "/workout-service/scheduled-workouts",
        "/workout-service/schedule",
    ]

    last_exc: Exception | None = None
    for endpoint in candidates:
        try:
            res = garth.connectapi(
                endpoint,
                params={"startDate": start_date_iso, "endDate": end_date_iso},
            )
        except Exception as exc:  # pragma: no cover
            last_exc = exc
            continue

        if isinstance(res, list):
            return [item for item in res if isinstance(item, dict)]

        if isinstance(res, dict):
            for key in ("scheduledWorkouts", "workoutSchedules", "items"):
                items = res.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]

    # Fallback: scan daily events for each date.
    start_date = dt.date.fromisoformat(start_date_iso)
    end_date = dt.date.fromisoformat(end_date_iso)
    if start_date > end_date:
        raise ValueError("start_date_iso must be <= end_date_iso")

    found: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, Any, Any]] = set()
    fetched_any = False

    current = start_date
    while current <= end_date:
        day_iso = current.isoformat()
        try:
            res = _garmin_get_all_day_events(day_iso)
        except Exception as exc:  # pragma: no cover
            last_exc = exc
            current += dt.timedelta(days=1)
            continue

        fetched_any = True

        for obj in _walk_json_objects(res):
            # The schedule service exposes a schedule id; if we see one, hydrate it.
            schedule_id = obj.get("workoutScheduleId") or obj.get("scheduledWorkoutId")
            if schedule_id is not None:
                try:
                    hydrated = garth.connectapi(
                        f"/workout-service/schedule/{schedule_id}"
                    )
                except Exception:  # pragma: no cover
                    hydrated = None

                if isinstance(hydrated, dict):
                    normalized = _normalize_scheduled_workout_item(
                        hydrated, fallback_date=day_iso
                    )
                    key = (
                        normalized.get("date"),
                        normalized.get("workoutScheduleId"),
                        normalized.get("workoutId"),
                    )
                    if key not in seen_keys:
                        seen_keys.add(key)
                        found.append(normalized)
                continue

            # Otherwise, keep any dict that looks like a scheduled workout entry.
            if "workoutId" in obj or "workoutName" in obj:
                normalized = _normalize_scheduled_workout_item(
                    obj, fallback_date=day_iso
                )
                key = (
                    normalized.get("date"),
                    normalized.get("workoutScheduleId"),
                    normalized.get("workoutId"),
                )
                if key not in seen_keys:
                    seen_keys.add(key)
                    found.append(normalized)

        current += dt.timedelta(days=1)

    if found:
        # Sort for stable output.
        def _sort_key(item: dict[str, Any]) -> tuple[str, str]:
            date_val = str(item.get("date") or "")
            name_val = str(item.get("workoutName") or "")
            return (date_val, name_val)

        return sorted(found, key=_sort_key)

    if fetched_any:
        return []

    msg = (
        "Could not list scheduled workouts via Garmin Connect API. "
        "Your account/region may not expose this endpoint or Garmin may have changed it."
    )
    if last_exc is None:
        raise RuntimeError(msg)
    raise RuntimeError(msg) from last_exc


def gdrive_service(*, scopes: list[str] | None = None, allow_interactive: bool) -> Any:
    """Build and return an authenticated Google Drive service."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    gdrive_scopes = scopes or ["https://www.googleapis.com/auth/drive.file"]

    credentials_info = json.loads(get_config("GDRIVE_CREDENTIALS"))
    gdrive_token_json = DOTENV_VALUES.get("GDRIVE_TOKEN_JSON") or os.environ.get(
        "GDRIVE_TOKEN_JSON"
    )

    if credentials_info.get("type") == "service_account":
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info, scopes=gdrive_scopes
        )
    else:
        credentials: Credentials | None = None

        if gdrive_token_json:
            credentials = Credentials.from_authorized_user_info(
                json.loads(gdrive_token_json), scopes=gdrive_scopes
            )

        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                if not allow_interactive:
                    raise RuntimeError(
                        "No valid/refreshable OAuth token found in GDRIVE_TOKEN_JSON. "
                        "Run sync_activity.py once locally to bootstrap OAuth and set GDRIVE_TOKEN_JSON."
                    )

                try:
                    from google_auth_oauthlib.flow import InstalledAppFlow
                except ImportError as exc:
                    raise RuntimeError(
                        "Interactive OAuth is required but google-auth-oauthlib is not installed. "
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
                    credentials_info, gdrive_scopes
                )
                credentials = flow.run_local_server(port=0)

                print("\nOAuth token generated.")
                print(
                    "Set GDRIVE_TOKEN_JSON to the following value (keep it secret):\n"
                    + credentials.to_json()
                )

    return build("drive", "v3", credentials=credentials)


def list_drive_files(
    service: Any, folder_id: str, *, fields: str = "id, name, mimeType, description"
) -> dict[str, dict[str, Any]]:
    """Return a mapping of filename -> file metadata for files in the folder."""
    files: dict[str, dict[str, Any]] = {}
    page_token = None
    query = f"'{folder_id}' in parents and trashed = false"
    while True:
        response = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields=f"nextPageToken, files({fields})",
                pageToken=page_token,
            )
            .execute()
        )
        for item in response.get("files", []):
            name = item.get("name")
            if name:
                files[name] = item
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def download_drive_file_bytes(service: Any, file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()


def upload_drive_json(
    service: Any,
    *,
    folder_id: str,
    file_bytes: bytes,
    filename: str,
    checksum: str,
) -> None:
    from googleapiclient.http import MediaIoBaseUpload

    file_metadata = {
        "name": filename,
        "parents": [folder_id],
        "description": checksum,
    }
    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype="application/json",
        resumable=False,
    )
    service.files().create(body=file_metadata, media_body=media, fields="id").execute()


def update_drive_json(
    service: Any,
    *,
    file_id: str,
    file_bytes: bytes,
    filename: str,
    checksum: str,
) -> None:
    from googleapiclient.http import MediaIoBaseUpload

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


def explain_drive_quota_error(err: Any) -> None:
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
