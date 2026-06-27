# -*- coding: utf-8 -*-
"""Integration tests for the single-pass grounded comparison pipeline.

Replaces the retired two-stage (grounding + scoring + writer) pipeline tests.
There is no scoring engine anymore: a single Google-grounded call returns the
decision_result schema and the server only sanitizes / guards it.
"""
import time

from app.services import comparison_service
from app.quota import compute_quota_window, resolve_app_timezone
from app.models import DailyQuotaUsage, ComparisonHistory
from main import db


DECISION_CATEGORIES = [
    ("pricing_and_value", "מחיר ותמורה"),
    ("trim_and_equipment", "רמות גימור ואבזור"),
    ("license_fee_and_running_cost", "אגרה ועלויות שוטפות"),
    ("fuel_consumption", "צריכת דלק/חשמל"),
    ("official_safety", "בטיחות רשמית"),
    ("powertrain_and_performance", "מכלולים וביצועים"),
    ("reliability_and_risk", "אמינות וסיכונים"),
    ("family_daily_use", "שימוש יומי ומשפחתי"),
    ("resale_and_market_confidence", "סחירות וירידת ערך"),
]


def _checked_version(make, model, transmission="אוטומטית"):
    return {
        "make": make,
        "model": model,
        "year": "2020",
        "trim": "Comfort",
        "engine_type": "בנזין",
        "transmission": transmission,
        "drivetrain": "FWD",
        "seats": "5",
        "data_basis": "verified_source",
        "confidence": "medium",
        "notes": "גרסה מייצגת לפי המידע הזמין.",
    }


def _decision_payload(slots=("car_1", "car_2"), label="car_1", fill_arrays=True):
    """Build a valid single-pass decision payload."""
    decision = {
        "overall_decision": {
            "label": label,
            "text": "קיימת עדיפות קלה לרכב הראשון לפי המידע המאומת.",
        },
        "category_decisions": [
            {
                "category_key": key,
                "category_name_he": name,
                "preferred": "car_1" if key == "pricing_and_value" else "tie",
                "why": "השוואה מנוסחת על בסיס מקורות מאומתים.",
                "important_caveat": None,
            }
            for key, name in DECISION_CATEGORIES
        ],
        "key_differences": [
            {
                "title": "עלות אחזקה",
                **{slot: "אחזקה צפויה." for slot in slots},
                "meaning_for_buyer": "משפיע על העלות השוטפת.",
            }
        ],
        "competitors_to_consider": [
            {"model": "Mazda 3", "why_consider": "חלופה שווה.", "confidence": "medium"}
        ],
        "practical_summary": "שתי אפשרויות סבירות; ההכרעה תלויה בשימוש ובמצב הרכב בפועל.",
    }
    for slot in slots:
        decision[f"choose_{slot}_if"] = (
            ["מי שמחפש אמינות ועלויות אחזקה נמוכות."] if fill_arrays else []
        )
        decision[f"avoid_or_check_{slot}_if"] = (
            ["בדקו היסטוריית תאונות וטיפולים."] if fill_arrays else []
        )
    return decision


def _single_pass_result(
    slots=("car_1", "car_2"),
    label="car_1",
    checked_versions=None,
    sources=None,
    fill_arrays=True,
    grounding=True,
):
    if checked_versions is None:
        checked_versions = {
            "car_1": _checked_version("Toyota", "Corolla"),
            "car_2": _checked_version("Honda", "Civic"),
        }
    if sources is None:
        sources = ["https://example.com/toyota", "https://example.com/honda"]
    parsed = {
        "decision_result": _decision_payload(slots, label, fill_arrays),
        "checked_versions": checked_versions,
        "sources": sources,
    }
    meta = {"grounding_successful": grounding, "source_count": len(sources)}
    return parsed, None, meta


def _patch_single_pass(monkeypatch, result):
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_single_pass_compare",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_SINGLE_PASS_TIMEOUT_SEC: result,
    )


def _two_cars():
    return [
        {"make": "Toyota", "model": "Corolla", "year": 2020},
        {"make": "Honda", "model": "Civic", "year": 2020},
    ]


def _post(client, cars=None):
    return client.post(
        "/api/compare",
        json={"cars": cars or _two_cars(), "legal_confirm": True},
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )


def test_single_pass_returns_decision_narrative_and_checked_versions(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    _patch_single_pass(monkeypatch, _single_pass_result())

    resp = _post(client)
    assert resp.status_code == 200
    payload = resp.get_json()["data"]

    assert payload["cached"] is False
    assert payload["decision_result"]["overall_decision"]["label"] == "car_1"
    assert payload["narrative"]["overall_summary"]
    assert set(payload["checked_versions"].keys()) == {"car_1", "car_2"}
    assert payload["sources_index"]["all_sources"]
    assert payload["research_status"]["grounding_successful"] is True
    # No scoring artifacts leak into the response.
    assert "overall_score" not in payload["computed_result"]
    assert "category_winners" not in payload["computed_result"]


def test_single_pass_decision_arrays_backfilled_when_model_omits_them(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    _patch_single_pass(monkeypatch, _single_pass_result(fill_arrays=False))

    resp = _post(client)
    assert resp.status_code == 200
    decision = resp.get_json()["data"]["decision_result"]
    # sanitize_decision_result backfills from the deterministic fallback.
    assert decision["choose_car_1_if"], "choose arrays must be backfilled"
    assert decision["avoid_or_check_car_1_if"]


def test_single_pass_transmission_mismatch_is_corrected(
    app, logged_in_client, monkeypatch
):
    """User selected automatic; model returns manual → final must not show ידנית."""
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    checked = {
        "car_1": _checked_version("Toyota", "Corolla", transmission="ידנית"),
        "car_2": _checked_version("Honda", "Civic", transmission="אוטומטית"),
    }
    _patch_single_pass(monkeypatch, _single_pass_result(checked_versions=checked))

    cars = [
        {"make": "Toyota", "model": "Corolla", "year": 2020, "gearbox": "אוטומטית"},
        {"make": "Honda", "model": "Civic", "year": 2020, "gearbox": "אוטומטית"},
    ]
    resp = _post(client, cars)
    assert resp.status_code == 200
    car1_transmission = resp.get_json()["data"]["checked_versions"]["car_1"][
        "transmission"
    ]
    assert "ידנית" not in car1_transmission


def test_single_pass_three_cars(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    slots = ("car_1", "car_2", "car_3")
    checked = {
        "car_1": _checked_version("Toyota", "Corolla"),
        "car_2": _checked_version("Honda", "Civic"),
        "car_3": _checked_version("Mazda", "3"),
    }
    _patch_single_pass(
        monkeypatch, _single_pass_result(slots=slots, checked_versions=checked)
    )

    cars = _two_cars() + [{"make": "Mazda", "model": "3", "year": 2020}]
    resp = _post(client, cars)
    assert resp.status_code == 200
    payload = resp.get_json()["data"]
    assert set(payload["checked_versions"].keys()) == {"car_1", "car_2", "car_3"}


def test_single_pass_unknown_floor_returns_neutral_fallback(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    # Model honours the decision floor: label unknown + clean neutral text.
    result = _single_pass_result(
        label="unknown", sources=[], grounding=False, fill_arrays=False
    )
    result[0]["decision_result"]["overall_decision"]["text"] = (
        "לא ניתן להשלים השוואה אמינה כרגע. אפשר לנסות שוב בעוד רגע או לדייק שנתון, מנוע ורמת גימור."
    )
    _patch_single_pass(monkeypatch, result)

    resp = _post(client)
    assert resp.status_code == 200
    decision = resp.get_json()["data"]["decision_result"]
    assert decision["overall_decision"]["label"] == "unknown"
    assert "לא ניתן להשלים השוואה אמינה" in decision["overall_decision"]["text"]


def test_single_pass_total_failure_returns_503_without_persisting(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    _patch_single_pass(
        monkeypatch, (None, "CALL_TIMEOUT", {"grounding_successful": False, "source_count": 0})
    )

    start = time.perf_counter()
    resp = _post(client)
    elapsed = time.perf_counter() - start
    assert resp.status_code == 503
    assert elapsed < 20
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "comparison_ai_unavailable"
    details = body["error"]["details"]
    assert details["stage"] == "single_pass"
    assert details["error_code"] == "single_pass_unavailable"
    assert details["retryable"] is True
    with app.app_context():
        assert ComparisonHistory.query.count() == 0


def test_single_pass_json_invalid_returns_503(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    _patch_single_pass(
        monkeypatch,
        (None, "MODEL_JSON_INVALID", {"grounding_successful": True, "source_count": 1}),
    )

    resp = _post(client)
    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == "comparison_ai_unavailable"
    with app.app_context():
        assert ComparisonHistory.query.count() == 0


def test_single_pass_quota_released_on_failure(app, logged_in_client, monkeypatch):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    _patch_single_pass(
        monkeypatch, (None, "CALL_TIMEOUT", {"grounding_successful": False, "source_count": 0})
    )

    resp = _post(client)
    assert resp.status_code == 503
    with app.app_context():
        tz, _ = resolve_app_timezone()
        day_key, *_ = compute_quota_window(tz)
        quota = DailyQuotaUsage.query.filter_by(user_id=user_id, day=day_key).first()
        assert quota is None or quota.count == 0


def test_single_pass_owner_bypasses_internal_history_gate(
    app, logged_in_client, monkeypatch
):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    app.config["OWNER_EMAILS"] = {"tester@example.com"}

    with app.app_context():
        for _ in range(3):
            db.session.add(
                ComparisonHistory(
                    user_id=user_id,
                    session_id=None,
                    cars_selected='[{"make":"Toyota","model":"Corolla","year":2020},{"make":"Honda","model":"Civic","year":2020}]',
                    model_json_raw="{}",
                    computed_result="{}",
                    sources_index="{}",
                )
            )
        db.session.commit()

    _patch_single_pass(monkeypatch, _single_pass_result())
    resp = _post(client)
    assert resp.status_code == 200


def test_single_pass_second_identical_request_is_cached(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    _patch_single_pass(monkeypatch, _single_pass_result())

    first = _post(client)
    assert first.status_code == 200
    assert first.get_json()["data"]["cached"] is False

    second = _post(client)
    assert second.status_code == 200
    payload = second.get_json()["data"]
    assert payload["cached"] is True
    assert payload["decision_result"]["overall_decision"]["label"] == "car_1"


def test_single_pass_timeout_constant_is_used(monkeypatch):
    seen = {}
    monkeypatch.setattr(comparison_service, "COMPARE_STAGE_A_TIMEOUT_SEC", 7)
    monkeypatch.setattr(comparison_service, "COMPARE_SINGLE_PASS_TIMEOUT_SEC", 123)
    monkeypatch.setattr(
        comparison_service,
        "_ground_call_gemini_single_pass_compare",
        lambda _prompt, timeout_sec: seen.setdefault("timeout", timeout_sec) or ({}, None, {}),
    )
    comparison_service.call_gemini_single_pass_compare("prompt")
    assert seen["timeout"] == 123


def test_fallback_decision_has_no_forbidden_placeholder_text():
    decision = comparison_service.build_deterministic_decision_result(
        {"car_1": {"display_name": "A"}, "car_2": {"display_name": "B"}}, {}, None
    )
    text = str(decision)
    assert decision["category_decisions"] == []
    assert "אין מספיק מידע מאומת" not in text
    assert "יש לאמת" not in text


def test_compare_catalog_lazy_endpoint(client):
    resp = client.get("/api/compare/catalog")
    assert resp.status_code == 200
    assert "max-age=3600" in resp.headers.get("Cache-Control", "")
    assert isinstance(resp.get_json()["data"]["catalog"], dict)


def test_compare_frontend_keeps_decision_result_for_fallback_ai_status():
    from pathlib import Path
    template = Path("templates/compare.html").read_text(encoding="utf-8")
    assert "const decision = result.decision_result || computed.decision_result || null;" in template
    assert "isPartialResearch ? buildLegacyDecisionFallback" not in template
    assert "categoriesSection.innerHTML = hasUsableDecision ? renderDecisionSections" in template


def test_active_compare_pipeline_does_not_call_legacy_scoring():
    from pathlib import Path
    pipeline = Path("app/services/comparison/pipeline.py").read_text(encoding="utf-8")
    assert "compute_comparison_results(" not in pipeline
    assert "compute_overall_score(" not in pipeline
