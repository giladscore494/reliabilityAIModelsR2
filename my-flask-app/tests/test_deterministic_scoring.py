# -*- coding: utf-8 -*-
"""Tests for deterministic reliability scoring (compute_reliability_score_and_banner)."""

import pytest
import main  # noqa: F401 (ensures app module resolution)

from app.services.analyze_service import (
    compute_reliability_score_and_banner,
    _banner_from_score,
    _confidence_label,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _default_validated(overrides=None):
    """Minimal validated payload with default usage_profile."""
    v = {
        "make": "Toyota",
        "model": "Corolla",
        "year": 2020,
        "mileage_range": "0-50k",
        "fuel_type": "בנזין",
        "transmission": "אוטומטית",
        "usage_profile": {
            "annual_km": 15000,
            "city_pct": 50,
            "driver_style": "normal",
            "load": "family",
        },
    }
    if overrides:
        v.update(overrides)
    return v


def _full_risk_signals(overrides=None):
    """Risk signals with good data, no issues."""
    rs = {
        "vehicle_resolution": {
            "generation": "E210",
            "engine_family": "2ZR-FE",
            "transmission_type": "cvt",
            "confidence": 0.9,
        },
        "recalls": {"count": 0, "high_severity_count": 0, "notes": ""},
        "systemic_issue_signals": [],
        "maintenance_cost_pressure": {
            "level": "low",
            "drivers": [],
            "evidence_strength": "strong",
        },
        "confidence_meta": {
            "data_completeness": 0.9,
            "source_quality": "high",
            "notes": "",
        },
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(rs.get(k), dict):
                rs[k].update(v)
            else:
                rs[k] = v
    return rs


# ---------------------------------------------------------------------------
# banner / confidence label helpers
# ---------------------------------------------------------------------------

class TestBannerFromScore:
    def test_high(self):
        assert _banner_from_score(80) == "גבוה"
        assert _banner_from_score(70) == "גבוה"
        assert _banner_from_score(100) == "גבוה"

    def test_medium(self):
        assert _banner_from_score(69) == "בינוני"
        assert _banner_from_score(45) == "בינוני"

    def test_low(self):
        assert _banner_from_score(44) == "נמוך"
        assert _banner_from_score(0) == "נמוך"


class TestConfidenceLabel:
    def test_high(self):
        assert _confidence_label(0.85) == "high"
        assert _confidence_label(0.80) == "high"

    def test_medium(self):
        assert _confidence_label(0.79) == "medium"
        assert _confidence_label(0.60) == "medium"

    def test_low(self):
        assert _confidence_label(0.59) == "low"
        assert _confidence_label(0.25) == "low"


# ---------------------------------------------------------------------------
# missing / malformed risk_signals => "לא ידוע"
# ---------------------------------------------------------------------------

class TestMissingRiskSignals:
    def test_none(self):
        r = compute_reliability_score_and_banner(_default_validated(), None)
        assert r["score_0_100"] == 0
        assert r["banner_he"] == "לא ידוע"
        assert r["confidence_0_1"] == 0.25

    def test_empty_dict(self):
        r = compute_reliability_score_and_banner(_default_validated(), {})
        assert r["score_0_100"] == 0
        assert r["banner_he"] == "לא ידוע"

    def test_string(self):
        r = compute_reliability_score_and_banner(_default_validated(), "bad")
        assert r["banner_he"] == "לא ידוע"

    def test_list(self):
        r = compute_reliability_score_and_banner(_default_validated(), [1, 2])
        assert r["banner_he"] == "לא ידוע"


# ---------------------------------------------------------------------------
# base score (no penalties)
# ---------------------------------------------------------------------------

class TestBaseScore:
    def test_clean_vehicle_scores_80(self):
        r = compute_reliability_score_and_banner(
            _default_validated(), _full_risk_signals()
        )
        assert r["score_0_100"] == 80
        assert r["banner_he"] == "גבוה"

    def test_confidence_high_with_good_data(self):
        r = compute_reliability_score_and_banner(
            _default_validated(), _full_risk_signals()
        )
        assert r["confidence_0_1"] >= 0.80


# ---------------------------------------------------------------------------
# usage penalties
# ---------------------------------------------------------------------------

class TestUsagePenalties:
    def test_aggressive_driver(self):
        v = _default_validated({"usage_profile": {
            "annual_km": 15000, "city_pct": 50,
            "driver_style": "aggressive", "load": "family",
        }})
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        assert r["score_0_100"] == 75  # -5

    def test_heavy_load(self):
        v = _default_validated({"usage_profile": {
            "annual_km": 15000, "city_pct": 50,
            "driver_style": "normal", "load": "heavy",
        }})
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        assert r["score_0_100"] == 75  # -5

    def test_high_km(self):
        v = _default_validated({"usage_profile": {
            "annual_km": 35000, "city_pct": 50,
            "driver_style": "normal", "load": "family",
        }})
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        assert r["score_0_100"] == 74  # -6

    def test_high_city(self):
        v = _default_validated({"usage_profile": {
            "annual_km": 15000, "city_pct": 85,
            "driver_style": "normal", "load": "family",
        }})
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        assert r["score_0_100"] == 76  # -4

    def test_usage_penalty_capped_at_20(self):
        v = _default_validated({"usage_profile": {
            "annual_km": 35000, "city_pct": 90,
            "driver_style": "aggressive", "load": "heavy",
        }})
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        assert r["score_0_100"] == 60  # -20 (capped)


# ---------------------------------------------------------------------------
# recall penalties
# ---------------------------------------------------------------------------

class TestRecallPenalties:
    def test_high_severity_recalls(self):
        rs = _full_risk_signals({
            "recalls": {"count": 2, "high_severity_count": 2, "notes": ""},
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] == 64  # -16

    def test_high_severity_capped(self):
        rs = _full_risk_signals({
            "recalls": {"count": 5, "high_severity_count": 5, "notes": ""},
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # 5*8 = 40 but capped at 24
        assert r["score_0_100"] == 56  # -24

    def test_normal_recalls(self):
        rs = _full_risk_signals({
            "recalls": {"count": 3, "high_severity_count": 0, "notes": ""},
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] == 74  # -6


# ---------------------------------------------------------------------------
# systemic issues
# ---------------------------------------------------------------------------

class TestSystemicIssues:
    def test_transmission_high_common(self):
        rs = _full_risk_signals({
            "systemic_issue_signals": [
                {"system": "transmission", "severity": "high",
                 "repeat_frequency": "common", "evidence_strength": "strong"},
            ],
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] == 62  # -18

    def test_engine_high_common_weak_evidence(self):
        rs = _full_risk_signals({
            "systemic_issue_signals": [
                {"system": "engine", "severity": "high",
                 "repeat_frequency": "common", "evidence_strength": "weak"},
            ],
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # -15 * 0.4 = -6
        assert r["score_0_100"] == 74

    def test_systemic_cap(self):
        """Many high-severity issues should cap at -40."""
        rs = _full_risk_signals({
            "systemic_issue_signals": [
                {"system": "transmission", "severity": "high",
                 "repeat_frequency": "common", "evidence_strength": "strong"},
                {"system": "engine", "severity": "high",
                 "repeat_frequency": "common", "evidence_strength": "strong"},
                {"system": "electrical", "severity": "high",
                 "repeat_frequency": "common", "evidence_strength": "strong"},
            ],
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # 18 + 15 + 8 = 41 → capped at 40
        assert r["score_0_100"] == 40


# ---------------------------------------------------------------------------
# maintenance cost pressure
# ---------------------------------------------------------------------------

class TestMaintenanceCostPressure:
    def test_high(self):
        rs = _full_risk_signals({
            "maintenance_cost_pressure": {
                "level": "high", "drivers": [], "evidence_strength": "strong",
            },
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] == 70  # -10

    def test_medium(self):
        rs = _full_risk_signals({
            "maintenance_cost_pressure": {
                "level": "medium", "drivers": [], "evidence_strength": "strong",
            },
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] == 75  # -5

    def test_low(self):
        rs = _full_risk_signals({
            "maintenance_cost_pressure": {
                "level": "low", "drivers": [], "evidence_strength": "strong",
            },
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] == 80  # no penalty


# ---------------------------------------------------------------------------
# confidence
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_low_data_completeness(self):
        rs = _full_risk_signals({
            "confidence_meta": {
                "data_completeness": 0.3,
                "source_quality": "high",
                "notes": "",
            },
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["confidence_0_1"] <= 0.65

    def test_low_source_quality(self):
        rs = _full_risk_signals({
            "confidence_meta": {
                "data_completeness": 0.9,
                "source_quality": "low",
                "notes": "",
            },
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["confidence_0_1"] <= 0.75

    def test_low_vehicle_resolution_confidence(self):
        rs = _full_risk_signals({
            "vehicle_resolution": {
                "generation": "", "engine_family": "",
                "transmission_type": "unknown", "confidence": 0.3,
            },
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["confidence_0_1"] <= 0.80

    def test_confidence_clamped(self):
        rs = _full_risk_signals({
            "confidence_meta": {
                "data_completeness": 0.1,
                "source_quality": "low",
                "notes": "",
            },
            "vehicle_resolution": {
                "generation": "", "engine_family": "",
                "transmission_type": "unknown", "confidence": 0.1,
            },
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["confidence_0_1"] >= 0.25


# ---------------------------------------------------------------------------
# combined scenario
# ---------------------------------------------------------------------------

class TestCombinedScenario:
    def test_worst_case(self):
        """Aggressive user + recalls + systemic + high maintenance → low banner."""
        v = _default_validated({"usage_profile": {
            "annual_km": 35000, "city_pct": 90,
            "driver_style": "aggressive", "load": "heavy",
        }})
        rs = _full_risk_signals({
            "recalls": {"count": 4, "high_severity_count": 3, "notes": ""},
            "systemic_issue_signals": [
                {"system": "transmission", "severity": "high",
                 "repeat_frequency": "common", "evidence_strength": "strong"},
            ],
            "maintenance_cost_pressure": {
                "level": "high", "drivers": ["חלקי חילוף יקרים"],
                "evidence_strength": "strong",
            },
        })
        r = compute_reliability_score_and_banner(v, rs)
        assert r["score_0_100"] <= 30
        assert r["banner_he"] == "נמוך"
