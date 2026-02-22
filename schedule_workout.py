#!/usr/bin/env python3
"""Schedule an existing Garmin workout onto your Garmin Connect calendar.

This uses your existing `GARMIN_SESSION` (a base64-encoded tar of a `.garth` session)
just like `sync_activity.py`.

Examples:
  python schedule_workout.py --list
  python schedule_workout.py --workout-name "ga_30min" --date 2026-02-22
  python schedule_workout.py --workout-id 1234567890 --date 2026-02-22

Notes:
- This schedules an *existing* workout (by `workoutId`). It does not create workouts.
- Garmin endpoints can vary by account/region and may change over time.
"""

from __future__ import annotations

import argparse
import datetime as dt
from typing import Any

from utils import garmin_authenticate, garmin_list_workouts, garmin_schedule_workout


def pick_workout_id(workouts: list[dict[str, Any]], workout_name: str) -> int:
    """Find a workoutId by exact workoutName match (case-insensitive)."""
    normalized = workout_name.casefold().strip()
    matches = [
        w
        for w in workouts
        if isinstance(w, dict)
        and str(w.get("workoutName", "")).casefold().strip() == normalized
    ]

    if not matches:
        available = sorted(
            {
                str(w.get("workoutName"))
                for w in workouts
                if isinstance(w, dict) and w.get("workoutName")
            }
        )
        sample = "\n".join(f"- {name}" for name in available[:25])
        more = "" if len(available) <= 25 else f"\n... ({len(available) - 25} more)"
        raise SystemExit(
            "Workout not found by name. "
            "Use --list to see available workouts.\n\n"
            f"First matches in your account:\n{sample}{more}"
        )

    if len(matches) > 1:
        ids = ", ".join(str(w.get("workoutId")) for w in matches)
        raise SystemExit(
            f"Multiple workouts match name {workout_name!r}. Matching IDs: {ids}. "
            "Please use --workout-id."
        )

    workout_id = matches[0].get("workoutId")
    if not isinstance(workout_id, int):
        raise TypeError(f"Unexpected workoutId type: {type(workout_id)!r}")
    return workout_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List workouts and exit")
    group.add_argument("--workout-id", type=int, help="Workout ID to schedule")
    group.add_argument(
        "--workout-name",
        type=str,
        help="Workout name to schedule (exact match; case-insensitive)",
    )

    parser.add_argument(
        "--date",
        type=str,
        help="Date to schedule the workout (YYYY-MM-DD). Required unless --list.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    garmin_authenticate()

    if args.list:
        workouts = garmin_list_workouts()
        for w in workouts:
            name = w.get("workoutName")
            wid = w.get("workoutId")
            print(f"{wid}\t{name}")
        return

    if not args.date:
        raise SystemExit("--date is required when scheduling.")

    try:
        schedule_date = dt.date.fromisoformat(args.date)
    except ValueError as exc:
        raise SystemExit("Invalid --date. Expected YYYY-MM-DD.") from exc

    if args.workout_id is not None:
        workout_id = args.workout_id
    else:
        workouts = garmin_list_workouts()
        workout_id = pick_workout_id(workouts, args.workout_name)

    try:
        res = garmin_schedule_workout(workout_id, schedule_date.strftime("%Y-%m-%d"))
    except Exception as exc:
        raise SystemExit(
            "Scheduling call failed. "
            "Your account/region may not expose this endpoint, or the session may lack scopes.\n"
            "If you consistently see 404/405, try generating a fresh .garth session by logging in again.\n\n"
            f"Error: {exc!r}"
        ) from exc

    print(res)

    if isinstance(res, dict) and "workoutScheduleId" in res:
        print(f"Scheduled successfully (workoutScheduleId={res['workoutScheduleId']}).")
    else:
        raise SystemExit(
            "Schedule request completed but did not return workoutScheduleId. "
            "Garmin may have changed the response format."
        )


if __name__ == "__main__":
    main()
