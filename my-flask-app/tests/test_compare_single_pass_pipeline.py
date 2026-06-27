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
    ("fuel_consumption", "צריכת דלק"),
    ("official_safety", "בטיחות"),
    ("powertrain_and_performance", "מנוע וביצועים"),
    ("reliability_and_risk", "אמינות וסיכון"),
    ("family_daily_use", "שימוש יומי/משפחתי"),
    ("resale_and_market_confidence", "סחירות ושוק"),
    ("ownership_cost", "עלויות אחזקה שוטפות"),
    ("comfort_practicality", "נוחות ושימושיות"),
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
    assert decision["choose_car_1_if"] == []
    assert decision["avoid_or_check_car_1_if"] == []


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



def test_single_pass_timeout_default_is_cloudflare_safe():
    assert comparison_service.COMPARE_SINGLE_PASS_TIMEOUT_SEC == 105
    assert comparison_service.COMPARE_SINGLE_PASS_MAX_REMOTE_CALLS == 8

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


def test_single_pass_timeout_env_override(monkeypatch):
    import importlib
    import app.services.comparison.constants as constants

    monkeypatch.setenv("COMPARE_SINGLE_PASS_TIMEOUT_SEC", "97")
    reloaded = importlib.reload(constants)
    try:
        assert reloaded.COMPARE_SINGLE_PASS_TIMEOUT_SEC == 97
    finally:
        monkeypatch.delenv("COMPARE_SINGLE_PASS_TIMEOUT_SEC", raising=False)
        importlib.reload(constants)


def test_single_pass_config_uses_json_mime():
    from app.services.comparison import grounding

    config, used = grounding._build_single_pass_config(include_json_mime=True)
    assert used is True
    assert getattr(config, "response_mime_type", None) == "application/json"
    assert getattr(config, "tools", None)


def test_single_pass_parser_unwraps_result_wrapper():
    import json
    from app.services.comparison.grounding import parse_single_pass_compare_json

    payload = _single_pass_result()[0]
    parsed, error = parse_single_pass_compare_json(json.dumps({"result": payload}, ensure_ascii=False))
    assert error is None
    assert parsed["decision_result"]["overall_decision"]["label"] == "car_1"


def test_single_pass_parser_rejects_scoring_only_output():
    import json
    from app.services.comparison.grounding import parse_single_pass_compare_json

    parsed, error = parse_single_pass_compare_json(
        json.dumps({"overall_score": {"car_1": 90}, "category_winners": {}}, ensure_ascii=False)
    )
    assert parsed is None
    assert error == "MODEL_JSON_INVALID"


def test_single_pass_repair_config_is_non_grounded_json(monkeypatch):
    import json
    from app.services.comparison import grounding

    seen = {}
    repaired = _single_pass_result(label="unknown", sources=[])[0]

    def fake_generate(**kwargs):
        config = kwargs["config"]
        seen["tools"] = getattr(config, "tools", None)
        seen["mime"] = getattr(config, "response_mime_type", None)
        seen["temperature"] = getattr(config, "temperature", None)

        class Resp:
            text = json.dumps(repaired, ensure_ascii=False)

        return Resp(), kwargs["model"], None

    monkeypatch.setattr(grounding, "_generate_content_with_404_fallback", fake_generate)
    parsed, error = grounding._attempt_single_pass_json_repair("prose with embedded facts")
    assert error is None
    assert parsed["decision_result"]["overall_decision"]["label"] == "unknown"
    assert seen["tools"] == []
    assert seen["mime"] == "application/json"
    assert seen["temperature"] == 0.0


def test_single_pass_json_invalid_triggers_repair(monkeypatch, app):
    import json
    from app.services.comparison import grounding

    class Resp:
        text = "Here is the comparison: not json"
        candidates = []

    repaired = _single_pass_result(label="unknown", sources=[])[0]
    monkeypatch.setattr(grounding.extensions, "ai_client", object())
    monkeypatch.setattr(grounding, "_extract_stage_a_grounding", lambda _resp: {"grounding_successful": True, "source_count": 1})
    monkeypatch.setattr(grounding, "_generate_content_with_404_fallback", lambda **_kwargs: (Resp(), "model", None))
    monkeypatch.setattr(grounding, "_attempt_single_pass_json_repair", lambda _raw: (repaired, None))

    with app.app_context():
        parsed, error, meta = grounding.call_gemini_single_pass_compare("prompt", timeout_sec=5)
    assert error is None
    assert parsed["decision_result"]["overall_decision"]["label"] == "unknown"
    assert meta["grounding_successful"] is True


def test_single_pass_json_invalid_repair_failure_returns_error(monkeypatch, app):
    from app.services.comparison import grounding

    class Resp:
        text = "not json"
        candidates = []

    monkeypatch.setattr(grounding.extensions, "ai_client", object())
    monkeypatch.setattr(grounding, "_extract_stage_a_grounding", lambda _resp: {"grounding_successful": True, "source_count": 1})
    monkeypatch.setattr(grounding, "_generate_content_with_404_fallback", lambda **_kwargs: (Resp(), "model", None))
    monkeypatch.setattr(grounding, "_attempt_single_pass_json_repair", lambda _raw: (None, "REPAIR_MODEL_JSON_INVALID"))

    with app.app_context():
        parsed, error, _meta = grounding.call_gemini_single_pass_compare("prompt", timeout_sec=5)
    assert parsed is None
    assert error == "MODEL_JSON_INVALID"


