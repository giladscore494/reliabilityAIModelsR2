# -*- coding: utf-8 -*-
"""Tests for deterministic reliability scoring (compute_reliability_score_and_banner)."""

import pytest
import main  # noqa: F401 (ensures app module resolution)

from app.services.analyze_service import (
    compute_reliability_score_and_banner,
    _banner_from_score,
    _confidence_label,
    _classify_recall_bucket,
    _BANNER_HIGH_THRESHOLD,
    _BANNER_MEDIUM_THRESHOLD,
    _SEVERITY_PENALTY,
    _FREQUENCY_MULT,
    _SYSTEM_TIER,
    _RECALL_PENALTY,
    _MCP_PENALTY,
    _CLEAN_BONUS,
    _PENALTY_CAP_FRACTION,
)
from app.services.scoring_baseline import get_exact_model_override


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
        },
        "recalls": {"count": 0, "high_severity_count": 0, "notes": ""},
        "systemic_issue_signals": [],
        "maintenance_cost_pressure": {
            "level": "low",
            "explanation": "",
        },
        "analysis_confidence": "high",
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
        assert _banner_from_score(_BANNER_HIGH_THRESHOLD) == "גבוה"
        assert _banner_from_score(100) == "גבוה"

    def test_medium(self):
        assert _banner_from_score(_BANNER_HIGH_THRESHOLD - 1) == "בינוני"
        assert _banner_from_score(_BANNER_MEDIUM_THRESHOLD) == "בינוני"

    def test_low(self):
        assert _banner_from_score(_BANNER_MEDIUM_THRESHOLD - 1) == "נמוך"
        assert _banner_from_score(0) == "נמוך"


class TestConfidenceLabel:
    def test_categorical_high(self):
        assert _confidence_label("high") == "high"

    def test_categorical_medium(self):
        assert _confidence_label("medium") == "medium"

    def test_categorical_low(self):
        assert _confidence_label("low") == "low"

    def test_float_backward_compat_high(self):
        assert _confidence_label(0.85) == "high"
        assert _confidence_label(0.80) == "high"

    def test_float_backward_compat_medium(self):
        assert _confidence_label(0.79) == "medium"
        assert _confidence_label(0.60) == "medium"

    def test_float_backward_compat_low(self):
        assert _confidence_label(0.59) == "low"
        assert _confidence_label(0.25) == "low"


# ---------------------------------------------------------------------------
# recall bucket classification
# ---------------------------------------------------------------------------

class TestRecallBucket:
    def test_none(self):
        assert _classify_recall_bucket(0, 0) == "none"

    def test_low(self):
        assert _classify_recall_bucket(2, 0) == "low"
        assert _classify_recall_bucket(1, 0) == "low"

    def test_medium(self):
        assert _classify_recall_bucket(3, 0) == "medium"
        assert _classify_recall_bucket(1, 1) == "medium"

    def test_high(self):
        assert _classify_recall_bucket(5, 0) == "high"
        assert _classify_recall_bucket(2, 2) == "high"
        assert _classify_recall_bucket(10, 3) == "high"


# ---------------------------------------------------------------------------
# missing / malformed risk_signals => "לא ידוע"
# ---------------------------------------------------------------------------

class TestMissingRiskSignals:
    def test_none(self):
        r = compute_reliability_score_and_banner(_default_validated(), None)
        assert r["score_0_100"] == 0
        assert r["banner_he"] == "לא ידוע"
        assert r["confidence_label"] == "low"

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
# base score (no penalties) — Toyota Corolla clean with bonus
# ---------------------------------------------------------------------------

