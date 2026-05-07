# -*- coding: utf-8 -*-
"""Focused tests for the comparison fallback payloads.

These cover the deterministic fallback narrative used when the AI writer
fails or is unavailable, and verify the partial-narrative marker. Added in
Phase 5 of the infra refactor as required by the brief.
"""
from app.services.comparison.fallbacks import (
    build_deterministic_fallback_narrative,
    mark_partial_comparison_narrative,
)


def test_fallback_narrative_has_required_keys_for_two_cars():
    cars = {
        "car_1": {"make": "Toyota", "model": "Corolla"},
        "car_2": {"make": "Honda", "model": "Civic"},
    }
    out = build_deterministic_fallback_narrative(cars, {})

    assert isinstance(out, dict)
    assert "overall_summary" in out
    assert "category_explanations" in out
    assert "disclaimers_he" in out

    assert isinstance(out["category_explanations"], list)
    assert len(out["category_explanations"]) > 0

    # Every category entry must reference both cars under "explanations".
    for cat in out["category_explanations"]:
        assert "category_key" in cat
        assert "winner" in cat
        assert "explanations" in cat
        assert set(cat["explanations"].keys()) == {"car_1", "car_2"}


def test_fallback_narrative_winner_normalizes_to_tie_when_unknown():
    cars = {"car_1": {"make": "X"}, "car_2": {"make": "Y"}}
    out = build_deterministic_fallback_narrative(cars, {})

    # With an empty computed_result, every category falls back to "tie".
    for cat in out["category_explanations"]:
        assert cat["winner"] == "tie"


def test_fallback_narrative_is_in_hebrew():
    cars = {"car_1": {}, "car_2": {}}
    out = build_deterministic_fallback_narrative(cars, {})
    # User-facing Hebrew copy must remain unchanged (frozen by the brief).
    assert out["overall_summary"] == "הסבר AI לא זמין כרגע; מוצגת השוואה מספרית."
    assert out["disclaimers_he"] == ["אפשר לנסות שוב."]


def test_mark_partial_returns_input_for_non_dict():
    # Non-dict inputs are returned unchanged (the function only mutates dicts).
    assert mark_partial_comparison_narrative(None) is None
    assert mark_partial_comparison_narrative("nope") == "nope"


def test_mark_partial_appends_disclaimer():
    narrative = {
        "overall_summary": "x",
        "category_explanations": [],
        "disclaimers_he": ["אפשר לנסות שוב."],
    }
    out = mark_partial_comparison_narrative(narrative)
    assert isinstance(out, dict)
    # The original disclaimer is preserved.
    assert "אפשר לנסות שוב." in out["disclaimers_he"]
    # A partial-narrative marker is appended (i.e. at least one disclaimer was added).
    assert len(out["disclaimers_he"]) > 1
