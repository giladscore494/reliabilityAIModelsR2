# -*- coding: utf-8 -*-
"""Unit tests for the deterministic Gemini health verdict.

These tests use fake check results only and never call the real Gemini API.
"""

import pytest

from app.services.gemini_health_verdict import (
    ROOT_CAUSE_BLOCKED_BY_GOOGLE,
    ROOT_CAUSE_GROUNDING,
    ROOT_CAUSE_INCONCLUSIVE,
    ROOT_CAUSE_INTERACTIONS,
    ROOT_CAUSE_KEY_BASIC,
    ROOT_CAUSE_LEGACY_GROUNDING,
    ROOT_CAUSE_NO_ISSUE,
    ROOT_CAUSE_PRODUCT_FLOW,
    classify_gemini_health,
    classify_product_flow_failure,
    get_last_health_root_cause,
    set_last_health_root_cause,
)

KEY_INFO = {
    "selected_key_source": "GEMINI_API_KEY",
    "selected_key_fingerprint": "sha256:deadbeefdeadbeef",
}


def _checks(gc_plain, int_plain, int_grounded, legacy, *, status_code=None, error_summary=None):
    def mk(ok):
        c = {"ok": ok}
        if not ok and status_code is not None:
            c["status_code"] = status_code
        if not ok and error_summary is not None:
            c["error_summary"] = error_summary
        return c

    return {
        "generate_content_plain": mk(gc_plain),
        "interactions_plain": mk(int_plain),
        "interactions_grounded": mk(int_grounded),
        "generate_content_grounded_legacy": mk(legacy),
    }


def test_rule1_basic_access_failed():
    checks = _checks(False, False, False, False)
    v = classify_gemini_health(checks, KEY_INFO)
    assert v["root_cause_class"] == ROOT_CAUSE_KEY_BASIC
    assert v["is_key_problem"] is True
    assert v["is_code_call_path_problem"] is False
    assert v["is_grounding_permission_problem"] is False
    assert v["confidence"] == "high"


def test_rule2_interactions_call_path_failed():
    checks = _checks(True, False, False, False)
    v = classify_gemini_health(checks, KEY_INFO)
    assert v["root_cause_class"] == ROOT_CAUSE_INTERACTIONS
    assert v["is_key_problem"] is False
    assert v["is_code_call_path_problem"] is True
    assert v["is_grounding_permission_problem"] is False


def test_rule3_grounding_permission_failed():
    checks = _checks(True, True, False, False)
    v = classify_gemini_health(checks, KEY_INFO)
    assert v["root_cause_class"] == ROOT_CAUSE_GROUNDING
    assert v["is_key_problem"] is False
    assert v["is_code_call_path_problem"] is False
    assert v["is_grounding_permission_problem"] is True


def test_rule3_grounding_failed_even_if_legacy_works():
    # Plain + interactions plain work, grounded interactions fails -> grounding
    # problem regardless of the legacy path result.
    checks = _checks(True, True, False, True)
    v = classify_gemini_health(checks, KEY_INFO)
    assert v["root_cause_class"] == ROOT_CAUSE_GROUNDING


def test_rule4_legacy_grounding_call_path_failed():
    checks = _checks(True, True, True, False)
    v = classify_gemini_health(checks, KEY_INFO)
    assert v["root_cause_class"] == ROOT_CAUSE_LEGACY_GROUNDING
    assert v["is_key_problem"] is False
    assert v["is_code_call_path_problem"] is True
    assert v["is_grounding_permission_problem"] is False


def test_rule5_no_issue():
    checks = _checks(True, True, True, True)
    v = classify_gemini_health(checks, KEY_INFO)
    assert v["root_cause_class"] == ROOT_CAUSE_NO_ISSUE
    assert v["is_key_problem"] is False
    assert v["is_code_call_path_problem"] is False
    assert v["is_grounding_permission_problem"] is False


def test_rule7_all_403_generic_google_html():
    html = "<html><head><title>403</title></head><body>Forbidden</body></html>"
    checks = _checks(False, False, False, False, status_code=403, error_summary=html)
    v = classify_gemini_health(checks, KEY_INFO)
    assert v["root_cause_class"] == ROOT_CAUSE_BLOCKED_BY_GOOGLE
    assert v["is_key_problem"] is True
    assert v["confidence"] == "medium"


def test_rule7_requires_html_not_just_403():
    # All 403 but JSON bodies -> falls through to the basic-access rule.
    checks = _checks(False, False, False, False, status_code=403, error_summary='{"error": "no"}')
    v = classify_gemini_health(checks, KEY_INFO)
    assert v["root_cause_class"] == ROOT_CAUSE_KEY_BASIC


def test_rule8_inconclusive_on_missing_check():
    checks = _checks(True, True, True, True)
    del checks["interactions_grounded"]
    v = classify_gemini_health(checks, KEY_INFO)
    assert v["root_cause_class"] == ROOT_CAUSE_INCONCLUSIVE
    assert v["is_key_problem"] is None
    assert v["confidence"] == "low"


def test_rule8_inconclusive_on_unknown_ok():
    checks = _checks(True, True, True, True)
    checks["interactions_grounded"]["ok"] = None
    v = classify_gemini_health(checks, KEY_INFO)
    assert v["root_cause_class"] == ROOT_CAUSE_INCONCLUSIVE


def test_verdict_has_all_required_fields():
    checks = _checks(True, True, True, True)
    v = classify_gemini_health(checks, KEY_INFO)
    for field in (
        "root_cause_class",
        "is_key_problem",
        "is_code_call_path_problem",
        "is_grounding_permission_problem",
        "confidence",
        "explanation",
        "next_action",
    ):
        assert field in v


def test_product_flow_failure_when_health_clean():
    v = classify_product_flow_failure(ROOT_CAUSE_NO_ISSUE)
    assert v["root_cause_class"] == ROOT_CAUSE_PRODUCT_FLOW
    assert v["is_key_problem"] is False
    assert v["is_code_call_path_problem"] is True


def test_product_flow_failure_when_health_unknown():
    v = classify_product_flow_failure(None)
    assert v["root_cause_class"] == ROOT_CAUSE_INCONCLUSIVE
    assert v["is_key_problem"] is None


def test_last_health_root_cause_roundtrip():
    set_last_health_root_cause(ROOT_CAUSE_GROUNDING)
    assert get_last_health_root_cause() == ROOT_CAUSE_GROUNDING
    set_last_health_root_cause(None)
    assert get_last_health_root_cause() is None


@pytest.mark.parametrize(
    "combo,expected",
    [
        ((False, True, True, True), ROOT_CAUSE_KEY_BASIC),
        ((True, False, True, True), ROOT_CAUSE_INTERACTIONS),
        ((True, True, False, True), ROOT_CAUSE_GROUNDING),
        ((True, True, True, False), ROOT_CAUSE_LEGACY_GROUNDING),
        ((True, True, True, True), ROOT_CAUSE_NO_ISSUE),
    ],
)
def test_full_truth_table(combo, expected):
    checks = _checks(*combo)
    v = classify_gemini_health(checks, KEY_INFO)
    assert v["root_cause_class"] == expected