class TestBaseScore:
    def test_clean_toyota_corolla_gets_bonus(self):
        """Toyota Corolla clean: base=62+8(make)+4(model)=74, +4 bonus=78."""
        r = compute_reliability_score_and_banner(
            _default_validated(), _full_risk_signals()
        )
        # Toyota (+8 make) + Corolla (+4 model) = 74 base, +4 clean bonus = 78
        assert r["score_0_100"] == 78
        assert r["banner_he"] == "גבוה"

    def test_confidence_label_returned(self):
        r = compute_reliability_score_and_banner(
            _default_validated(), _full_risk_signals()
        )
        assert r["confidence_label"] in ("high", "medium", "low")

    def test_overall_reliability_anchor_is_modest(self):
        rs = _full_risk_signals()
        r_high = compute_reliability_score_and_banner(
            _default_validated(), rs, overall_reliability_estimate="high"
        )
        r_medium = compute_reliability_score_and_banner(
            _default_validated(), rs, overall_reliability_estimate="medium"
        )
        r_low = compute_reliability_score_and_banner(
            _default_validated(), rs, overall_reliability_estimate="low"
        )
        assert r_high["score_0_100"] - r_medium["score_0_100"] == 3
        assert r_medium["score_0_100"] - r_low["score_0_100"] == 3
        assert 15 <= r_low["score_0_100"] <= 95
        assert 15 <= r_medium["score_0_100"] <= 95
        assert 15 <= r_high["score_0_100"] <= 95


class TestCalibrationFallback:
    def test_exact_model_entry_uses_light_calibration_metadata(self):
        r = compute_reliability_score_and_banner(
            _default_validated(),
            _full_risk_signals(),
            overall_reliability_estimate="high",
        )

        assert get_exact_model_override("Toyota", "Corolla") is not None
        assert r["calibration_applied"] is True
        assert r["calibration_source"] == "model_entry"
        assert -2 <= r["calibration_delta"] <= 2

    def test_no_exact_model_entry_uses_raw_model_score(self):
        v = _default_validated({"make": "Toyota", "model": "Corolla Mystery Trim"})
        rs = _full_risk_signals()

        r = compute_reliability_score_and_banner(v, rs, overall_reliability_estimate="high")

        assert get_exact_model_override("Toyota", "Corolla Mystery Trim") is None
        assert r["calibration_applied"] is False
        assert r["calibration_source"] == "none"
        assert r["score_0_100"] == 81


# ---------------------------------------------------------------------------
# usage penalties are neutralized (no longer affect score)
# ---------------------------------------------------------------------------

class TestUsageNeutralized:
    def test_aggressive_driver_no_effect(self):
        """Usage profile should no longer affect score."""
        v = _default_validated({"usage_profile": {
            "annual_km": 15000, "city_pct": 50,
            "driver_style": "aggressive", "load": "family",
        }})
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        clean = compute_reliability_score_and_banner(
            _default_validated(), _full_risk_signals()
        )
        assert r["score_0_100"] == clean["score_0_100"]

    def test_heavy_load_no_effect(self):
        v = _default_validated({"usage_profile": {
            "annual_km": 15000, "city_pct": 50,
            "driver_style": "normal", "load": "heavy",
        }})
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        clean = compute_reliability_score_and_banner(
            _default_validated(), _full_risk_signals()
        )
        assert r["score_0_100"] == clean["score_0_100"]

    def test_high_km_no_effect(self):
        v = _default_validated({"usage_profile": {
            "annual_km": 35000, "city_pct": 50,
            "driver_style": "normal", "load": "family",
        }})
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        clean = compute_reliability_score_and_banner(
            _default_validated(), _full_risk_signals()
        )
        assert r["score_0_100"] == clean["score_0_100"]


# ---------------------------------------------------------------------------
# recall penalties (4 buckets)
# ---------------------------------------------------------------------------

