# -*- coding: utf-8 -*-
"""Tests for deterministic reliability scoring (compute_reliability_score_and_banner)."""

import main  # noqa: F401 (ensures app module resolution)

from app.services.analyze_service import (
    _BANNER_HIGH_THRESHOLD,
    _BANNER_MEDIUM_THRESHOLD,
    _CLEAN_BONUS,
    _FREQUENCY_MULT,
    _MCP_PENALTY,
    _OVERALL_RELIABILITY_ADJUSTMENT,
    _PENALTY_CAP_FRACTION,
    _SEVERITY_PENALTY,
    _SYSTEM_TIER,
    _banner_from_score,
    _confidence_label,
    compute_reliability_score_and_banner,
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
        },
        "recalls": {"count": 0, "high_severity_count": 0, "items": [], "notes": ""},
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
# base score (no penalties) — clean car with bonus
# ---------------------------------------------------------------------------


class TestBaseScore:
    def test_clean_toyota_corolla_gets_bonus(self):
        r = compute_reliability_score_and_banner(
            _default_validated(),
            _full_risk_signals(),
        )
        assert _CLEAN_BONUS == 6
        assert r["score_0_100"] == 86
        assert r["banner_he"] == "גבוה"

    def test_confidence_label_returned(self):
        r = compute_reliability_score_and_banner(
            _default_validated(),
            _full_risk_signals(),
        )
        assert r["confidence_label"] in ("high", "medium", "low")

    def test_overall_reliability_anchor_is_wider(self):
        rs = _full_risk_signals()
        r_high = compute_reliability_score_and_banner(
            _default_validated(),
            rs,
            overall_reliability_estimate="high",
        )
        r_medium = compute_reliability_score_and_banner(
            _default_validated(),
            rs,
            overall_reliability_estimate="medium",
        )
        r_low = compute_reliability_score_and_banner(
            _default_validated(),
            rs,
            overall_reliability_estimate="low",
        )
        assert _OVERALL_RELIABILITY_ADJUSTMENT["high"] == 10
        assert _OVERALL_RELIABILITY_ADJUSTMENT["low"] == -10
        assert r_high["score_0_100"] - r_medium["score_0_100"] == 10
        assert r_medium["score_0_100"] - r_low["score_0_100"] == 10
        assert 15 <= r_low["score_0_100"] <= 100
        assert 15 <= r_medium["score_0_100"] <= 100
        assert 15 <= r_high["score_0_100"] <= 100


class TestCalibrationFallback:
    def test_model_json_calibration_is_optional_and_light(self):
        r = compute_reliability_score_and_banner(
            _default_validated(),
            _full_risk_signals(),
            overall_reliability_estimate="high",
            model_output={
                "reliability_bias": "strong",
                "recall_penalty_sensitivity": "normal",
                "calibration_confidence": "high",
            },
        )

        assert r["calibration_applied"] is True
        assert r["calibration_source"] == "model_json"
        assert -2 <= r["calibration_delta"] <= 2

    def test_missing_model_json_calibration_keeps_model_score(self):
        r = compute_reliability_score_and_banner(
            _default_validated(),
            _full_risk_signals(),
            overall_reliability_estimate="high",
        )
        assert r["calibration_applied"] is False
        assert r["calibration_source"] == "none"
        assert r["score_0_100"] == 96


# ---------------------------------------------------------------------------
# usage penalties are neutralized (no longer affect score)
# ---------------------------------------------------------------------------


class TestUsageNeutralized:
    def test_aggressive_driver_no_effect(self):
        v = _default_validated(
            {
                "usage_profile": {
                    "annual_km": 15000,
                    "city_pct": 50,
                    "driver_style": "aggressive",
                    "load": "family",
                }
            }
        )
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        clean = compute_reliability_score_and_banner(
            _default_validated(),
            _full_risk_signals(),
        )
        assert r["score_0_100"] == clean["score_0_100"]

    def test_heavy_load_no_effect(self):
        v = _default_validated(
            {
                "usage_profile": {
                    "annual_km": 15000,
                    "city_pct": 50,
                    "driver_style": "normal",
                    "load": "heavy",
                }
            }
        )
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        clean = compute_reliability_score_and_banner(
            _default_validated(),
            _full_risk_signals(),
        )
        assert r["score_0_100"] == clean["score_0_100"]

    def test_high_km_no_effect(self):
        v = _default_validated(
            {
                "usage_profile": {
                    "annual_km": 35000,
                    "city_pct": 50,
                    "driver_style": "normal",
                    "load": "family",
                }
            }
        )
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        clean = compute_reliability_score_and_banner(
            _default_validated(),
            _full_risk_signals(),
        )
        assert r["score_0_100"] == clean["score_0_100"]


