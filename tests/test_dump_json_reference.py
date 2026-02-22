import json
import unittest
from pathlib import Path

import yaml

import workout_builder


class TestDumpJsonReference(unittest.TestCase):
    def test_workout_plan_swim_dump_matches_reference(self):
        """Generated payload matches the reference fixture."""

        expected_path = Path("tests/fixtures/expected_dump_workout_plan_swim.json")
        expected = json.loads(expected_path.read_text(encoding="utf-8"))

        plan_path = Path("workout_plan_swim.yaml")
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        self.assertIsInstance(plan, dict)

        settings = plan.get("settings") or {}
        self.assertIsInstance(settings, dict)

        sport_type, sub_sport_type = workout_builder.parse_sport_settings(
            settings, source_label=str(plan_path)
        )
        pool_length, pool_unit = workout_builder.parse_pool_settings(
            settings, source_label=str(plan_path)
        )

        workouts = plan.get("workouts")
        self.assertIsInstance(workouts, dict)

        # This plan currently defines this workout.
        workout_name = "Ultimate Script Stress Test 2026"
        workout_def = workouts[workout_name]
        self.assertIsInstance(workout_def, dict)

        steps = workout_def["steps"]
        self.assertIsInstance(steps, list)

        actual = workout_builder.build_workout_payload(
            workout_name,
            steps,
            description=workout_def.get("description"),
            sport_type=sport_type,
            sub_sport_type=sub_sport_type,
            pool_length=pool_length,
            pool_length_unit=pool_unit,
        )

        self.assertDictEqual(actual, expected)

    def test_workout_plan_run_dump_matches_reference(self):
        """Generated payload matches the running reference fixture."""

        expected_path = Path("tests/fixtures/expected_dump_workout_plan_run.json")
        expected = json.loads(expected_path.read_text(encoding="utf-8"))

        plan_path = Path("workout_plan_run.yaml")
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        self.assertIsInstance(plan, dict)

        settings = plan.get("settings") or {}
        self.assertIsInstance(settings, dict)

        sport_type, sub_sport_type = workout_builder.parse_sport_settings(
            settings, source_label=str(plan_path)
        )
        pool_length, pool_unit = workout_builder.parse_pool_settings(
            settings, source_label=str(plan_path)
        )

        workouts = plan.get("workouts")
        self.assertIsInstance(workouts, dict)

        workout_name = "Ultimate Script Stress Test 2026"
        workout_def = workouts[workout_name]
        self.assertIsInstance(workout_def, dict)

        steps = workout_def["steps"]
        self.assertIsInstance(steps, list)

        actual = workout_builder.build_workout_payload(
            workout_name,
            steps,
            description=workout_def.get("description"),
            sport_type=sport_type,
            sub_sport_type=sub_sport_type,
            pool_length=pool_length,
            pool_length_unit=pool_unit,
        )

        self.assertDictEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
