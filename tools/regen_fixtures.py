"""Regenerate JSON fixtures used by unit tests.

This is a dev helper (not used by production CLIs).

Usage:
    python tools/regen_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

# Allow running this tool without installing the package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from garmin_scheduler import workout_builder  # noqa: E402


def _regen_plan_fixture(*, plan_path: Path, workout_name: str, out_path: Path) -> None:
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise SystemExit(f"Plan must be a mapping: {plan_path}")

    settings = plan.get("settings") or {}
    if not isinstance(settings, dict):
        raise SystemExit(f"settings must be a mapping: {plan_path}")

    sport_type, sub_sport_type = workout_builder.parse_sport_settings(
        settings, source_label=str(plan_path)
    )
    pool_length, pool_unit = workout_builder.parse_pool_settings(
        settings, source_label=str(plan_path)
    )

    workouts = plan.get("workouts")
    if not isinstance(workouts, dict):
        raise SystemExit(f"workouts must be a mapping: {plan_path}")

    workout_def = workouts.get(workout_name)
    if not isinstance(workout_def, dict):
        raise SystemExit(f"Workout not found: {workout_name!r} in {plan_path}")

    steps = workout_def.get("steps")
    if not isinstance(steps, list):
        raise SystemExit(f"steps must be a list for {workout_name!r} in {plan_path}")

    payload = workout_builder.build_workout_payload(
        workout_name,
        steps,
        description=workout_def.get("description"),
        sport_type=sport_type,
        sub_sport_type=sub_sport_type,
        pool_length=pool_length,
        pool_length_unit=pool_unit,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("wrote", out_path)


def main() -> None:
    _regen_plan_fixture(
        plan_path=Path("workout_plan_swim.yaml"),
        workout_name="Ultimate Script Stress Test 2026",
        out_path=Path("tests/fixtures/expected_dump_workout_plan_swim.json"),
    )

    _regen_plan_fixture(
        plan_path=Path("workout_plan_run.yaml"),
        workout_name="Ultimate Script Stress Test 2026",
        out_path=Path("tests/fixtures/expected_dump_workout_plan_run.json"),
    )


if __name__ == "__main__":
    main()
