import os
import unittest
from pathlib import Path
from typing import Any

import garth
import yaml

import workout_builder
import utils


def _walk_steps_collect_types(
    steps: list[Any], *, out: dict[str, set[tuple[int, str]]]
) -> None:
    stack: list[Any] = list(steps)
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue

        st = item.get("stepType")
        if isinstance(st, dict):
            stid = st.get("stepTypeId")
            stkey = st.get("stepTypeKey")
            if isinstance(stid, int) and isinstance(stkey, str):
                out.setdefault("stepType", set()).add((stid, stkey))

        ec = item.get("endCondition")
        if isinstance(ec, dict):
            cid = ec.get("conditionTypeId")
            ckey = ec.get("conditionTypeKey")
            if isinstance(cid, int) and isinstance(ckey, str):
                out.setdefault("conditionType", set()).add((cid, ckey))

        tt = item.get("targetType")
        if isinstance(tt, dict):
            tid = tt.get("workoutTargetTypeId")
            tkey = tt.get("workoutTargetTypeKey")
            if isinstance(tid, int) and isinstance(tkey, str):
                out.setdefault("targetType", set()).add((tid, tkey))

        nested = item.get("workoutSteps")
        if isinstance(nested, list):
            stack.extend(nested)


def _walk_payload_steps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    segs = payload.get("workoutSegments") or []
    if not isinstance(segs, list) or not segs:
        return []
    seg0 = segs[0]
    if not isinstance(seg0, dict):
        return []
    steps = seg0.get("workoutSteps") or []
    if not isinstance(steps, list):
        return []

    out: list[dict[str, Any]] = []
    stack: list[Any] = list(steps)
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        out.append(item)
        nested = item.get("workoutSteps")
        if isinstance(nested, list):
            stack.extend(nested)
    return out


@unittest.skipUnless(
    os.environ.get("RUN_GARMIN_VALIDATE_TESTS") == "1"
    and os.environ.get("GARMIN_SESSION"),
    "Set RUN_GARMIN_VALIDATE_TESTS=1 and GARMIN_SESSION to run Garmin read-only validation.",
)
class TestGarminReadOnlyValidation(unittest.TestCase):
    """Read-only integration checks against your Garmin account.

    These tests NEVER create, delete, or schedule workouts.

    What they do:
    - Build the payload from a YAML plan.
    - Authenticate and download existing Garmin workout JSON.
    - Verify that the payload only uses step/condition/target IDs+keys that Garmin
      already uses on your account.

    This is a best-effort proxy for "Garmin validation" without side effects.
    """

    def test_swim_stress_test_payload_uses_known_types(self):
        utils.garmin_authenticate()

        plan_path = Path("workout_plan_swim.yaml")
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        self.assertIsInstance(plan, dict)

        settings = plan.get("settings") or {}
        sport_type, sub_sport_type = workout_builder.parse_sport_settings(
            settings, source_label=str(plan_path)
        )
        pool_length, pool_unit = workout_builder.parse_pool_settings(
            settings, source_label=str(plan_path)
        )

        workout_def = plan["workouts"]["Ultimate Script Stress Test 2026"]
        self.assertIsInstance(workout_def, dict)

        payload = workout_builder.build_workout_payload(
            "Ultimate Script Stress Test 2026",
            workout_def["steps"],
            description=workout_def.get("description"),
            sport_type=sport_type,
            sub_sport_type=sub_sport_type,
            pool_length=pool_length,
            pool_length_unit=pool_unit,
        )

        allowed: dict[str, set[tuple[int, str]]] = {}
        workouts = utils.garmin_list_workouts()
        max_fetch = int(os.environ.get("GARMIN_VALIDATE_MAX_FETCH", "25"))

        for w in workouts[:max_fetch]:
            wid = w.get("workoutId")
            if not isinstance(wid, int):
                continue
            full = garth.connectapi(f"/workout-service/workout/{wid}")
            segs = full.get("workoutSegments") or []
            if not isinstance(segs, list):
                continue
            for seg in segs:
                if not isinstance(seg, dict):
                    continue
                steps = seg.get("workoutSteps") or []
                if isinstance(steps, list):
                    _walk_steps_collect_types(steps, out=allowed)

        self.assertTrue(allowed.get("stepType"), "No stepType values discovered")
        self.assertTrue(
            allowed.get("conditionType"), "No conditionType values discovered"
        )
        self.assertTrue(allowed.get("targetType"), "No targetType values discovered")

        payload_steps = _walk_payload_steps(payload)
        self.assertTrue(payload_steps, "Payload had no steps")

        missing_step_types: set[tuple[int, str]] = set()
        missing_condition_types: set[tuple[int, str]] = set()
        missing_target_types: set[tuple[int, str]] = set()

        for step in payload_steps:
            st = step.get("stepType")
            if isinstance(st, dict):
                stid = st.get("stepTypeId")
                stkey = st.get("stepTypeKey")
                if isinstance(stid, int) and isinstance(stkey, str):
                    if (stid, stkey) not in allowed["stepType"]:
                        missing_step_types.add((stid, stkey))

            ec = step.get("endCondition")
            if isinstance(ec, dict):
                cid = ec.get("conditionTypeId")
                ckey = ec.get("conditionTypeKey")
                if isinstance(cid, int) and isinstance(ckey, str):
                    if (cid, ckey) not in allowed["conditionType"]:
                        missing_condition_types.add((cid, ckey))

            tt = step.get("targetType")
            if isinstance(tt, dict):
                tid = tt.get("workoutTargetTypeId")
                tkey = tt.get("workoutTargetTypeKey")
                if isinstance(tid, int) and isinstance(tkey, str):
                    if (tid, tkey) not in allowed["targetType"]:
                        missing_target_types.add((tid, tkey))

        if missing_step_types or missing_condition_types or missing_target_types:
            self.fail(
                "Payload contains types not observed on this account. "
                "This often means: wrong IDs/keys, or you used an override placeholder.\n"
                f"Missing stepType: {sorted(missing_step_types)}\n"
                f"Missing conditionType: {sorted(missing_condition_types)}\n"
                f"Missing targetType: {sorted(missing_target_types)}\n"
            )


if __name__ == "__main__":
    unittest.main()
