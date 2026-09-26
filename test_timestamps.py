"""Every timestamp Keep shows is in local time, whatever offset it was
stored with. Regression: the integrity check is recorded in UTC
("…+00:00") and showed 8 hours early in Singapore (1:35 PM for 9:35 PM)."""
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_STATE = tempfile.TemporaryDirectory()
os.environ["KEEP_CONFIG_PATH"] = str(Path(_STATE.name) / "config.json")
os.environ["XDG_STATE_HOME"] = str(Path(_STATE.name) / "state")

import main  # noqa: E402


class LocalTime(unittest.TestCase):
    def setUp(self):
        self.addCleanup(time.tzset)  # after patch.dict restores TZ
        patcher = patch.dict(os.environ, {"TZ": "Asia/Singapore"})
        patcher.start()
        self.addCleanup(patcher.stop)
        time.tzset()

    # The clock's format follows the locale (6:25 AM here, 06:25:00 on CI),
    # so compare with the same moment written as naive local time.
    def test_utc_is_shown_in_local_time(self):
        self.assertEqual(main.friendly_timestamp("2020-09-24T22:25:14.753626+00:00"),
                         main.friendly_datetime(datetime(2020, 9, 25, 6, 25, 14)))

    def test_local_offsets_and_naive_times_are_unchanged(self):
        local = main.friendly_datetime(datetime(2020, 9, 24, 22, 25, 14))
        self.assertEqual(main.friendly_timestamp("2020-09-24T22:25:14+08:00"), local)
        self.assertEqual(main.friendly_timestamp("2020-09-24T22:25:14.000000"), local)

    def test_today_is_judged_in_local_time(self):
        now = datetime.now().astimezone()
        self.assertTrue(main.friendly_datetime(now.astimezone(timezone.utc)).startswith("Today at "))


if __name__ == "__main__":
    unittest.main()
