import os
import sys
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import main  # noqa: E402
from app.utils.sanitization import sanitize_analyze_response, sanitize_reliability_report_response  # noqa: E402


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
            "based_on_available_information": "<b>מידע חלקי</b>",
            "key_risk_areas_to_examine": [
                {
                    "risk_area": "<script>גיר",
                    "why_to_check": "סיבה",
                }
            ],
            "what_must_be_checked_before_a_decision": {
                "mechanical_inspection_points": ["<בדיקה>"],
                "documents_to_verify": [],
                "questions_to_ask_seller": ["<שאלה>"],
                "red_flags_to_look_for": [],
            },
            "known_uncertainties": ["<חסר>"],
            "estimated_cost_sensitivity": ["₪5,000-₪7,000", "<note>"],
            "final_line": "should be overwritten",
        }
        missing_info = ["תת-דגם/תצורה"]
        payload = {"make": "Mazda", "model": "3", "year": 2020}

        sanitized = sanitize_reliability_report_response(raw, missing_info=missing_info, payload=payload)

        self.assertIn("&lt;b&gt;מידע חלקי", sanitized["based_on_available_information"])
        self.assertIn("missing_info", sanitized)
        self.assertIn("תת-דגם/תצורה", sanitized["missing_info"])
        self.assertTrue(len(sanitized["key_risk_areas_to_examine"]) >= 4)
        self.assertIn("&lt;script&gt;גיר", sanitized["key_risk_areas_to_examine"][0]["risk_area"])
        self.assertIn("&lt;בדיקה&gt;", sanitized["what_must_be_checked_before_a_decision"]["mechanical_inspection_points"][0])
        self.assertIn("&lt;חסר&gt;", sanitized["known_uncertainties"][0])
        self.assertEqual(
            sanitized["final_line"],
            "This information highlights areas to verify and is not a substitute for a professional inspection.",
        )


class AnalyzeSanitizationTests(unittest.TestCase):
    def test_sanitize_analyze_response_drops_removed_sections(self):
        raw = {
            "ok": True,
            "micro_reliability": {"adjusted_score": 50},
            "timeline_plan": {"horizon_months": 36},
            "sim_model": {"defaults": {"annual_km": 12000}},
        }
        sanitized = sanitize_analyze_response(raw)
        self.assertNotIn("micro_reliability", sanitized)
        self.assertNotIn("timeline_plan", sanitized)
        self.assertNotIn("sim_model", sanitized)

    def test_sanitize_analyze_response_keeps_information_review_fields(self):
        raw = {
            "ok": True,
            "data_quality_label": "טובה",
            "decision_readiness": "מוכן לבדיקה מקצועית",
            "missing_critical_info": ["<b>ספר טיפולים</b>"],
            "verification_focus": ["<i>בדיקת גיר</i>"],
        }
        sanitized = sanitize_analyze_response(raw)
        self.assertEqual(sanitized["data_quality_label"], "טובה")
        self.assertEqual(sanitized["decision_readiness"], "מוכן לבדיקה מקצועית")
        self.assertTrue(
            any("&lt;b&gt;ספר טיפולים" in item for item in sanitized["missing_critical_info"])
        )
        self.assertIn("&lt;i&gt;בדיקת גיר", sanitized["verification_focus"][0])

    def test_sanitize_analyze_response_keeps_issue_text_in_risk_signals(self):
        raw = {
            "ok": True,
            "risk_signals": {
                "vehicle_resolution": {},
                "recalls": {"count": 2, "high_severity_count": 1, "notes": "<b>Brake campaign</b>"},
                "systemic_issue_signals": [
                    {
                        "system": "brakes",
                        "issue": "Bolt loosening risk",
                        "severity": "medium",
                        "repeat_frequency": "sometimes",
                        "typical_timing": "early ownership",
                        "evidence_text": "<i>Grounded source note</i>",
                    }
                ],
                "maintenance_cost_pressure": {"level": "medium"},
            },
        }
        sanitized = sanitize_analyze_response(raw)
        signal = sanitized["risk_signals"]["systemic_issue_signals"][0]
        self.assertEqual(signal["issue"], "Bolt loosening risk")
        self.assertIn("&lt;i&gt;", signal["evidence_text"])


if __name__ == "__main__":
    unittest.main()
