import os
import sys
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import main  # noqa: E402
from app.utils.sanitization import sanitize_reliability_report_response  # noqa: E402


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


class ReliabilityReportSanitizationTests(unittest.TestCase):
    def test_reliability_report_sanitization_defaults(self):
        raw = {
            "overall_score": 120,
            "confidence": "HIGH",
            "one_sentence_verdict": "<b>טוב</b>",
            "top_risks": [
                {
                    "risk_title": "<script>כשל",
                    "why_it_matters": "סיבה",
                    "how_to_check": "בדיקה",
                    "severity": "extreme",
                    "cost_impact": "meh",
                }
            ],
            "expected_ownership_cost": {
                "maintenance_level": "ultra",
                "typical_yearly_range_ils": "₪5,000-₪7,000",
                "notes": "<note>",
            },
            "buyer_checklist": {
                "ask_seller": ["<שאלה>"],
                "inspection_focus": [],
                "walk_away_signs": [],
            },
            "what_changes_with_mileage": [
                {"mileage_band": "עד 120k", "what_to_expect": "<בדיקה>"}
            ],
            "recommended_next_step": {"action": "<go>", "reason": "<why>"},
        }
        missing_info = ["תת-דגם/תצורה"]
        payload = {"make": "Mazda", "model": "3", "year": 2020}

        sanitized = sanitize_reliability_report_response(raw, missing_info=missing_info, payload=payload)

        self.assertEqual(sanitized["overall_score"], 100)
        self.assertEqual(sanitized["confidence"], "high")
        self.assertIn("missing_info", sanitized)
        self.assertIn("תת-דגם/תצורה", sanitized["missing_info"])
        self.assertTrue(len(sanitized["top_risks"]) >= 3)
        self.assertEqual(sanitized["expected_ownership_cost"]["maintenance_level"], "medium")
        self.assertIn("&lt;b&gt;טוב", sanitized["one_sentence_verdict"])


if __name__ == "__main__":
    unittest.main()
