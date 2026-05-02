from app.utils import ai_guardrails


def test_research_without_consent_is_rejected():
    report = ai_guardrails.validate_research_submission({"consent_required": True, "consent_accepted": False})
    assert report["status"] == "critical"


def test_pii_in_research_free_text_redacted():
    result, report = ai_guardrails.apply_feature_guardrails(
        "research_collection",
        {},
        {"consent_required": True, "consent_accepted": True, "free_text": "טלפון 050-1234567"},
    )
    assert report["status"] == "critical"
    assert "[REDACTED]" in result["free_text"]


def test_extreme_repair_cost_flagged_as_outlier():
    report = ai_guardrails.validate_research_submission(
        {"consent_required": True, "consent_accepted": True, "repair_cost_ils": 99999}
    )
    assert "repair cost outlier flagged" in report["warnings"]


def test_small_sample_size_gets_caveat():
    result, report = ai_guardrails.apply_feature_guardrails(
        "research_collection",
        {},
        {"consent_required": True, "consent_accepted": True, "sample_size": 2},
    )
    assert "small sample size caveat required" in report["warnings"]
    assert result["sample_size_caveat"]


def test_user_reported_data_not_treated_as_verified():
    report = ai_guardrails.validate_research_submission(
        {
            "consent_required": True,
            "consent_accepted": True,
            "notes": "user said car is excellent",
        }
    )
    assert report["field_sources"]["notes"] == "user_reported"


def test_duplicate_report_flagged():
    report = ai_guardrails.validate_research_submission(
        {"consent_required": True, "consent_accepted": True, "duplicate_report": True}
    )
    assert "duplicate report flagged" in report["warnings"]