class TestRecallPenalties:
    def test_no_recalls(self):
        rs = _full_risk_signals({
            "recalls": {"count": 0, "high_severity_count": 0, "notes": ""},
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # Clean Toyota Corolla with bonus
        assert r["score_0_100"] == 78

    def test_low_recalls(self):
        """1-2 recalls, no high severity → 'low' bucket → small penalty."""
        rs = _full_risk_signals({
            "recalls": {"count": 2, "high_severity_count": 0, "notes": ""},
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # base 74, recall penalty = round(1 * 0.7) = 1, bonus applies, score = 74 - 1 + 4 = 77
        assert r["score_0_100"] == 77

    def test_medium_recalls(self):
        """3+ recalls or 1 high severity → 'medium' bucket."""
        rs = _full_risk_signals({
            "recalls": {"count": 3, "high_severity_count": 1, "notes": ""},
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # Model-primary base 74 with neutral recall penalty = 5, no bonus => 69
        assert r["score_0_100"] == 69
        assert r["banner_he"] == "גבוה"

    def test_high_recalls(self):
        """5+ recalls or 2+ high severity → 'high' bucket."""
        rs = _full_risk_signals({
            "recalls": {"count": 5, "high_severity_count": 2, "notes": ""},
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # Model-primary base 74 with neutral high recall penalty = 10, no bonus => 64
        assert r["score_0_100"] == 64
        assert r["banner_he"] == "בינוני"

    def test_recall_like_systemic_issue_not_double_counted_fully(self):
        rs = _full_risk_signals({
            "recalls": {"count": 5, "high_severity_count": 2, "notes": ""},
            "systemic_issue_signals": [
                {
                    "system": "brakes",
                    "issue": "Official recall campaign for brake booster",
                    "severity": "high",
                    "repeat_frequency": "common",
                },
            ],
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # Should remain medium/high band and not collapse from stacked duplicate recall semantics
        assert r["score_0_100"] >= 57

    def test_recall_overlap_detected_from_notes_without_keyword(self):
        v = _default_validated({"model": "RAV4", "year": 2025})
        rs = _full_risk_signals({
            "recalls": {
                "count": 3,
                "high_severity_count": 1,
                "notes": "Brake software update due to instrument cluster blackout risk",
            },
            "systemic_issue_signals": [
                {
                    "system": "brakes",
                    "issue": "Braking software issue",
                    "typical_timing": "instrument cluster blackout warning",
                    "severity": "medium",
                    "repeat_frequency": "sometimes",
                },
            ],
            "maintenance_cost_pressure": {"level": "medium", "explanation": ""},
        })
        r = compute_reliability_score_and_banner(v, rs, overall_reliability_estimate="high")
        assert r["banner_he"] == "גבוה"
        assert r["score_0_100"] >= 69

    def test_strong_toyota_recall_heavy_wording_stays_realistic(self):
        v = _default_validated({"model": "Corolla Cross", "year": 2025})
        rs = _full_risk_signals({
            "recalls": {
                "count": 2,
                "high_severity_count": 1,
                "notes": "Brake actuator bolt loosening campaign",
            },
            "systemic_issue_signals": [
                {
                    "system": "brakes",
                    "issue": "Bolt loosening risk in braking actuator",
                    "severity": "medium",
                    "repeat_frequency": "sometimes",
                },
            ],
            "maintenance_cost_pressure": {"level": "medium", "explanation": ""},
        })
        r = compute_reliability_score_and_banner(v, rs, overall_reliability_estimate="high")
        assert r["banner_he"] == "גבוה"
        assert r["score_0_100"] >= 70


# ---------------------------------------------------------------------------
# systemic issues (severity × frequency × system tier)
# ---------------------------------------------------------------------------

class TestSystemicIssues:
    def test_transmission_high_common(self):
        """Critical system, high severity, common frequency."""
        rs = _full_risk_signals({
            "systemic_issue_signals": [
                {"system": "transmission", "severity": "high",
                 "repeat_frequency": "common"},
            ],
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # penalty = 7 * 1.3 * 1.25 = 11.375, no bonus, score = 74 - 11 = 63
        expected_penalty = _SEVERITY_PENALTY["high"] * _FREQUENCY_MULT["common"] * _SYSTEM_TIER["transmission"]
        expected_score = 74 - int(round(expected_penalty))
        assert r["score_0_100"] == expected_score

    def test_infotainment_low_rare(self):
        """Minor system, low severity, rare → very small penalty."""
        rs = _full_risk_signals({
            "systemic_issue_signals": [
                {"system": "infotainment", "severity": "low",
                 "repeat_frequency": "rare"},
            ],
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # penalty = 2 * 0.7 * 0.7 = 0.98, bonus still applies, score = 74 - 1 + 4 = 77
        assert r["score_0_100"] >= 77

    def test_systemic_cap(self):
        """Many high-severity issues should cap at _SYSTEMIC_PENALTY_CAP."""
        rs = _full_risk_signals({
            "systemic_issue_signals": [
                {"system": "transmission", "severity": "high",
                 "repeat_frequency": "common"},
                {"system": "engine", "severity": "high",
                 "repeat_frequency": "common"},
                {"system": "brakes", "severity": "high",
                 "repeat_frequency": "common"},
                {"system": "electrical", "severity": "high",
                 "repeat_frequency": "common"},
                {"system": "suspension", "severity": "high",
                 "repeat_frequency": "common"},
            ],
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # Even with many issues, penalty cap + base cap prevent score from going too low
        assert r["score_0_100"] >= 0
        assert r["banner_he"] == "נמוך"

    def test_medium_sometimes(self):
        """Standard severity × frequency."""
        rs = _full_risk_signals({
            "systemic_issue_signals": [
                {"system": "electrical", "severity": "medium",
                 "repeat_frequency": "sometimes"},
            ],
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # penalty = 4 * 1.0 * 1.0 = 4, no bonus (has issues), score = 74 - 4 = 70
        assert r["score_0_100"] == 70

    def test_vehicle_specific_neglect_claim_not_penalized(self):
        rs = _full_risk_signals({
            "systemic_issue_signals": [
                {
                    "system": "engine",
                    "issue": "Likely neglected by previous owner due to incomplete service history",
                    "severity": "high",
                    "repeat_frequency": "common",
                },
            ],
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # Unverified vehicle-specific neglect assumptions should not add penalties
        assert r["score_0_100"] == 78


# ---------------------------------------------------------------------------
# maintenance cost pressure
# ---------------------------------------------------------------------------

class TestMaintenanceCostPressure:
    def test_high(self):
        rs = _full_risk_signals({
            "maintenance_cost_pressure": {
                "level": "high", "explanation": "",
            },
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # Model-primary flow uses neutral maintenance pressure penalty (no make multiplier)
        expected_penalty = _MCP_PENALTY["high"]
        expected_score = 74 - int(round(expected_penalty))
        assert r["score_0_100"] == expected_score

    def test_medium(self):
        rs = _full_risk_signals({
            "maintenance_cost_pressure": {
                "level": "medium", "explanation": "",
            },
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # Model-primary flow uses neutral maintenance pressure penalty (no make multiplier)
        expected_penalty = _MCP_PENALTY["medium"]
        expected_score = 74 - int(round(expected_penalty))
        assert r["score_0_100"] == expected_score

    def test_low(self):
        rs = _full_risk_signals({
            "maintenance_cost_pressure": {
                "level": "low", "explanation": "",
            },
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # mcp penalty = 0, bonus applies, score = 74 + 4 = 78
        assert r["score_0_100"] == 78


# ---------------------------------------------------------------------------
# confidence (categorical, does not affect score)
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_high_confidence_from_llm(self):
        rs = _full_risk_signals({"analysis_confidence": "high"})
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["confidence_label"] == "high"

    def test_medium_confidence(self):
        rs = _full_risk_signals({"analysis_confidence": "medium"})
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # Model override exists for Toyota Corolla, so medium gets boosted to high
        assert r["confidence_label"] == "high"

    def test_low_confidence(self):
        rs = _full_risk_signals({"analysis_confidence": "low"})
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["confidence_label"] == "low"

    def test_confidence_does_not_affect_score(self):
        """Score must be identical regardless of confidence level."""
        rs_high = _full_risk_signals({"analysis_confidence": "high"})
        rs_low = _full_risk_signals({"analysis_confidence": "low"})
        r_high = compute_reliability_score_and_banner(_default_validated(), rs_high)
        r_low = compute_reliability_score_and_banner(_default_validated(), rs_low)
        assert r_high["score_0_100"] == r_low["score_0_100"]

    def test_missing_confidence_defaults_medium(self):
        rs = _full_risk_signals()
        del rs["analysis_confidence"]
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # Default medium, but model override boosts to high
        assert r["confidence_label"] == "high"


# ---------------------------------------------------------------------------
# clean bonus
# ---------------------------------------------------------------------------

class TestCleanBonus:
    def test_bonus_eligible_clean_gets_bonus(self):
        """Toyota (bonus_eligible=True) with no issues gets +4."""
        r = compute_reliability_score_and_banner(
            _default_validated(), _full_risk_signals()
        )
        # base=74, no penalties, +4 bonus = 78
        assert r["score_0_100"] == 78

    def test_non_bonus_eligible_no_bonus(self):
        """Without exact model calibration pressure, clean signals keep the raw model score."""
        v = _default_validated({"make": "Volkswagen", "model": "Golf"})
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        assert r["score_0_100"] == 78

    def test_bonus_blocked_by_issues(self):
        """Toyota with systemic issues should not get bonus."""
        rs = _full_risk_signals({
            "systemic_issue_signals": [
                {"system": "engine", "severity": "medium",
                 "repeat_frequency": "sometimes"},
            ],
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # penalty = 4 * 1.0 * 1.25 = 5, no bonus, score = 74 - 5 = 69
        assert r["score_0_100"] == 69

    def test_bonus_blocked_by_meaningful_recalls(self):
        """Toyota with medium+ recalls should not get bonus."""
        rs = _full_risk_signals({
            "recalls": {"count": 4, "high_severity_count": 1, "notes": ""},
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # medium recall bucket, no bonus
        assert r["score_0_100"] < 74


# ---------------------------------------------------------------------------
# penalty cap
# ---------------------------------------------------------------------------

class TestPenaltyCap:
    def test_penalties_capped_at_fraction_of_base(self):
        """Total penalties should not exceed _PENALTY_CAP_FRACTION of base."""
        rs = _full_risk_signals({
            "recalls": {"count": 10, "high_severity_count": 5, "notes": ""},
            "systemic_issue_signals": [
                {"system": "transmission", "severity": "high",
                 "repeat_frequency": "common"},
                {"system": "engine", "severity": "high",
                 "repeat_frequency": "common"},
            ],
            "maintenance_cost_pressure": {
                "level": "high", "explanation": "",
            },
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # base=74, penalty cap = 74*0.55 = 40.7, score >= 74-41 = 33
        assert r["score_0_100"] >= int(round(74 * (1 - _PENALTY_CAP_FRACTION)))


# ---------------------------------------------------------------------------
# combined scenario
# ---------------------------------------------------------------------------

class TestCombinedScenario:
    def test_worst_case(self):
        """Recalls + systemic + high maintenance → low banner."""
        rs = _full_risk_signals({
            "recalls": {"count": 4, "high_severity_count": 3, "notes": ""},
            "systemic_issue_signals": [
                {"system": "transmission", "severity": "high",
                 "repeat_frequency": "common"},
            ],
            "maintenance_cost_pressure": {
                "level": "high", "explanation": "",
            },
        })
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] <= 55
        assert r["banner_he"] in ("נמוך", "בינוני")


# ---------------------------------------------------------------------------
# sanity checks for known vehicles
# ---------------------------------------------------------------------------

class TestSanityChecks:
    def test_toyota_corolla_clean_is_high(self):
        r = compute_reliability_score_and_banner(
            _default_validated(), _full_risk_signals()
        )
        assert r["banner_he"] == "גבוה"

    def test_mazda_cx5_clean_is_high(self):
        v = _default_validated({"make": "Mazda", "model": "CX-5"})
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        assert r["banner_he"] == "גבוה"

    def test_subaru_forester_clean_is_high(self):
        v = _default_validated({"make": "Subaru", "model": "Forester"})
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        assert r["banner_he"] == "גבוה"

    def test_vw_golf_dsg_issues_is_medium(self):
        """VW Golf with DSG issues should not be excessively collapsed."""
        v = _default_validated({"make": "Volkswagen", "model": "Golf"})
        rs = _full_risk_signals({
            "recalls": {"count": 2, "high_severity_count": 0, "notes": ""},
            "systemic_issue_signals": [
                {"system": "transmission", "severity": "high",
                 "repeat_frequency": "sometimes"},
            ],
            "maintenance_cost_pressure": {
                "level": "medium", "explanation": "",
            },
        })
        r = compute_reliability_score_and_banner(v, rs)
        # With lighter recall history (low bucket), VW Golf stays medium
        assert r["banner_he"] == "בינוני"
        assert r["score_0_100"] >= 40

    def test_land_rover_many_issues_is_low(self):
        v = _default_validated({"make": "Land Rover", "model": "Discovery"})
        rs = _full_risk_signals({
            "recalls": {"count": 6, "high_severity_count": 2, "notes": ""},
            "systemic_issue_signals": [
                {"system": "electrical", "severity": "high",
                 "repeat_frequency": "common"},
                {"system": "suspension", "severity": "medium",
                 "repeat_frequency": "common"},
            ],
            "maintenance_cost_pressure": {
                "level": "high", "explanation": "",
            },
        })
        r = compute_reliability_score_and_banner(v, rs)
        assert r["banner_he"] == "נמוך"
