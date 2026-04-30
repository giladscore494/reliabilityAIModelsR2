# -*- coding: utf-8 -*-
"""
Contract tests for the Data Quality Indicator.

Tests operate at the payload / template-render level (not on script.js
strings) to stay robust against JS refactoring.
"""

import json
import pytest

from app.utils.sanitization import sanitize_analyze_response, derive_information_status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_response(**overrides):
    """Minimal valid /analyze-style response dict."""
    base = {
        "ok": True,
        "search_performed": True,
        "sources": [],
        "reliability_report": {},
        "data_quality_label": "חלקית",
        "decision_readiness": "נדרש אימות נוסף",
        "missing_critical_info": [],
        "verification_focus": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. derive_information_status returns required fields
# ---------------------------------------------------------------------------

class TestDeriveInformationStatus:
    def test_returns_source_count(self):
        result = derive_information_status({"sources": []})
        assert "source_count" in result
        assert isinstance(result["source_count"], int)

    def test_returns_source_scope_label(self):
        result = derive_information_status({"sources": []})
        assert "source_scope_label" in result
        assert isinstance(result["source_scope_label"], str)

    def test_returns_weakly_sourced(self):
        result = derive_information_status({"sources": []})
        assert "weakly_sourced" in result
        assert isinstance(result["weakly_sourced"], bool)

    def test_returns_data_quality_label(self):
        result = derive_information_status({"sources": [], "data_quality_label": "טובה"})
        assert "data_quality_label" in result

    def test_returns_decision_readiness(self):
        result = derive_information_status({"sources": [], "decision_readiness": "מוכן לבדיקה מקצועית"})
        assert "decision_readiness" in result

    def test_weakly_sourced_true_when_zero_sources(self):
        result = derive_information_status({"sources": []})
        assert result["weakly_sourced"] is True
        assert result["source_count"] == 0

    def test_weakly_sourced_true_when_one_source(self):
        result = derive_information_status({"sources": [{"title": "foo", "url": "https://example.com"}]})
        assert result["weakly_sourced"] is True
        assert result["source_count"] == 1

    def test_weakly_sourced_false_when_two_or_more_sources(self):
        sources = [
            {"title": "foo", "url": "https://example.com"},
            {"title": "bar", "url": "https://other.com"},
        ]
        result = derive_information_status({"sources": sources})
        assert result["weakly_sourced"] is False
        assert result["source_count"] == 2

    def test_source_scope_label_israeli(self):
        sources = [{"title": "test", "url": "https://ynet.co.il"}]
        result = derive_information_status({"sources": sources})
        assert result["source_scope_label"] == "ישראליים"

    def test_source_scope_label_global(self):
        sources = [{"title": "test", "url": "https://example.com"}]
        result = derive_information_status({"sources": sources})
        assert result["source_scope_label"] == "גלובליים"

    def test_source_scope_label_mixed(self):
        sources = [
            {"title": "il", "url": "https://ynet.co.il"},
            {"title": "en", "url": "https://carsguide.com.au"},
        ]
        result = derive_information_status({"sources": sources})
        assert result["source_scope_label"] == "ישראליים וגלובליים"

    def test_source_scope_label_unknown_when_no_sources(self):
        result = derive_information_status({"sources": []})
        assert result["source_scope_label"] == "לא זוהה"


# ---------------------------------------------------------------------------
# 2. sanitize_analyze_response passes data_quality fields through
# ---------------------------------------------------------------------------

class TestSanitizeAnalyzeResponsePassesFields:
    def test_data_quality_label_good_passes(self):
        out = sanitize_analyze_response(_base_response(data_quality_label="טובה"))
        assert out.get("data_quality_label") == "טובה"

    def test_data_quality_label_partial_passes(self):
        out = sanitize_analyze_response(_base_response(data_quality_label="חלקית"))
        assert out.get("data_quality_label") == "חלקית"

    def test_data_quality_label_missing_passes(self):
        out = sanitize_analyze_response(_base_response(data_quality_label="חסרה"))
        assert out.get("data_quality_label") == "חסרה"

    def test_source_count_in_output(self):
        out = sanitize_analyze_response(_base_response())
        assert "source_count" in out

    def test_source_scope_label_in_output(self):
        out = sanitize_analyze_response(_base_response())
        assert "source_scope_label" in out

    def test_weakly_sourced_in_output(self):
        out = sanitize_analyze_response(_base_response())
        assert "weakly_sourced" in out

    def test_decision_readiness_in_output(self):
        out = sanitize_analyze_response(_base_response(
            decision_readiness="מוכן לבדיקה מקצועית"
        ))
        assert "decision_readiness" in out

    def test_weakly_sourced_true_when_no_sources(self):
        out = sanitize_analyze_response(_base_response(sources=[]))
        assert out["weakly_sourced"] is True
        assert out["source_count"] == 0

    def test_no_source_chip_data_when_zero_sources(self):
        """When source_count==0, no source count is provided (fallback state)."""
        out = sanitize_analyze_response(_base_response(sources=[]))
        assert out["source_count"] == 0

    def test_weakly_sourced_chip_when_few_sources(self):
        """weakly_sourced=True when source_count < 2."""
        sources = [{"title": "one", "url": "https://example.com"}]
        out = sanitize_analyze_response(_base_response(sources=sources))
        assert out["weakly_sourced"] is True

    def test_good_quality_label_in_output(self):
        """When data_quality_label='טובה', the sanitised response preserves it."""
        out = sanitize_analyze_response(_base_response(data_quality_label="טובה"))
        assert out["data_quality_label"] == "טובה"

    def test_deprecated_score_keys_absent(self):
        """Deprecated score fields must not appear in sanitised output."""
        deprecated = [
            "model_reliability_score", "deal_risk_score", "banner_he",
            "score_0_100", "base_score_calculated", "estimated_reliability",
            "model_reliability_label", "deal_risk_label",
        ]
        inp = _base_response(**{k: "some_value" for k in deprecated})
        out = sanitize_analyze_response(inp)
        for k in deprecated:
            assert k not in out, f"Deprecated key '{k}' leaked into sanitized output"


# ---------------------------------------------------------------------------
# 3. Template-level: example.html renders with score container id
# ---------------------------------------------------------------------------

class TestExampleTemplateScoreContainerId:
    def test_example_template_has_score_container(self, app, client):
        """The example template must contain reliability-score-container."""
        with app.app_context():
            from main import db, SearchHistory
            row = SearchHistory(
                user_id=1,
                make="Toyota",
                model="Corolla",
                year=2018,
                mileage_range="50k-100k",
                fuel_type="בנזין",
                transmission="אוטומטית",
                is_public_example=True,
                example_slug="toyota-corolla-2018-test",
                result_json=json.dumps({
                    "ok": True,
                    "search_performed": True,
                    "sources": [],
                    "reliability_report": {},
                    "data_quality_label": "טובה",
                    "decision_readiness": "מוכן לבדיקה מקצועית",
                    "missing_critical_info": [],
                    "verification_focus": [],
                }),
            )
            db.session.add(row)
            db.session.commit()

        resp = client.get("/example/toyota-corolla-2018-test")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'id="reliability-score-container"' in html

    def test_example_template_has_script_js(self, app, client):
        """The example page must include script.js so the indicator renders."""
        with app.app_context():
            from main import db, SearchHistory
            row = SearchHistory(
                user_id=1,
                make="Honda",
                model="Civic",
                year=2019,
                mileage_range="50k-100k",
                fuel_type="בנזין",
                transmission="אוטומטית",
                is_public_example=True,
                example_slug="honda-civic-2019-test",
                result_json=json.dumps({
                    "ok": True,
                    "search_performed": True,
                    "sources": [],
                    "reliability_report": {},
                    "data_quality_label": "חלקית",
                    "decision_readiness": "נדרש אימות נוסף",
                    "missing_critical_info": [],
                    "verification_focus": [],
                }),
            )
            db.session.add(row)
            db.session.commit()

        resp = client.get("/example/honda-civic-2019-test")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "script.js" in html


# ---------------------------------------------------------------------------
# 4. script.js contains the DataQualityIndicator function markers
# ---------------------------------------------------------------------------

class TestScriptJsContainsDQIMarkers:
    def test_build_data_quality_indicator_present(self, client):
        resp = client.get("/static/script.js")
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "buildDataQualityIndicator" in body

    def test_aria_meter_role_present(self, client):
        resp = client.get("/static/script.js")
        body = resp.get_data(as_text=True)
        assert "role" in body and "meter" in body

    def test_aria_valuenow_present(self, client):
        resp = client.get("/static/script.js")
        body = resp.get_data(as_text=True)
        assert "aria-valuenow" in body

    def test_system_disclaimer_present(self, client):
        resp = client.get("/static/script.js")
        body = resp.get_data(as_text=True)
        assert "המערכת לא קובעת אם לקנות את הרכב, אלא מציפה מה לבדוק" in body

    def test_weakly_sourced_chip_logic_present(self, client):
        resp = client.get("/static/script.js")
        body = resp.get_data(as_text=True)
        assert "weaklySourced" in body

    def test_fallback_aria_busy_present(self, client):
        resp = client.get("/static/script.js")
        body = resp.get_data(as_text=True)
        assert "aria-busy" in body