# ---------------------------------------------------------------------------
# recall penalties (severity-based)
# ---------------------------------------------------------------------------


class TestRecallPenalties:
    def test_no_recalls(self):
        rs = _full_risk_signals(
            {
                "recalls": {"count": 0, "high_severity_count": 0, "items": [], "notes": ""},
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] == 86

    def test_recall_low_severity_no_penalty(self):
        """Infotainment/cosmetic recalls should not reduce score at all."""
        v = _default_validated()
        rs = _full_risk_signals(
            {
                "recalls": {
                    "count": 4,
                    "items": [
                        {"system": "infotainment", "severity": "low", "description": "עדכון תוכנה"},
                        {"system": "trim", "severity": "low", "description": "רעש פנל"},
                        {"system": "infotainment", "severity": "low", "description": "באג מסך"},
                        {"system": "infotainment", "severity": "low", "description": "בלוטות׳"},
                    ],
                    "notes": "",
                },
            }
        )
        clean_rs = _full_risk_signals()
        r_with = compute_reliability_score_and_banner(v, rs, "high")
        r_clean = compute_reliability_score_and_banner(v, clean_rs, "high")
        assert r_with["score_0_100"] == r_clean["score_0_100"], (
            "Low-severity recalls should not affect score"
        )

    def test_recall_high_severity_significant_penalty(self):
        """Engine/brakes recalls should meaningfully reduce score."""
        v = _default_validated()
        rs = _full_risk_signals(
            {
                "recalls": {
                    "count": 2,
                    "items": [
                        {"system": "engine", "severity": "high", "description": "דליפת שמן"},
                        {"system": "brakes", "severity": "high", "description": "כשל ABS"},
                    ],
                    "notes": "",
                },
            }
        )
        r = compute_reliability_score_and_banner(v, rs, "high")
        r_clean = compute_reliability_score_and_banner(v, _full_risk_signals(), "high")
        assert r["score_0_100"] < r_clean["score_0_100"] - 5

    def test_recall_medium_severity_minor_penalty(self):
        """AC/sensor recalls should stay meaningfully lighter than high-severity recalls."""
        v = _default_validated()
        rs = _full_risk_signals(
            {
                "recalls": {
                    "count": 2,
                    "items": [
                        {"system": "ac", "severity": "medium", "description": "דליפת גז"},
                        {"system": "sensors", "severity": "medium", "description": "חיישן חמצן"},
                    ],
                    "notes": "",
                },
            }
        )
        r = compute_reliability_score_and_banner(v, rs)
        r_clean = compute_reliability_score_and_banner(v, _full_risk_signals())
        diff = r_clean["score_0_100"] - r["score_0_100"]
        assert 5 < diff <= 10, (
            f"Medium recall penalty should stay moderate after bonus loss, got {diff}"
        )

    def test_recall_fallback_old_format(self):
        """Old recall format (count only, no items) should still work."""
        v = _default_validated()
        rs = _full_risk_signals(
            {
                "recalls": {"count": 3, "high_severity_count": 1, "notes": "various"},
            }
        )
        r = compute_reliability_score_and_banner(v, rs, "high")
        assert r["score_0_100"] > 0

    def test_recall_penalty_capped(self):
        """Even many high-severity recalls should hit the cap."""
        v = _default_validated()
        rs = _full_risk_signals(
            {
                "recalls": {
                    "count": 10,
                    "items": [
                        {"system": "engine", "severity": "high", "description": f"recall {i}"}
                        for i in range(10)
                    ],
                    "notes": "",
                },
            }
        )
        r = compute_reliability_score_and_banner(v, rs, "medium")
        assert r["score_0_100"] >= 40

    def test_recall_like_systemic_issue_not_double_counted_fully(self):
        rs = _full_risk_signals(
            {
                "recalls": {
                    "count": 5,
                    "items": [
                        {
                            "system": "brakes",
                            "severity": "high",
                            "description": "Official recall campaign for brake booster",
                        }
                    ],
                    "notes": "",
                },
                "systemic_issue_signals": [
                    {
                        "system": "brakes",
                        "issue": "Official recall campaign for brake booster",
                        "severity": "high",
                        "repeat_frequency": "common",
                    },
                ],
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] >= 57

    def test_recall_overlap_detected_from_notes_without_keyword(self):
        v = _default_validated({"model": "RAV4", "year": 2025})
        rs = _full_risk_signals(
            {
                "recalls": {
                    "count": 3,
                    "items": [
                        {
                            "system": "brakes",
                            "severity": "medium",
                            "description": "Brake software update due to instrument cluster blackout risk",
                        }
                    ],
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
            }
        )
        r = compute_reliability_score_and_banner(v, rs, overall_reliability_estimate="high")
        assert r["banner_he"] == "גבוה"
        assert r["score_0_100"] >= 67

    def test_strong_toyota_recall_heavy_wording_stays_realistic(self):
        v = _default_validated({"model": "Corolla Cross", "year": 2025})
        rs = _full_risk_signals(
            {
                "recalls": {
                    "count": 2,
                    "items": [
                        {
                            "system": "brakes",
                            "severity": "medium",
                            "description": "Brake actuator bolt loosening campaign",
                        }
                    ],
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
            }
        )
        r = compute_reliability_score_and_banner(v, rs, overall_reliability_estimate="high")
        assert r["banner_he"] == "גבוה"
        assert r["score_0_100"] >= 67


# ---------------------------------------------------------------------------
# systemic issues (severity × frequency × system tier)
# ---------------------------------------------------------------------------


class TestSystemicIssues:
    def test_transmission_high_common(self):
        rs = _full_risk_signals(
            {
                "systemic_issue_signals": [
                    {"system": "transmission", "severity": "high", "repeat_frequency": "common"},
                ],
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # New simplified penalty: severity value only (no freq/system multipliers)
        expected_penalty = _SEVERITY_PENALTY["high"]
        expected_score = 80 - int(round(expected_penalty))
        assert r["score_0_100"] == expected_score

    def test_frequency_and_system_multipliers_ignored(self):
        """Different frequency/system values should produce same score for same severity."""
        v = _default_validated()
        rs_common = _full_risk_signals(
            {"systemic_issue_signals": [
                {"system": "engine", "severity": "high", "repeat_frequency": "common"},
            ]}
        )
        rs_rare = _full_risk_signals(
            {"systemic_issue_signals": [
                {"system": "infotainment", "severity": "high", "repeat_frequency": "rare"},
            ]}
        )
        r_common = compute_reliability_score_and_banner(v, rs_common)
        r_rare = compute_reliability_score_and_banner(v, rs_rare)
        assert r_common["score_0_100"] == r_rare["score_0_100"]

    def test_infotainment_low_rare(self):
        rs = _full_risk_signals(
            {
                "systemic_issue_signals": [
                    {"system": "infotainment", "severity": "low", "repeat_frequency": "rare"},
                ],
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] == 85

    def test_systemic_cap(self):
        rs = _full_risk_signals(
            {
                "systemic_issue_signals": [
                    {"system": "transmission", "severity": "high", "repeat_frequency": "common"},
                    {"system": "engine", "severity": "high", "repeat_frequency": "common"},
                    {"system": "brakes", "severity": "high", "repeat_frequency": "common"},
                    {"system": "electrical", "severity": "high", "repeat_frequency": "common"},
                    {"system": "suspension", "severity": "high", "repeat_frequency": "common"},
                ],
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] >= 0
        assert r["banner_he"] == "בינוני"

    def test_medium_sometimes(self):
        rs = _full_risk_signals(
            {
                "systemic_issue_signals": [
                    {"system": "electrical", "severity": "medium", "repeat_frequency": "sometimes"},
                ],
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] == 77

    def test_vehicle_specific_neglect_claim_not_penalized(self):
        rs = _full_risk_signals(
            {
                "systemic_issue_signals": [
                    {
                        "system": "engine",
                        "issue": "Likely neglected by previous owner due to incomplete service history",
                        "severity": "high",
                        "repeat_frequency": "common",
                    },
                ],
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] == 86


# ---------------------------------------------------------------------------
# maintenance cost pressure
# ---------------------------------------------------------------------------


class TestMaintenanceCostPressure:
    def test_high(self):
        rs = _full_risk_signals(
            {
                "maintenance_cost_pressure": {
                    "level": "high",
                    "explanation": "",
                },
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        # MCP penalty disabled — maintenance cost does not affect reliability score
        assert r["score_0_100"] == 86

    def test_medium(self):
        rs = _full_risk_signals(
            {
                "maintenance_cost_pressure": {
                    "level": "medium",
                    "explanation": "",
                },
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] == 86

    def test_low(self):
        rs = _full_risk_signals(
            {
                "maintenance_cost_pressure": {
                    "level": "low",
                    "explanation": "",
                },
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] == 86

    def test_mcp_discounted_when_systemic_high(self):
        """When systemic penalty is already heavy, mcp should be discounted."""
        v = _default_validated()
        rs_heavy = _full_risk_signals(
            {
                "systemic_issue_signals": [
                    {"system": "engine", "severity": "high", "repeat_frequency": "common"},
                    {"system": "transmission", "severity": "high", "repeat_frequency": "sometimes"},
                ],
                "maintenance_cost_pressure": {"level": "high", "explanation": ""},
            }
        )
        rs_light = _full_risk_signals(
            {
                "systemic_issue_signals": [
                    {"system": "ac", "severity": "low", "repeat_frequency": "rare"},
                ],
                "maintenance_cost_pressure": {"level": "high", "explanation": ""},
            }
        )
        r_heavy = compute_reliability_score_and_banner(v, rs_heavy, "medium")
        r_light = compute_reliability_score_and_banner(v, rs_light, "medium")
        assert r_heavy["score_0_100"] >= 35, (
            f"MCP + systemic stacking too aggressive: {r_heavy['score_0_100']}"
        )
        assert r_heavy["score_0_100"] < r_light["score_0_100"]


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
        assert r["confidence_label"] == "medium"

    def test_low_confidence(self):
        rs = _full_risk_signals({"analysis_confidence": "low"})
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["confidence_label"] == "low"

    def test_confidence_does_not_affect_score(self):
        rs_high = _full_risk_signals({"analysis_confidence": "high"})
        rs_low = _full_risk_signals({"analysis_confidence": "low"})
        r_high = compute_reliability_score_and_banner(_default_validated(), rs_high)
        r_low = compute_reliability_score_and_banner(_default_validated(), rs_low)
        assert r_high["score_0_100"] == r_low["score_0_100"]

    def test_missing_confidence_defaults_medium(self):
        rs = _full_risk_signals()
        del rs["analysis_confidence"]
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["confidence_label"] == "medium"


# ---------------------------------------------------------------------------
# clean bonus
# ---------------------------------------------------------------------------


class TestCleanBonus:
    def test_clean_car_gets_generic_bonus(self):
        r = compute_reliability_score_and_banner(
            _default_validated(),
            _full_risk_signals(),
        )
        assert r["score_0_100"] == 86

    def test_clean_score_is_make_agnostic_without_dictionary(self):
        v = _default_validated({"make": "Volkswagen", "model": "Golf"})
        r = compute_reliability_score_and_banner(v, _full_risk_signals())
        assert r["score_0_100"] == 86

    def test_clean_bonus_survives_infotainment_medium(self):
        """A medium infotainment issue should NOT kill the clean bonus excessively."""
        v = _default_validated()
        rs = _full_risk_signals(
            {
                "systemic_issue_signals": [
                    {
                        "system": "infotainment",
                        "severity": "medium",
                        "repeat_frequency": "sometimes",
                    },
                ],
            }
        )
        r = compute_reliability_score_and_banner(v, rs, "high")
        r_clean = compute_reliability_score_and_banner(v, _full_risk_signals(), "high")
        # Medium infotainment = 3pt penalty + loss of clean bonus (6pt) = max 9pt drop
        assert r["score_0_100"] >= r_clean["score_0_100"] - 10, (
            f"Infotainment medium killed too many points: clean={r_clean['score_0_100']}, got={r['score_0_100']}"
        )

    def test_clean_bonus_killed_by_engine_medium(self):
        """A medium engine issue SHOULD kill the clean bonus."""
        v = _default_validated()
        rs = _full_risk_signals(
            {
                "systemic_issue_signals": [
                    {"system": "engine", "severity": "medium", "repeat_frequency": "sometimes"},
                ],
            }
        )
        r = compute_reliability_score_and_banner(v, rs, "high")
        r_clean = compute_reliability_score_and_banner(v, _full_risk_signals(), "high")
        assert r["score_0_100"] < r_clean["score_0_100"] - 4

    def test_bonus_blocked_by_meaningful_recalls(self):
        rs = _full_risk_signals(
            {
                "recalls": {"count": 4, "high_severity_count": 1, "notes": ""},
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] < 80


# ---------------------------------------------------------------------------
# penalty cap
# ---------------------------------------------------------------------------


class TestPenaltyCap:
    def test_penalties_capped_at_fraction_of_base(self):
        rs = _full_risk_signals(
            {
                "recalls": {"count": 10, "high_severity_count": 5, "notes": ""},
                "systemic_issue_signals": [
                    {"system": "transmission", "severity": "high", "repeat_frequency": "common"},
                    {"system": "engine", "severity": "high", "repeat_frequency": "common"},
                ],
                "maintenance_cost_pressure": {
                    "level": "high",
                    "explanation": "",
                },
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] >= int(round(80 * (1 - _PENALTY_CAP_FRACTION)))


# ---------------------------------------------------------------------------
# combined scenario
# ---------------------------------------------------------------------------


class TestCombinedScenario:
    def test_worst_case(self):
        rs = _full_risk_signals(
            {
                "recalls": {"count": 4, "high_severity_count": 3, "notes": ""},
                "systemic_issue_signals": [
                    {"system": "transmission", "severity": "high", "repeat_frequency": "common"},
                ],
                "maintenance_cost_pressure": {
                    "level": "high",
                    "explanation": "",
                },
            }
        )
        r = compute_reliability_score_and_banner(_default_validated(), rs)
        assert r["score_0_100"] <= 70
        assert r["banner_he"] in ("נמוך", "בינוני")


# ---------------------------------------------------------------------------
# estimate floor and calibration sensitivity
# ---------------------------------------------------------------------------


class TestEstimateFloor:
    def test_reliable_car_with_typical_llm_signals_stays_high(self):
        """A reliable car where LLM reports typical medium issues should stay high."""
        v = _default_validated()
        rs = _full_risk_signals(
            {
                "recalls": {
                    "count": 2,
                    "items": [
                        {"system": "infotainment", "severity": "low", "description": "עדכון"},
                        {"system": "sensors", "severity": "medium", "description": "חיישן"},
                    ],
                    "notes": "",
                },
                "systemic_issue_signals": [
                    {"system": "electrical", "severity": "medium", "repeat_frequency": "sometimes"},
                    {"system": "ac", "severity": "low", "repeat_frequency": "rare"},
                    {"system": "suspension", "severity": "medium", "repeat_frequency": "rare"},
                ],
                "maintenance_cost_pressure": {"level": "medium", "explanation": ""},
            }
        )
        r = compute_reliability_score_and_banner(v, rs, "high")
        assert r["banner_he"] == "גבוה", (
            f"Got {r['banner_he']} (score={r['score_0_100']}), expected גבוה"
        )

    def test_estimate_floor_disabled_by_major_systemic(self):
        """Code-side floor must NOT apply when high-severity systemic issue exists."""
        v = _default_validated()
        rs = _full_risk_signals(
            {
                "systemic_issue_signals": [
                    {"system": "engine", "severity": "high", "repeat_frequency": "common"},
                    {"system": "transmission", "severity": "high", "repeat_frequency": "common"},
                    {"system": "brakes", "severity": "high", "repeat_frequency": "common"},
                    {"system": "electrical", "severity": "high", "repeat_frequency": "common"},
                    {"system": "suspension", "severity": "high", "repeat_frequency": "common"},
                ],
                "recalls": {
                    "count": 3,
                    "items": [
                        {"system": "engine", "severity": "high", "description": "כשל מנוע"},
                        {"system": "engine", "severity": "high", "description": "דליפת שמן"},
                        {"system": "brakes", "severity": "high", "description": "כשל בלמים"},
                    ],
                    "notes": "",
                },
                "maintenance_cost_pressure": {"level": "high", "explanation": ""},
            }
        )
        r = compute_reliability_score_and_banner(v, rs, "high")
        assert r["banner_he"] != "גבוה"

    def test_calibration_sensitivity_disabled(self):
        """Calibration sensitivity scales are disabled — all settings produce same score."""
        v = _default_validated()
        rs = _full_risk_signals(
            {
                "systemic_issue_signals": [
                    {"system": "engine", "severity": "medium", "repeat_frequency": "common"},
                    {"system": "electrical", "severity": "medium", "repeat_frequency": "sometimes"},
                ],
                "maintenance_cost_pressure": {"level": "medium", "explanation": ""},
            }
        )
        model_low = {
            "systemic_penalty_sensitivity": "low",
            "recall_penalty_sensitivity": "low",
            "maintenance_penalty_sensitivity": "low",
        }
        model_high = {
            "systemic_penalty_sensitivity": "high",
            "recall_penalty_sensitivity": "high",
            "maintenance_penalty_sensitivity": "high",
        }
        r_low = compute_reliability_score_and_banner(v, rs, "medium", model_output=model_low)
        r_high = compute_reliability_score_and_banner(v, rs, "medium", model_output=model_high)
        assert r_low["score_0_100"] == r_high["score_0_100"], (
            f"Calibration scales should be disabled: low={r_low['score_0_100']}, high={r_high['score_0_100']}"
        )


# ---------------------------------------------------------------------------
# sanity checks for known vehicles
# ---------------------------------------------------------------------------


class TestSanityChecks:
    def test_toyota_corolla_clean_is_high(self):
        r = compute_reliability_score_and_banner(
            _default_validated(),
            _full_risk_signals(),
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
        rs = _full_risk_signals(
            {
                "recalls": {"count": 2, "high_severity_count": 0, "notes": ""},
                "systemic_issue_signals": [
                    {"system": "transmission", "severity": "high", "repeat_frequency": "sometimes"},
                ],
                "maintenance_cost_pressure": {
                    "level": "medium",
                    "explanation": "",
                },
            }
        )
        # With medium overall estimate (typical for VW), score stays in medium-high range
        r = compute_reliability_score_and_banner(v, rs, "medium")
        assert r["banner_he"] in ("גבוה", "בינוני")
        assert r["score_0_100"] >= 50

    def test_land_rover_many_issues_is_low(self):
        v = _default_validated({"make": "Land Rover", "model": "Discovery"})
        rs = _full_risk_signals(
            {
                "recalls": {
                    "count": 4,
                    "items": [
                        {"system": "engine", "severity": "high", "description": "כשל מנוע"},
                        {"system": "brakes", "severity": "high", "description": "כשל בלמים"},
                        {
                            "system": "transmission",
                            "severity": "high",
                            "description": "אובדן הנעה",
                        },
                    ],
                    "notes": "",
                },
                "systemic_issue_signals": [
                    {"system": "engine", "severity": "high", "repeat_frequency": "common"},
                    {"system": "electrical", "severity": "high", "repeat_frequency": "common"},
                    {"system": "suspension", "severity": "high", "repeat_frequency": "common"},
                    {"system": "transmission", "severity": "high", "repeat_frequency": "common"},
                    {"system": "cooling", "severity": "medium", "repeat_frequency": "common"},
                ],
                "maintenance_cost_pressure": {
                    "level": "high",
                    "explanation": "",
                },
            }
        )
        r = compute_reliability_score_and_banner(v, rs, "low")
        assert r["banner_he"] == "נמוך"