def test_ungrounded_empty_sources_forces_unknown_decision(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    _patch_single_pass(monkeypatch, _single_pass_result(label="car_1", sources=[], grounding=False))

    resp = _post(client)
    assert resp.status_code == 200
    decision = resp.get_json()["data"]["decision_result"]
    assert decision["overall_decision"]["label"] == "unknown"


def test_single_pass_repair_prompt_forbids_invention():
    from app.services.comparison.grounding import _build_single_pass_repair_prompt

    prompt = _build_single_pass_repair_prompt("raw response")
    assert "No new facts" in prompt
    assert "Do not invent sources" in prompt
    assert "Do not preserve the full broken JSON" in prompt


def test_single_pass_prompt_is_compact_no_legacy_hints():
    from app.services.comparison.prompts import build_single_pass_compare_prompt

    prompt = build_single_pass_compare_prompt(_two_cars())
    assert "deterministic_preference_hints" not in prompt
    assert "legacy_category_winners" not in prompt
    assert "Fill exactly the 9" not in prompt
    assert "category_decisions may be []" in prompt
    assert "[1.1.1]" in prompt


def test_single_pass_parser_accepts_compact_schema_and_defaults_missing_arrays():
    import json
    from app.services.comparison.grounding import parse_single_pass_compare_json

    payload = {
        "decision_result": {
            "overall_decision": {"label": "depends", "text": "בחירה תלויה בשימוש ובמצב הרכב."}
        },
        "sources": ["https://example.com"],
    }
    parsed, error = parse_single_pass_compare_json(json.dumps(payload, ensure_ascii=False))
    assert error is None
    decision = parsed["decision_result"]
    assert decision["category_decisions"] == []
    assert decision["key_differences"] == []
    assert decision["choose_car_1_if"] == []
    assert decision["avoid_or_check_car_2_if"] == []
    assert decision["competitors_to_consider"] == []
    assert decision["practical_summary"] == ""


def test_single_pass_local_salvage_drops_incomplete_category():
    from app.services.comparison.grounding import salvage_single_pass_compare_json

    broken = '''{"decision_result":{"overall_decision":{"label":"car_1","text":"עדיפות קלה לרכב הראשון לפי הנתונים."},"category_decisions":[{"category_key":"pricing_and_value","category_name_he":"מחיר ותמורה","preferred":"car_1","why":"זול יותר במקורות."},{"category_key":"official_safety","category_name_he":"בטיחות רשמית","preferred":"'''
    parsed = salvage_single_pass_compare_json(broken)
    assert parsed is not None
    assert parsed["decision_result"]["overall_decision"]["label"] == "car_1"
    assert len(parsed["decision_result"]["category_decisions"]) == 1


def test_checked_versions_omit_placeholder_seats():
    from app.services.comparison.normalization import public_checked_versions

    checked = public_checked_versions({
        "car_1": {"make": "Fiat", "model": "500", "seats": "לא ידוע / לבדיקה"},
        "car_2": {"make": "Fiat", "model": "Panda", "seats": "לא מאומת"},
    })

    assert "seats" not in checked["car_1"]
    assert "seats" not in checked["car_2"]


def test_checked_versions_normalize_robotized_transmission_and_omit_generic_auto():
    from app.services.comparison.normalization import public_checked_versions

    checked = public_checked_versions({
        "car_1": {"make": "Fiat", "model": "500", "transmission": "Dualogic robotized automated manual"},
        "car_2": {"make": "Toyota", "model": "Yaris", "transmission": "אוטומטית"},
    })

    assert checked["car_1"]["transmission"] == "רובוטית חד-מצמדית"
    assert "transmission" not in checked["car_2"]
    assert "אוטומטית" not in str(checked["car_1"])


def test_compare_result_template_copy_sections_and_primary_color():
    from pathlib import Path

    template = Path("templates/compare.html").read_text(encoding="utf-8")
    result_block = template[template.index("function renderCheckedVersions"):template.index("function renderComparePartialResearch")]

    assert "ההשוואה מבוססת על הגרסאות הבאות:" in result_block
    assert "הקטע מציג" not in result_block
    assert "מה לבדוק לפני החלטה" not in result_block
    assert "פחות מתאים אם" in result_block
    assert "מה לבדוק לפני קנייה" not in result_block or "avoid_or_check" not in result_block
    assert "text-primary" in result_block
    assert "text-white" not in result_block


def test_ownership_category_insurance_claim_is_sanitized():
    from app.services.comparison.decision import sanitize_decision_result

    decision = _decision_payload()
    for item in decision["category_decisions"]:
        if item["category_key"] == "ownership_cost":
            item["why"] = "עלויות הדלק, הביטוח והטיפולים נמוכות יותר ברכב הראשון."

    cleaned = sanitize_decision_result(decision, {"car_1": {}, "car_2": {}}, request_id="req")
    ownership = next(item for item in cleaned["category_decisions"] if item["category_key"] == "ownership_cost")
    assert "ביטוח" not in ownership["why"]
    assert "הטיפולים" in ownership["why"] or "אחזקה שוטפת" in ownership["why"]


def test_nine_clean_category_decisions_still_render_in_template_contract():
    decision = _decision_payload()
    assert len(decision["category_decisions"]) == 9
    assert [item["category_key"] for item in decision["category_decisions"]]


def test_single_pass_prompt_preserves_nine_categories_and_no_scoring():
    from app.services.comparison.prompts import SINGLE_PASS_COMPARE_PROMPT_BODY

    assert "exactly these 9 category_decisions" in SINGLE_PASS_COMPARE_PROMPT_BODY
    assert "pricing_and_value, fuel_consumption, official_safety, powertrain_and_performance, reliability_and_risk, family_daily_use, resale_and_market_confidence, ownership_cost, comfort_practicality" in SINGLE_PASS_COMPARE_PROMPT_BODY
    assert "No scoring" in SINGLE_PASS_COMPARE_PROMPT_BODY
