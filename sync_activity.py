#!/usr/bin/env python3
"""Sync Garmin activities to Google Drive."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import garth
from googleapiclient.errors import HttpError

from utils import (
    compute_checksum,
    explain_drive_quota_error,
    garmin_authenticate,
    gdrive_service,
    get_config,
    get_toml_setting,
    list_drive_files,
    load_toml,
    update_drive_json,
    upload_drive_json,
)

SYNC_SETTINGS = load_toml(Path(__file__).with_name("sync_settings.toml"))


def get_sync_setting(name: str, default):
    return get_toml_setting(SYNC_SETTINGS, "sync", name, default)


ACTIVITY_TYPE_KEY = str(get_sync_setting("activity_type", "lap_swimming"))
NUM_LAST_ACTIVITIES = int(get_sync_setting("num_last_activities", 5))
DETAIL_LEVEL = str(get_sync_setting("detail_level", "summary")).lower().strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list",
        action="store_true",
        help=(
            "List matching activities from Garmin Connect and exit. "
            "This mode does not touch Google Drive."
        ),
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help=(
            "Filter activities by local start date (inclusive), YYYY-MM-DD. "
            "Applies to both --list and sync mode."
        ),
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help=(
            "Filter activities by local start date (inclusive), YYYY-MM-DD. "
            "Applies to both --list and sync mode."
        ),
    )
    return parser.parse_args(argv)


def _parse_iso_date(value: str, *, flag_name: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"{flag_name} must be YYYY-MM-DD, got: {value!r}") from exc


def _activity_local_date(activity: dict) -> dt.date | None:
    start_time = activity.get("startTimeLocal")
    if not isinstance(start_time, str) or len(start_time) < 10:
        return None
    try:
        return dt.date.fromisoformat(start_time[:10])
    except ValueError:
        return None


def _filter_activities_by_date(
    activities: list[dict],
    *,
    start_date: dt.date | None,
    end_date: dt.date | None,
) -> list[dict]:
    if start_date is None and end_date is None:
        return activities

    filtered: list[dict] = []
    for act in activities:
        d = _activity_local_date(act)
        if d is None:
            continue
        if start_date is not None and d < start_date:
            continue
        if end_date is not None and d > end_date:
            continue
        filtered.append(act)
    return filtered


def fetch_recent_activities(activity_type_key: str, *, limit: int) -> list[dict]:
    """Fetch the most recent activities (best-effort) and filter by type key."""
    activities = garth.connectapi(
        "/activitylist-service/activities/search/activities",
        params={"limit": limit, "start": 0},
    )
    return [
        a
        for a in activities
        if a.get("activityType", {}).get("typeKey") == activity_type_key
    ]


def fetch_activities_in_range(
    activity_type_key: str,
    *,
    start_date: dt.date | None,
    end_date: dt.date | None,
    page_size: int = 100,
    max_pages: int = 50,
) -> list[dict]:
    """Fetch activities from Garmin (paged) and filter to a local date range.

    Garmin's search endpoint returns activities in reverse-chronological order.
    We page until we have passed the start_date (when provided) or until max_pages.
    """

    start = 0
    collected: list[dict] = []
    oldest_seen: dt.date | None = None

    for _ in range(max_pages):
        page = garth.connectapi(
            "/activitylist-service/activities/search/activities",
            params={"limit": page_size, "start": start},
        )
        if not page:
            break

        typed = [
            a
            for a in page
            if a.get("activityType", {}).get("typeKey") == activity_type_key
        ]
        typed = _filter_activities_by_date(
            typed, start_date=start_date, end_date=end_date
        )
        collected.extend(typed)

        for act in page:
            d = _activity_local_date(act)
            if d is None:
                continue
            if oldest_seen is None or d < oldest_seen:
                oldest_seen = d

        # Stop when paging would only return items older than the requested range.
        if (
            start_date is not None
            and oldest_seen is not None
            and oldest_seen < start_date
        ):
            break

        start += page_size

    # Keep stable, reverse-chronological-ish output: rely on Garmin ordering.
    return collected


def activity_filename(activity: dict) -> str:
    """Return the Drive filename for the activity."""
    activity_id = activity["activityId"]
    start_time = activity.get("startTimeLocal", "unknown")
    date_part = start_time[:10] if len(start_time) >= 10 else start_time

    # Backward-compat: keep the old prefix for lap swimming.
    activity_type_key = (
        activity.get("activityType", {}).get("typeKey") or ACTIVITY_TYPE_KEY
    )

    return f"{activity_type_key}_{date_part}_{activity_id}.json"


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


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    start_date = (
        _parse_iso_date(args.start_date, flag_name="--start-date")
        if args.start_date
        else None
    )
    end_date = (
        _parse_iso_date(args.end_date, flag_name="--end-date")
        if args.end_date
        else None
    )
    if start_date is not None and end_date is not None and start_date > end_date:
        raise SystemExit("--start-date must be <= --end-date")

    print("Authenticating with Garmin Connect...")
    garmin_authenticate()

    if args.list:
        if start_date is None and end_date is None:
            activities = fetch_recent_activities(
                ACTIVITY_TYPE_KEY, limit=NUM_LAST_ACTIVITIES
            )
        else:
            activities = fetch_activities_in_range(
                ACTIVITY_TYPE_KEY,
                start_date=start_date,
                end_date=end_date,
            )

        if not activities:
            print(f"No activities found for type: {ACTIVITY_TYPE_KEY}")
            return

        print(f"Found {len(activities)} activity(ies) of type {ACTIVITY_TYPE_KEY}.")
        for a in activities:
            activity_id = a.get("activityId")
            when = a.get("startTimeLocal")
            name = a.get("activityName")
            print(f"{activity_id}\t{when}\t{name}")
        return

    gdrive_folder_id = get_config("GDRIVE_FOLDER_ID_SWIMMING_DATA", default="")
    if not gdrive_folder_id:
        raise SystemExit(
            "Missing Drive folder id. Set GDRIVE_FOLDER_ID_SWIMMING_DATA. "
            "(This is not required for --list.)"
        )

    if start_date is None and end_date is None:
        print(f"Fetching last {NUM_LAST_ACTIVITIES} activities...")
        activities = fetch_recent_activities(
            ACTIVITY_TYPE_KEY, limit=NUM_LAST_ACTIVITIES
        )
    else:
        print("Fetching activities in the requested date range...")
        activities = fetch_activities_in_range(
            ACTIVITY_TYPE_KEY,
            start_date=start_date,
            end_date=end_date,
        )

    if not activities:
        print(f"No activities found for type: {ACTIVITY_TYPE_KEY}")
        return
    print(f"Found {len(activities)} activity(ies) of type {ACTIVITY_TYPE_KEY}.")

    print("Connecting to Google Drive...")
    service = gdrive_service(allow_interactive=True)
    drive_files = list_drive_files(
        service, gdrive_folder_id, fields="id, name, description"
    )

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
                update_drive_json(
                    service,
                    file_id=existing["id"],
                    file_bytes=activity_data,
                    filename=filename,
                    checksum=checksum,
                )
                print(f"Updated: {filename}")
            else:
                upload_drive_json(
                    service,
                    folder_id=gdrive_folder_id,
                    file_bytes=activity_data,
                    filename=filename,
                    checksum=checksum,
                )
                print(f"Uploaded: {filename}")
    except HttpError as err:
        explain_drive_quota_error(err)
        raise

    print("Sync complete.")


if __name__ == "__main__":
    main()
