import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Allow running tests without installing the package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from garmin_scheduler.garmin_constants import SPORT_SWIMMING  # noqa: E402


class TestSyncWorkoutsSportType(unittest.TestCase):
    def test_missing_garmin_session_is_user_friendly(self):
        """Running Garmin-backed paths without GARMIN_SESSION should not crash."""

        from garmin_scheduler import sync_workouts

        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("GARMIN_SESSION", None)
            with self.assertRaises(SystemExit) as ctx:
                with patch.object(sys, "argv", ["sync_workouts.py", "--list"]):
                    sync_workouts.main()

        self.assertIn("GARMIN_SESSION", str(ctx.exception))

    def test_validate_sport_type(self):
        from garmin_scheduler import workout_builder

        st = workout_builder._validate_sport_type(
            {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
            where="x",
        )
        self.assertEqual(st["sportTypeId"], 1)
        self.assertEqual(st["sportTypeKey"], "running")

        with self.assertRaises(SystemExit):
            workout_builder._validate_sport_type("running", where="x")

    def test_parse_sport_settings_string_running(self):
        from garmin_scheduler import workout_builder

        st, sst = workout_builder.parse_sport_settings(
            {"sportType": "running"},
            source_label="x",
        )
        self.assertIsInstance(st, dict)
        self.assertEqual(st.get("sportTypeKey"), "running")
        self.assertIsNone(sst)

    def test_swimming_payload_pool_length_config(self):
        from garmin_scheduler import workout_builder

        payload = workout_builder.build_workout_payload(
            "swim_test",
            [{"run": "50m"}],
            sport_type=SPORT_SWIMMING,
            pool_length=50.0,
            pool_length_unit={"unitId": 1, "unitKey": "meter", "factor": 100.0},
        )
        self.assertEqual(payload.get("sportType", {}).get("sportTypeKey"), "swimming")
        self.assertEqual(payload.get("poolLength"), 50.0)
        self.assertEqual(payload.get("poolLengthUnit", {}).get("unitKey"), "meter")

    def test_workout_description_in_payload(self):
        from garmin_scheduler import workout_builder

        payload = workout_builder.build_workout_payload(
            "desc_test",
            [{"run": "30sec"}],
            description="hello world",
        )
        self.assertEqual(payload.get("description"), "hello world")


class TestSyncWorkoutsSteps(unittest.TestCase):
    def test_swim_alias_builds(self):
        from garmin_scheduler import workout_builder

        steps = workout_builder.build_steps(
            [{"swim": "50m"}, {"recovery": "30sec"}],
            [0],
        )
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["stepType"]["stepTypeKey"], "interval")

    def test_targets_cadence_and_power(self):
        from garmin_scheduler import workout_builder

        cadence = workout_builder.parse_step_detail("1000m @C(180-195)")
        self.assertEqual(cadence["targetType"]["workoutTargetTypeKey"], "cadence")
        self.assertEqual(cadence["targetValueOne"], 180)
        self.assertEqual(cadence["targetValueTwo"], 195)

        power = workout_builder.parse_step_detail("300sec @W(250-280)")
        self.assertEqual(power["targetType"]["workoutTargetTypeKey"], "speed.zone")
        self.assertEqual(power["targetValueOne"], 250.0)
        self.assertEqual(power["targetValueTwo"], 280.0)

    def test_custom_hr_target_is_hr_zone_with_values(self):
        from garmin_scheduler import workout_builder

        step = workout_builder.parse_step_detail("60sec @HR(120-130)")
        self.assertEqual(step["targetType"]["workoutTargetTypeKey"], "heart.rate.zone")
        self.assertNotIn("zoneNumber", step)
        self.assertEqual(step["targetValueOne"], 120.0)
        self.assertEqual(step["targetValueTwo"], 130.0)

    def test_swim_instruction_sets_secondary_target(self):
        from garmin_scheduler import workout_builder

        step = workout_builder.parse_step_detail(
            "50m @SI(descending)",
            sport_type_key="swimming",
        )
        self.assertEqual(step["targetType"]["workoutTargetTypeKey"], "no.target")
        self.assertEqual(
            step["secondaryTargetType"]["workoutTargetTypeKey"], "swim.instruction"
        )
        self.assertEqual(step["secondaryTargetValueOne"], 10.0)

    def test_swim_css_offset_sets_secondary_target(self):
        from garmin_scheduler import workout_builder

        step = workout_builder.parse_step_detail(
            "50m @CSS(-1)",
            sport_type_key="swimming",
        )
        self.assertEqual(step["targetType"]["workoutTargetTypeKey"], "no.target")
        self.assertEqual(
            step["secondaryTargetType"]["workoutTargetTypeKey"], "swim.css.offset"
        )
        self.assertEqual(step["secondaryTargetValueOne"], -1.0)

    def test_pace_conversion_matches_expected_speed_range(self):
        from garmin_scheduler import workout_builder

        # 3:40 min/km -> ~4.545 m/s; 3:20 min/km -> 5.0 m/s
        step = workout_builder.parse_step_detail("800m @P(3:40-3:20)")
        self.assertEqual(step["targetType"]["workoutTargetTypeKey"], "pace.zone")
        self.assertAlmostEqual(step["targetValueOne"], 4.545, places=2)
        self.assertAlmostEqual(step["targetValueTwo"], 5.0, places=2)

    def test_km_duration_is_converted_to_meters(self):
        from garmin_scheduler import workout_builder

        step = workout_builder.parse_step_detail("1km")
        self.assertEqual(step["endCondition"]["conditionTypeKey"], "distance")
        self.assertAlmostEqual(step["endConditionValue"], 1000.0, places=6)

    def test_swim_pace_uses_single_target_and_secondary_fields(self):
        from garmin_scheduler import workout_builder

        step = workout_builder.parse_step_detail(
            "800m @SP(1:50-1:40)",
            sport_type_key="swimming",
            swim_pace_unit={"unitId": 1, "unitKey": "meter", "factor": 100.0},
        )
        self.assertEqual(step["targetType"]["workoutTargetTypeKey"], "pace.zone")
        self.assertEqual(
            step["secondaryTargetType"]["workoutTargetTypeKey"], "pace.zone"
        )
        self.assertEqual(step["secondaryTargetValueUnit"]["factor"], 100.0)
        self.assertNotIn("targetValueTwo", step)
        self.assertAlmostEqual(step["targetValueOne"], 0.909, places=2)


if __name__ == "__main__":
    unittest.main()
