#!/usr/bin/env python3
"""Create and schedule Garmin workouts from a YAML plan.

This script is intentionally modeled after the workflow from:
https://github.com/yeekang-0311/garmin_planner

High-level behavior:
1) Authenticate to Garmin using `GARMIN_SESSION` (same approach as sync_activity.py).
2) Parse a workout plan YAML file containing:
   - definitions (optional)
   - workouts (workout creation)
   - schedulePlan (calendar scheduling)
3) Optionally delete existing workouts with the same name.
4) Create workouts via: POST /workout-service/workout
5) Schedule workouts via: POST /workout-service/schedule/{workoutId} with {"date": "YYYY-MM-DD"}

Usage:
    # Sync from Drive (default when no plan is provided)
    python -m garmin_scheduler.sync_workouts

    # Sync from a local YAML file
    python -m garmin_scheduler.sync_workouts workout_plan_swim.yaml

    # List workouts
    python -m garmin_scheduler.sync_workouts --list              # from Garmin Connect
    python -m garmin_scheduler.sync_workouts --list --from-drive # from Drive YAML plans
    python -m garmin_scheduler.sync_workouts workout_plan_swim.yaml --list

    # List schedules
    python -m garmin_scheduler.sync_workouts --list-schedule              # from Garmin Connect
    python -m garmin_scheduler.sync_workouts --list-schedule --from-drive # from Drive YAML plans
    python -m garmin_scheduler.sync_workouts workout_plan_swim.yaml --list-schedule

Env vars (same as other scripts in this repo):
- GARMIN_SESSION (required)

Optional config:
- sync_settings.toml [workouts].deleteSameNameWorkout
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow running as a script: `python src/garmin_scheduler/sync_workouts.py ...`
# (relative imports require a package context).
_SRC_DIR = Path(__file__).resolve().parents[1]
if __package__ in (None, "") and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from garmin_scheduler.utils import (  # noqa: E402
    download_drive_file_bytes,
    garmin_authenticate,
    garmin_delete_workout,
    garmin_import_workout,
    garmin_list_scheduled_workouts,
    garmin_list_workouts,
    garmin_schedule_workout,
    gdrive_service,
    get_config,
    get_toml_setting,
    list_drive_files,
    load_toml,
)
from garmin_scheduler.workout_builder import (  # noqa: E402
    build_workout_payload,
    parse_pool_settings,
    parse_sport_settings,
)

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: PyYAML. Install with: pip install pyyaml\n"
        "(It is included in this project's dependencies if installed via pyproject.toml.)"
    ) from exc

DATE_FORMAT = "%Y-%m-%d"

WEEKDAY_INDEX: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


_SYNC_SETTINGS_PATH = Path("sync_settings.toml")
if not _SYNC_SETTINGS_PATH.exists():
    _SYNC_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "sync_settings.toml"
SYNC_SETTINGS = load_toml(_SYNC_SETTINGS_PATH)


@dataclass(frozen=True)
class ScheduledDay:
    date: dt.date
    names: list[str]
    source_label: str


def normalize_weekday(name: str) -> int | None:
    """Return weekday index (Mon=0..Sun=6) for common weekday strings."""
    s = name.strip().lower()
    if s in WEEKDAY_INDEX:
        return WEEKDAY_INDEX[s]
    if len(s) >= 3:
        # accept 3-letter abbreviations (mon, tue, wed, ...)
        for full, idx in WEEKDAY_INDEX.items():
            if full.startswith(s[:3]):
                return idx
    return None


def next_weekday_on_or_after(start: dt.date, target_weekday: int) -> dt.date:
    delta = (target_weekday - start.weekday()) % 7
    return start + dt.timedelta(days=delta)


def parse_day_value(value: Any, *, where: str) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "rest":
            return []
        return [w.strip() for w in text.split(",") if w.strip()]

    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise SystemExit(f"{where}: expected workout names to be strings")
            item = item.strip()
            if not item or item.lower() == "rest":
                continue
            names.append(item)
        return names

    raise SystemExit(f"{where}: expected a string, list of strings, or 'rest'")


def parse_schedule_days(
    schedule_plan: dict[str, Any],
    *,
    today: dt.date,
    source_label: str,
) -> list[ScheduledDay]:
    entries = schedule_plan.get("workouts")
    if not isinstance(entries, list):
        raise SystemExit("schedulePlan.workouts must be a list")

    start_from_raw = schedule_plan.get("start_from")

    # Absolute schedule (existing behavior)
    if start_from_raw is not None:
        if isinstance(start_from_raw, dt.datetime):
            start_date = start_from_raw.date()
        elif isinstance(start_from_raw, dt.date):
            start_date = start_from_raw
        elif isinstance(start_from_raw, str):
            try:
                start_date = dt.date.fromisoformat(start_from_raw)
            except ValueError as exc:
                raise SystemExit("schedulePlan.start_from must be YYYY-MM-DD") from exc
        else:
            raise SystemExit(
                "schedulePlan.start_from must be YYYY-MM-DD (string) or a YAML date"
            )

        days: list[ScheduledDay] = []
        current_date = start_date
        for idx, entry in enumerate(entries):
            where = f"{source_label}: schedulePlan.workouts[{idx}]"
            if isinstance(entry, str):
                names = parse_day_value(entry, where=where)
            elif isinstance(entry, dict) and len(entry) == 1:
                # allow { weekdayName: "workout" } for readability even in absolute mode
                names = parse_day_value(next(iter(entry.values())), where=where)
            else:
                raise SystemExit(
                    f"{where}: expected a string (workout names/rest) or a single-key mapping"
                )

            days.append(
                ScheduledDay(date=current_date, names=names, source_label=source_label)
            )
            current_date += dt.timedelta(days=1)

        return days

    # Relative weekday schedule
    # Use a list of weekday-keyed items; interpret each weekday as the next one
    # from today onwards (Saturday can mean today, Sunday tomorrow, etc.).
    days = []
    cursor = today
    for idx, entry in enumerate(entries):
        where = f"{source_label}: schedulePlan.workouts[{idx}]"

        if not (isinstance(entry, dict) and len(entry) == 1):
            raise SystemExit(
                f"{where}: without schedulePlan.start_from, each entry must be a single-key mapping "
                "like {monday: workout_name} or {sunday: rest}"
            )

        raw_weekday = next(iter(entry.keys()))
        if not isinstance(raw_weekday, str):
            raise SystemExit(f"{where}: weekday key must be a string")

        weekday = normalize_weekday(raw_weekday)
        if weekday is None:
            raise SystemExit(f"{where}: unrecognized weekday: {raw_weekday!r}")

        date = next_weekday_on_or_after(cursor, weekday)
        cursor = date + dt.timedelta(days=1)

        names = parse_day_value(next(iter(entry.values())), where=where)
        days.append(ScheduledDay(date=date, names=names, source_label=source_label))

    return sorted(days, key=lambda d: d.date)


def replace_definitions(value: Any, definitions: dict[str, str]) -> Any:
    if isinstance(value, str):
        return re.sub(
            r"\$(\w+)", lambda m: definitions.get(m.group(1), m.group(0)), value
        )
    if isinstance(value, list):
        return [replace_definitions(v, definitions) for v in value]
    if isinstance(value, dict):
        return {k: replace_definitions(v, definitions) for k, v in value.items()}
    return value


def _unpack_workout_entry(
    workout_name: str,
    step_specs: Any,
) -> tuple[str | None, list[Any]]:
    """Return (description, steps) from a workout definition in the YAML plan."""
    if isinstance(step_specs, list):
        return None, step_specs
    if isinstance(step_specs, dict):
        raw_desc = step_specs.get("description")
        raw_steps = step_specs.get("steps")
        if raw_desc is not None and not isinstance(raw_desc, str):
            raise SystemExit(
                f"Workout {workout_name!r} description must be a string if provided"
            )
        if not isinstance(raw_steps, list):
            raise SystemExit(
                f"Workout {workout_name!r} must provide a list under 'steps'"
            )
        return raw_desc, raw_steps
    raise SystemExit(
        f"Workout {workout_name!r} must be a list of steps, or a mapping with 'steps'"
    )


def parse_plan(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("Workout plan must be a YAML mapping at top-level")
    return data


def parse_plan_bytes(data: bytes, *, source_label: str) -> dict[str, Any]:
    try:
        obj = yaml.safe_load(data.decode("utf-8"))
    except Exception as exc:
        raise SystemExit(f"{source_label}: invalid YAML: {exc!r}") from exc
    if not isinstance(obj, dict):
        raise SystemExit(
            f"{source_label}: workout plan must be a YAML mapping at top-level"
        )
    return obj


def iter_drive_plans(folder_id: str) -> list[tuple[str, dict[str, Any]]]:
    service = gdrive_service(allow_interactive=False)
    drive_files = list_drive_files(service, folder_id)

    plans: list[tuple[str, dict[str, Any]]] = []
    for name, meta in sorted(drive_files.items()):
        if not isinstance(name, str):
            continue
        if not (name.lower().endswith(".yaml") or name.lower().endswith(".yml")):
            continue
        file_id = meta.get("id")
        if not isinstance(file_id, str) or not file_id:
            continue

        raw = download_drive_file_bytes(service, file_id)
        plans.append((name, parse_plan_bytes(raw, source_label=f"Drive:{name}")))

    return plans


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "plan",
        type=str,
        nargs="?",
        default=None,
        help=(
            "Path to workout plan YAML. If omitted, default behavior is: "
            "sync from Drive (GDRIVE_FOLDER_ID_WORKOUTS)."
        ),
    )
    parser.add_argument(
        "--from-drive",
        action="store_true",
        help="Load and sync all YAML plans found in the Drive folder GDRIVE_FOLDER_ID_WORKOUTS",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help=(
            "List workouts and exit. Source selection: Garmin Connect if no plan is provided, "
            "Drive if --from-drive is set, or the given YAML file if provided."
        ),
    )
    parser.add_argument(
        "--list-schedule",
        action="store_true",
        help=(
            "List scheduled workouts and exit. Source selection: Garmin Connect if no plan is provided, "
            "Drive if --from-drive is set, or the given YAML file if provided."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print what would be created/scheduled, without calling Garmin",
    )
    parser.add_argument(
        "--dump-json",
        action="store_true",
        help=(
            "Dump generated Garmin workout payload JSON to stdout and exit. "
            "This does not call Garmin (safe/read-only)."
        ),
    )
    return parser.parse_args()


def load_plans_from_source(
    *,
    from_drive: bool,
    plan_path: Path | None,
    allow_empty: bool = False,
) -> list[tuple[str, dict[str, Any]]]:
    if from_drive:
        folder_id = get_config("GDRIVE_FOLDER_ID_WORKOUTS")
        plans = iter_drive_plans(folder_id)
        if not plans and not allow_empty:
            raise SystemExit("No YAML plan files found in GDRIVE_FOLDER_ID_WORKOUTS")
        return plans

    if plan_path is None:
        raise SystemExit("No plan provided and --from-drive not set.")

    if not plan_path.exists():
        raise SystemExit(f"Plan file not found: {plan_path}")
    return [(str(plan_path), parse_plan(plan_path))]


def print_workouts_from_plans(plans: list[tuple[str, dict[str, Any]]]) -> None:
    for source_label, plan in plans:
        definitions = plan.get("definitions") or {}
        if definitions and isinstance(definitions, dict):
            definitions = {str(k): str(v) for k, v in definitions.items()}
            plan = replace_definitions(plan, definitions)

        workouts = plan.get("workouts")
        if not isinstance(workouts, dict) or not workouts:
            print(f"=== {source_label} ===")
            print("(no workouts found)")
            continue

        print(f"=== {source_label} ===")
        for name in sorted(str(k) for k in workouts.keys()):
            print(name)


def print_schedule_from_plans(
    plans: list[tuple[str, dict[str, Any]]], *, today: dt.date
) -> None:
    for source_label, plan in plans:
        definitions = plan.get("definitions") or {}
        if definitions and isinstance(definitions, dict):
            definitions = {str(k): str(v) for k, v in definitions.items()}
            plan = replace_definitions(plan, definitions)

        schedule_plan = plan.get("schedulePlan")
        if not isinstance(schedule_plan, dict):
            print(f"=== {source_label} ===")
            print("(no schedulePlan found)")
            continue

        schedule_days = parse_schedule_days(
            schedule_plan, today=today, source_label=source_label
        )

        print(f"=== {source_label} ===")
        for d in schedule_days:
            status = "UPCOMING" if d.date >= today else "OUTDATED"
            names = ", ".join(d.names) if d.names else "rest"
            print(f"{d.date} [{status}] {names}")


def main() -> None:
    args = parse_args()

    plan_path = Path(args.plan) if args.plan is not None else None
    plan_provided = plan_path is not None

    # New default: no-args sync uses Drive.
    effective_from_drive = bool(
        args.from_drive
        or (not plan_provided and not args.list and not args.list_schedule)
    )

    today = dt.date.today()

    if args.dump_json:
        plans = load_plans_from_source(
            from_drive=effective_from_drive,
            plan_path=plan_path,
            allow_empty=True,
        )
        if not plans:
            raise SystemExit("No YAML workout plan(s) found to dump")

        dumped: list[dict[str, Any]] = []
        for source_label, plan in plans:
            definitions = plan.get("definitions") or {}
            if definitions and isinstance(definitions, dict):
                definitions = {str(k): str(v) for k, v in definitions.items()}
                plan = replace_definitions(plan, definitions)

            settings = plan.get("settings") or {}
            if settings and not isinstance(settings, dict):
                raise SystemExit("settings must be a mapping")

            sport_type, sub_sport_type = parse_sport_settings(
                settings,
                source_label=source_label,
            )
            pool_length, pool_length_unit = parse_pool_settings(
                settings,
                source_label=source_label,
            )

            workouts = plan.get("workouts")
            if not isinstance(workouts, dict) or not workouts:
                raise SystemExit("workouts must be a non-empty mapping")

            needed_names: set[str]
            schedule_plan = plan.get("schedulePlan")
            if isinstance(schedule_plan, dict):
                schedule_days = parse_schedule_days(
                    schedule_plan, today=today, source_label=source_label
                )
                upcoming = [d for d in schedule_days if d.date >= today]
                needed_names = set()
                for d in upcoming:
                    needed_names.update(d.names)
            else:
                needed_names = {str(k) for k in workouts.keys()}

            payloads_by_name: dict[str, Any] = {}
            for workout_name, step_specs in workouts.items():
                if not isinstance(workout_name, str):
                    raise SystemExit("Workout name keys must be strings")
                if workout_name not in needed_names:
                    continue

                workout_description, workout_steps = _unpack_workout_entry(
                    workout_name, step_specs
                )

                payloads_by_name[workout_name] = build_workout_payload(
                    workout_name,
                    workout_steps,
                    description=workout_description,
                    sport_type=sport_type,
                    sub_sport_type=sub_sport_type,
                    pool_length=pool_length,
                    pool_length_unit=pool_length_unit,
                )

            dumped.append({"source": source_label, "workouts": payloads_by_name})

        # Emit JSON only so users can redirect to a file.
        if len(dumped) == 1:
            print(json.dumps(dumped[0], indent=2, sort_keys=True))
        else:
            print(json.dumps(dumped, indent=2, sort_keys=True))
        return

    # --list / --list-schedule are source-aware.
    if args.list:
        if effective_from_drive:
            plans = load_plans_from_source(
                from_drive=True, plan_path=None, allow_empty=True
            )
            if not plans:
                print("No YAML plan files found in GDRIVE_FOLDER_ID_WORKOUTS")
                return
            print_workouts_from_plans(plans)
            return

        if plan_provided:
            plans = load_plans_from_source(from_drive=False, plan_path=plan_path)
            print_workouts_from_plans(plans)
            return

        # No plan + no --from-drive -> list from Garmin Connect
        print("Authenticating with Garmin Connect...")
        garmin_authenticate()
        for w in garmin_list_workouts():
            wid = w.get("workoutId")
            name = w.get("workoutName")
            print(f"{wid}\t{name}")
        return

    if args.list_schedule:
        if effective_from_drive:
            plans = load_plans_from_source(
                from_drive=True, plan_path=None, allow_empty=True
            )
            if not plans:
                print("No YAML plan files found in GDRIVE_FOLDER_ID_WORKOUTS")
                return
            print_schedule_from_plans(plans, today=today)
            return

        if plan_provided:
            plans = load_plans_from_source(from_drive=False, plan_path=plan_path)
            print_schedule_from_plans(plans, today=today)
            return

        # No plan + no --from-drive -> list from Garmin Connect
        print("Authenticating with Garmin Connect...")
        garmin_authenticate()
        # Keep this window reasonably small because the fallback implementation
        # may need to query a per-day endpoint.
        start = (today - dt.timedelta(days=7)).strftime(DATE_FORMAT)
        end = (today + dt.timedelta(days=90)).strftime(DATE_FORMAT)
        items = garmin_list_scheduled_workouts(start_date_iso=start, end_date_iso=end)
        print(f"Listing scheduled workouts in range {start}..{end}")
        for item in items:
            date = item.get("date") or item.get("workoutDate")
            name = item.get("workoutName")
            wid = item.get("workoutId")
            sid = item.get("workoutScheduleId")
            print(f"{date}\t{sid}\t{wid}\t{name}")
        return

    # Sync mode (default: Drive when no args)
    plans = load_plans_from_source(from_drive=effective_from_drive, plan_path=plan_path)

    if not args.dry_run:
        print("Authenticating with Garmin Connect...")
        garmin_authenticate()

    for source_label, plan in plans:
        definitions = plan.get("definitions") or {}
        if definitions and not isinstance(definitions, dict):
            raise SystemExit("definitions must be a mapping")
        definitions = {str(k): str(v) for k, v in definitions.items()}

        plan = replace_definitions(plan, definitions)

        settings = plan.get("settings") or {}
        if settings and not isinstance(settings, dict):
            raise SystemExit("settings must be a mapping")

        sport_type, sub_sport_type = parse_sport_settings(
            settings,
            source_label=source_label,
        )

        pool_length, pool_length_unit = parse_pool_settings(
            settings,
            source_label=source_label,
        )

        # precedence: plan settings > sync_settings.toml > default
        if "deleteSameNameWorkout" in settings:
            delete_same_name = bool(settings.get("deleteSameNameWorkout"))
        else:
            delete_same_name = bool(
                get_toml_setting(
                    SYNC_SETTINGS, "workouts", "deleteSameNameWorkout", False
                )
            )

        workouts = plan.get("workouts")
        if not isinstance(workouts, dict) or not workouts:
            raise SystemExit("workouts must be a non-empty mapping")

        schedule_plan = plan.get("schedulePlan")
        if not isinstance(schedule_plan, dict):
            raise SystemExit("schedulePlan must be a mapping")

        schedule_days = parse_schedule_days(
            schedule_plan, today=today, source_label=source_label
        )
        upcoming = [d for d in schedule_days if d.date >= today]

        needed_names: set[str] = set()
        for d in upcoming:
            needed_names.update(d.names)

        print(f"=== {source_label} ===")

        if args.dry_run:
            print(f"Workouts defined: {len(workouts)}")
            print(f"Schedule days (total): {len(schedule_days)}")
            print(f"Schedule days (upcoming): {len(upcoming)}")
            print(f"Workouts referenced (upcoming): {sorted(needed_names)}")
            continue

        # Always list existing workouts so we can log duplicate names and
        # schedule deterministically even if deletion is disabled.
        existing = garmin_list_workouts()
        existing_by_name: dict[str, list[int]] = {}
        for w in existing:
            name = w.get("workoutName")
            wid = w.get("workoutId")
            if isinstance(name, str) and isinstance(wid, int):
                existing_by_name.setdefault(name.casefold().strip(), []).append(wid)

        # Create only workouts needed from today onward.
        for workout_name, step_specs in workouts.items():
            if not isinstance(workout_name, str):
                raise SystemExit("Workout name keys must be strings")
            if workout_name not in needed_names:
                continue

            workout_description, workout_steps = _unpack_workout_entry(
                workout_name, step_specs
            )

            if delete_same_name:
                to_delete = existing_by_name.get(workout_name.casefold().strip(), [])
                if to_delete:
                    print(
                        f"Found {len(to_delete)} existing workout(s) named {workout_name!r}"
                    )
                for wid in to_delete:
                    print(f"Deleting existing workout: {wid} ({workout_name})")
                    garmin_delete_workout(wid)
            else:
                dupes = existing_by_name.get(workout_name.casefold().strip(), [])
                if dupes:
                    print(
                        f"Found {len(dupes)} existing workout(s) named {workout_name!r}; "
                        "not deleting (deleteSameNameWorkout=false)"
                    )

            payload = build_workout_payload(
                workout_name,
                workout_steps,
                description=workout_description,
                sport_type=sport_type,
                sub_sport_type=sub_sport_type,
                pool_length=pool_length,
                pool_length_unit=pool_length_unit,
            )
            created = garmin_import_workout(payload)
            print(
                f"Created workout: {created.get('workoutName')} (id={created.get('workoutId')})"
            )

        # Schedule (today onward)
        all_workouts = garmin_list_workouts()
        workout_map: dict[str, int] = {}
        for w in all_workouts:
            name = w.get("workoutName")
            wid = w.get("workoutId")
            if isinstance(name, str) and isinstance(wid, int):
                # If duplicates exist, prefer the most recently created one.
                workout_map[name] = max(workout_map.get(name, 0), wid)

        for d in upcoming:
            for name in d.names:
                if name not in workout_map:
                    print(f"Workout not found on Garmin, skipping: {name!r}")
                    continue
                wid = workout_map[name]
                res = garmin_schedule_workout(wid, d.date.strftime(DATE_FORMAT))
                if isinstance(res, dict) and "workoutScheduleId" in res:
                    print(
                        f"Scheduled {name!r} on {d.date} (workoutScheduleId={res['workoutScheduleId']})"
                    )
                else:
                    print(f"Scheduled {name!r} on {d.date} (response={res})")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # Common when piping large JSON output to tools that stop early
        # (e.g. PowerShell Select-Object -First N).
        import os
        import sys

        try:
            sys.stdout.close()
        finally:
            # Exit 0 to avoid showing this as an error.
            os._exit(0)
