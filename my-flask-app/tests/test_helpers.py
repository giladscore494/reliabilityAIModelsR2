import os
import sys
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import main  # noqa: E402


class QuotaWindowTests(unittest.TestCase):
    def test_quota_window_timezone(self):
        tz = ZoneInfo("Asia/Jerusalem")
        fixed_utc = datetime(2024, 1, 1, 21, 30, tzinfo=ZoneInfo("UTC"))

        day_key, _, _, resets_at, now_tz, retry_after = main.compute_quota_window(tz, now=fixed_utc)

        self.assertEqual(day_key, now_tz.date())
        self.assertEqual(resets_at.tzinfo, tz)
        self.assertEqual(resets_at.hour, 0)
        self.assertEqual(resets_at.minute, 0)
        self.assertEqual(resets_at.date(), now_tz.date() + timedelta(days=1))
        self.assertEqual(retry_after, max(0, int((resets_at - now_tz).total_seconds())))


class OwnerEmailNormalizationTests(unittest.TestCase):
    def test_owner_email_normalization(self):
        raw = " User@Example.com , ,ADMIN@Example.COM,,"
        parsed = main.parse_owner_emails(raw)
        self.assertEqual(parsed, ["user@example.com", "admin@example.com"])


if __name__ == "__main__":
    unittest.main()
