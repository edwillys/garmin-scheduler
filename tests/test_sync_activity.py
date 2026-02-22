import unittest
from unittest.mock import patch


class TestSyncActivityArgs(unittest.TestCase):
    def test_help_works_without_env(self):
        import sync_activity

        with self.assertRaises(SystemExit) as ctx:
            sync_activity.parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_parse_iso_date_validation(self):
        import sync_activity

        self.assertEqual(
            sync_activity._parse_iso_date(
                "2026-02-21", flag_name="--start-date"
            ).isoformat(),
            "2026-02-21",
        )
        with self.assertRaises(SystemExit):
            sync_activity._parse_iso_date("2026-02-30", flag_name="--start-date")


class TestSyncActivityBehavior(unittest.TestCase):
    def test_list_mode_does_not_touch_drive(self):
        import sync_activity

        with (
            patch.object(sync_activity, "garmin_authenticate") as garmin_auth,
            patch.object(sync_activity, "fetch_recent_activities", return_value=[]),
            patch.object(
                sync_activity,
                "gdrive_service",
                side_effect=AssertionError("Drive should not be used"),
            ),
        ):
            # Should not raise, and should not call Drive helpers.
            sync_activity.main(["--list"])
            garmin_auth.assert_called_once()

    def test_fetch_activities_in_range_paginates(self):
        import datetime as dt

        import sync_activity

        page1 = [
            {
                "activityId": 1,
                "startTimeLocal": "2026-02-21 10:00:00",
                "activityType": {"typeKey": "lap_swimming"},
            },
            {
                "activityId": 2,
                "startTimeLocal": "2026-02-20 10:00:00",
                "activityType": {"typeKey": "lap_swimming"},
            },
        ]
        page2 = [
            {
                "activityId": 3,
                "startTimeLocal": "2026-02-19 10:00:00",
                "activityType": {"typeKey": "lap_swimming"},
            },
        ]

        def fake_connectapi(_path, params):
            if params["start"] == 0:
                return page1
            if params["start"] == 100:
                return page2
            return []

        with patch.object(
            sync_activity.garth, "connectapi", side_effect=fake_connectapi
        ):
            res = sync_activity.fetch_activities_in_range(
                "lap_swimming",
                start_date=dt.date.fromisoformat("2026-02-20"),
                end_date=dt.date.fromisoformat("2026-02-21"),
                page_size=100,
                max_pages=5,
            )

        self.assertEqual([a["activityId"] for a in res], [1, 2])


if __name__ == "__main__":
    unittest.main()
