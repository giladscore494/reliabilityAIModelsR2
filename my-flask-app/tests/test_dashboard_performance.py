# -*- coding: utf-8 -*-
"""Dashboard performance, safety, and guardrail regression tests."""

import json
import logging

import pytest

from main import db
from app.models import SearchHistory


def _make_bad_payload(index: int) -> str:
    """Generate a search result payload with various unsafe fields."""
    base = {
        "summary": f"Test result {index}",
        "reliability_summary": "Overall the vehicle appears reliable.",
        "phone": "050-1234567",  # PII
        "prompt": "secret system prompt",
        "debug": {"internal_flag": True},
        "internal_score": 88,
    }
    if index % 3 == 0:
        # Add truncated text (ends with a connector — triggers truncation detection)
        base["notes"] = "This is an incomplete sentence because"
    if index % 5 == 0:
        base["prompt_text"] = "hidden prompt text"
        base["debug_info"] = {"nested": {"deep_debug": True}}
    return json.dumps(base)


class TestDashboardPerformanceSafety:
    """Regression tests for /dashboard route performance and safety."""

    def test_dashboard_returns_200_with_many_rows(self, logged_in_client, app):
        """Dashboard should load successfully even with many history rows."""
        client, user_id = logged_in_client
        client.post("/api/legal/accept", json={"legal_confirm": True})

        with app.app_context():
            for i in range(200):
                db.session.add(
                    SearchHistory(
                        user_id=user_id,
                        make="Toyota",
                        model="Corolla",
                        year=2020,
                        mileage_range="0-50k",
                        fuel_type="בנזין",
                        transmission="אוטומטית",
                        result_json=_make_bad_payload(i),
                    )
                )
            db.session.commit()

        response = client.get("/dashboard")
        assert response.status_code == 200

    def test_dashboard_does_not_expose_pii_or_debug(self, logged_in_client, app):
        """Dashboard HTML must not contain PII or internal debug fields."""
        client, user_id = logged_in_client
        client.post("/api/legal/accept", json={"legal_confirm": True})

        with app.app_context():
            for i in range(5):
                db.session.add(
                    SearchHistory(
                        user_id=user_id,
                        make="Toyota",
                        model="Corolla",
                        year=2020,
                        result_json=_make_bad_payload(i),
                    )
                )
            db.session.commit()

        response = client.get("/dashboard")
        html = response.data.decode("utf-8")
        assert "050-1234567" not in html
        assert "secret system prompt" not in html
        assert "internal_flag" not in html

    def test_dashboard_limits_rows(self, logged_in_client, app):
        """Dashboard should render at most DASHBOARD_SEARCH_LIMIT cards."""
        from app.services.history_service import DASHBOARD_SEARCH_LIMIT

        client, user_id = logged_in_client
        client.post("/api/legal/accept", json={"legal_confirm": True})

        row_count = DASHBOARD_SEARCH_LIMIT + 30
        with app.app_context():
            for i in range(row_count):
                db.session.add(
                    SearchHistory(
                        user_id=user_id,
                        make="Honda",
                        model="Civic",
                        year=2021,
                        result_json=json.dumps({"summary": f"row {i}"}),
                    )
                )
            db.session.commit()

        response = client.get("/dashboard")
        html = response.data.decode("utf-8")
        # Count occurrences of data-search-id (one per card)
        card_count = html.count("data-search-id=")
        assert card_count <= DASHBOARD_SEARCH_LIMIT

    def test_dashboard_no_per_row_guardrail_log(self, logged_in_client, app, caplog):
        """Dashboard should not emit one ai_guardrail_validation log per row."""
        client, user_id = logged_in_client
        client.post("/api/legal/accept", json={"legal_confirm": True})

        with app.app_context():
            for i in range(20):
                db.session.add(
                    SearchHistory(
                        user_id=user_id,
                        make="Mazda",
                        model="3",
                        year=2019,
                        result_json=_make_bad_payload(i),
                    )
                )
            db.session.commit()

        with caplog.at_level(logging.INFO):
            response = client.get("/dashboard")

        assert response.status_code == 200
        # There should be at most a few aggregated guardrail log lines,
        # not one per row.
        guardrail_validation_lines = [
            r for r in caplog.records
            if "ai_guardrail_validation" in r.getMessage()
        ]
        # With 20 rows, the old code would emit ~20 lines; new code emits 0.
        assert len(guardrail_validation_lines) <= 2


class TestSearchDetails:
    """Tests for the /search-details/<id> detail endpoint."""

    def test_detail_returns_200_and_redacts(self, logged_in_client, app):
        """Detail endpoint must return JSON 200 with PII redacted and debug removed."""
        client, user_id = logged_in_client
        client.post("/api/legal/accept", json={"legal_confirm": True})

        with app.app_context():
            db.session.add(
                SearchHistory(
                    user_id=user_id,
                    make="Toyota",
                    model="Corolla",
                    year=2020,
                    mileage_range="0-50k",
                    fuel_type="בנזין",
                    transmission="אוטומטית",
                    result_json=json.dumps({
                        "summary": "call 050-1234567",
                        "prompt": "hidden",
                        "debug": {"x": 1},
                        "internal_score": 99,
                    }),
                )
            )
            db.session.commit()
            search_id = SearchHistory.query.filter_by(user_id=user_id).first().id

        response = client.get(f"/search-details/{search_id}")
        assert response.status_code == 200
        body = response.get_json()
        data_section = body["data"]["data"]
        dumped = json.dumps(data_section, ensure_ascii=False)
        assert "050-1234567" not in dumped
        assert '"prompt"' not in dumped
        assert '"debug"' not in dumped
        assert '"internal_score"' not in dumped

    def test_detail_guardrail_meta_not_critical_after_repair(self, logged_in_client, app):
        """After repair, guardrail meta should not remain critical."""
        client, user_id = logged_in_client
        client.post("/api/legal/accept", json={"legal_confirm": True})

        with app.app_context():
            db.session.add(
                SearchHistory(
                    user_id=user_id,
                    make="Toyota",
                    model="Yaris",
                    year=2019,
                    result_json=json.dumps({
                        "summary": "ok",
                        "prompt": "secret",
                        "phone_field": "050-9876543",
                    }),
                )
            )
            db.session.commit()
            search_id = SearchHistory.query.filter_by(user_id=user_id).first().id

        response = client.get(f"/search-details/{search_id}")
        assert response.status_code == 200
        body = response.get_json()
        data_section = body["data"]["data"]
        meta = data_section.get("guardrail_meta", {})
        # The repair should resolve critical issues.
        assert meta.get("validation_status") != "critical" or meta.get("repaired") is True
