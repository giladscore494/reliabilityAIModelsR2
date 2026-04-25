import json
import pytest

from app.utils.http_helpers import api_ok
from app.legal import PRIVACY_VERSION, TERMS_VERSION
from app.extensions import db
from app.models import (
    AdvisorHistory,
    ComparisonHistory,
    ResearchConsent,
    ResearchResponse,
    ResearchResponseSession,
)
from app.research import FIELD_CLASSIFICATION, RESEARCH_QUESTION_VERSION, validate_research_payload
from app.utils.validation import ValidationError


def _advisor_headers(client):
    with client.session_transaction() as sess:
        sess["csrf_token"] = "a" * 64
    return {"X-CSRF-Token": "a" * 64}


def test_research_consent_supports_anonymous_client(client, app):
    resp = client.post(
        "/api/research/consent",
        json={"research_confirm": True, "accepted_source": "reliability_results"},
        headers={"User-Agent": "pytest-agent", "Accept-Language": "he-IL"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    with app.app_context():
        consent = ResearchConsent.query.one()
        assert consent.user_id is None
        assert consent.anon_id
        assert consent.accepted_user_agent == "pytest-agent"
        assert consent.terms_version == TERMS_VERSION
        assert consent.privacy_version == PRIVACY_VERSION


def test_research_response_requires_consent(logged_in_client):
    client, _ = logged_in_client
    resp = client.post(
        "/api/research/responses",
        json={
            "flow_type": "reliability",
            "source_analysis_type": "search_history",
            "source_record_id": 1,
            "vehicle_context": {"fuel_type": "בנזין"},
            "responses": [
                {
                    "question_code": "ownership_status",
                    "response": {"ownership_status": "owner"},
                },
                {
                    "question_code": "maintenance_profile",
                    "response": {
                        "garage_type": "authorized",
                        "last_service_cost_ils": 1200,
                    },
                },
                {
                    "question_code": "first_test_pass",
                    "response": {"first_test_pass": True},
                },
                {
                    "question_code": "out_of_warranty_repairs",
                    "response": {"out_of_warranty_repairs": False},
                },
            ],
        },
    )
    assert resp.status_code == 412
    assert resp.get_json()["error"] == "RESEARCH_CONSENT_REQUIRED"


def test_research_response_persists_session_and_answers_for_logged_in_user(
    logged_in_client, app
):
    client, user_id = logged_in_client
    consent_resp = client.post(
        "/api/research/consent",
        json={"research_confirm": True, "accepted_source": "compare_results"},
    )
    consent_id = consent_resp.get_json()["consent_id"]

    with app.app_context():
        comparison = ComparisonHistory(
            user_id=user_id,
            cars_selected=json.dumps(
                [{"make": "Toyota", "model": "Corolla", "year": 2020}],
                ensure_ascii=False,
            ),
        )
        db.session.add(comparison)
        db.session.commit()
        comparison_id = comparison.id

    resp = client.post(
        "/api/research/responses",
        json={
            "consent_id": consent_id,
            "flow_type": "compare",
            "source_analysis_type": "compare_history",
            "source_record_id": comparison_id,
            "vehicle_context": {"cars": [{"make": "Toyota", "model": "Corolla"}]},
            "responses": [
                {
                    "question_code": "subject_vehicle",
                    "response": {"subject_vehicle_slot": "car_1"},
                },
                {
                    "question_code": "annual_insurance",
                    "response": {"annual_insurance_ils": 6500},
                },
                {
                    "question_code": "annual_total_cost",
                    "response": {"annual_total_cost_ils": 18000},
                },
                {
                    "question_code": "owner_satisfaction",
                    "response": {"satisfaction_score": 9, "would_buy_again": True},
                },
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["saved_count"] == 4

    with app.app_context():
        session_record = ResearchResponseSession.query.one()
        assert session_record.user_id == user_id
        assert session_record.flow_type == "compare"
        assert session_record.source_record_id == comparison_id
        assert (
            ResearchResponse.query.filter_by(session_id=session_record.id).count() == 4
        )


def test_reliability_and_compare_pages_render_research_panels(logged_in_client):
    client, _ = logged_in_client
    reliability_resp = client.get("/app")
    compare_resp = client.get("/compare")
    assert reliability_resp.status_code == 200
    assert compare_resp.status_code == 200
    reliability_html = reliability_resp.data.decode("utf-8")
    compare_html = compare_resp.data.decode("utf-8")
    assert 'id="reliabilityResultReadyPanel"' in reliability_html
    assert 'id="reliabilityOpenResultButton"' in reliability_html
    assert 'id="reliabilityResearchSection"' in reliability_html
    assert 'id="reliabilityResearchSection" class="mt-6 hidden' in reliability_html
    assert 'id="researchConsentModal"' in reliability_html
    assert 'id="compareResultReadyPanel"' in compare_html
    assert 'id="compareOpenResultButton"' in compare_html
    assert 'id="compareResearchSection"' in compare_html
    assert 'id="compareResearchSection" class="mt-6 hidden' in compare_html
    assert 'id="researchConsentModal"' in compare_html


def test_owner_advisor_page_renders_research_fields(logged_in_client, app):
    client, user_id = logged_in_client
    with app.app_context():
        app.config["OWNER_EMAILS"] = {"tester@example.com"}
    resp = client.get("/recommendations")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert 'id="advisorResultReadyPanel"' in html
    assert 'id="advisorOpenResultButton"' in html
    assert 'id="advisorResearchSection"' in html
    assert 'id="advisorResearchCurrentVehicle"' in html
    assert 'id="advisorResearchSection" class="mt-6 hidden' in html
    assert 'id="advisorResearchAnswerNow"' in html
    assert "אפשרויות מתקדמות (אופציונלי)" in html
    form_index = html.index('id="advisor-form"')
    research_index = html.index('id="advisorResearchSection"')
    assert research_index > form_index
    assert (
        "כדי לבדוק כדאיות עסקה על רכב ספציפי, יש להיכנס לבודק האמינות."
        in html
    )
    assert (
        "Fit Score = רמת התאמה להעדפות שהוזנו בשאלון, ולא ציון אמינות או כדאיות קנייה."
        in html
    )


def test_owner_can_submit_advisor_without_research_fields(logged_in_client, app, monkeypatch):
    client, _ = logged_in_client
    app.config["OWNER_EMAILS"] = {"tester@example.com"}
    client.post("/api/legal/accept", json={"legal_confirm": True})

    monkeypatch.setattr(
        "app.routes.advisor_routes.advisor_service.handle_advisor_logic",
        lambda payload, user, user_id: api_ok({"received": payload}),
    )

    resp = client.post(
        "/advisor_api",
        json={
            "budget_min": 20000,
            "budget_max": 120000,
            "year_min": 2012,
            "year_max": 2025,
            "fuels_he": ["בנזין"],
            "gears_he": ["אוטומטית"],
                "annual_km": 15000,
                "legal_confirm": True,
                "weights": {
                "reliability": 5,
                "resale": 3,
                "fuel": 4,
                "performance": 2,
                "comfort": 3,
            },
        },
        headers=_advisor_headers(client),
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True


def test_regular_user_can_submit_advisor_without_research_fields(logged_in_client, app, monkeypatch):
    client, _ = logged_in_client
    app.config["ADVISOR_OWNER_ONLY"] = False
    app.config["OWNER_EMAILS"] = set()
    client.post("/api/legal/accept", json={"legal_confirm": True})
    monkeypatch.setattr(
        "app.routes.advisor_routes.advisor_service.handle_advisor_logic",
        lambda payload, user, user_id: api_ok({"received": payload}),
    )

    resp = client.post(
        "/advisor_api",
        json={
            "budget_min": 20000,
            "budget_max": 120000,
            "year_min": 2012,
            "year_max": 2025,
            "fuels_he": ["בנזין"],
            "gears_he": ["אוטומטית"],
                "annual_km": 15000,
                "legal_confirm": True,
                "weights": {
                "reliability": 5,
                "resale": 3,
                "fuel": 4,
                "performance": 2,
                "comfort": 3,
            },
        },
        headers=_advisor_headers(client),
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True


def test_advisor_research_responses_reject_unknown_question_code(logged_in_client, app):
    client, user_id = logged_in_client
    consent_resp = client.post(
        "/api/research/consent",
        json={"research_confirm": True, "accepted_source": "advisor_after_result"},
    )
    consent_id = consent_resp.get_json()["consent_id"]

    with app.app_context():
        history = AdvisorHistory(
            user_id=user_id,
            profile_json=json.dumps({"budget_min": 20000}),
            result_json=json.dumps({"recommended_cars": []}),
        )
        db.session.add(history)
        db.session.commit()
        history_id = history.id

    resp = client.post(
        "/api/research/responses",
        json={
            "consent_id": consent_id,
            "flow_type": "advisor",
            "source_analysis_type": "advisor_history",
            "source_record_id": history_id,
            "vehicle_context": {"advisor_history_id": history_id},
            "responses": [
                {
                    "question_code": "totally_unknown",
                    "response": {"value": "x"},
                }
            ],
        },
    )

    assert resp.status_code == 400
    assert "Unknown research question_code" in resp.get_json()["message"]


def test_research_field_classification_exposes_service_vs_research_boundaries():
    assert FIELD_CLASSIFICATION["make"] == "service_required"
    assert FIELD_CLASSIFICATION["driver_gender"] == "service_optional"
    assert FIELD_CLASSIFICATION["insurance_history"] == "service_optional"
    assert FIELD_CLASSIFICATION["violations"] == "service_optional"
    assert FIELD_CLASSIFICATION["current_vehicle"] == "research_optional"
    assert FIELD_CLASSIFICATION["maintenance_cost_bucket"] == "research_optional"


def test_owner_profile_validation_does_not_fall_through_advisor_questions():
    with pytest.raises(ValidationError) as exc:
        validate_research_payload(
            "owner_profile",
            [
                {
                    "question_code": "current_vehicle",
                    "response": {"current_vehicle": "Toyota Corolla"},
                }
            ],
            {},
        )

    assert "Unknown research question_code" in exc.value.message


def test_research_frontend_scripts_store_seen_state_for_same_history(client):
    reliability_js = client.get("/static/script.js").get_data(as_text=True)
    advisor_js = client.get("/static/recommendations.js").get_data(as_text=True)

    assert "research_prompt_seen_" in reliability_js
    assert "research_prompt_seen_" in advisor_js
    assert RESEARCH_QUESTION_VERSION == "research_v2_after_value_2026_04_25"
