# -*- coding: utf-8 -*-
"""Tests for Leasing Advisor feature: BIK calc, inversion, legal gating, quota, endpoints."""

import json
import io
from datetime import datetime

import pytest

from app.legal import TERMS_VERSION, PRIVACY_VERSION
from app.models import LegalAcceptance, LeasingAdvisorHistory, DailyQuotaUsage
from app.services.leasing_advisor_service import (
    calc_bik_2026,
    invert_list_price_from_bik,
    load_catalog,
    select_candidates,
    parse_company_file,
    BIK_RATE_2026,
    BIK_CAP_PRICE_2026,
    BIK_GREEN_DISCOUNTS_2026,
)
from main import db


# ── BIK Calculation Unit Tests ────────────────────────────────────────


class TestCalcBik2026:
    def test_ice_basic(self):
        result = calc_bik_2026(200000, "ice")
        expected = 200000 * BIK_RATE_2026 - 0
        assert result["monthly_bik"] == round(expected, 2)
        assert result["capped"] is False
        assert result["powertrain"] == "ice"

    def test_ev_discount(self):
        result = calc_bik_2026(200000, "ev")
        expected = 200000 * BIK_RATE_2026 - 1380
        assert result["monthly_bik"] == round(expected, 2)
        assert result["discount"] == 1380

    def test_phev_discount(self):
        result = calc_bik_2026(200000, "phev")
        expected = 200000 * BIK_RATE_2026 - 1150
        assert result["monthly_bik"] == round(expected, 2)

    def test_hybrid_discount(self):
        result = calc_bik_2026(200000, "hybrid")
        expected = 200000 * BIK_RATE_2026 - 580
        assert result["monthly_bik"] == round(expected, 2)

    def test_cap_price(self):
        """Price above cap should be clamped."""
        result = calc_bik_2026(700000, "ice")
        assert result["capped"] is True
        assert result["price_for_calc"] == BIK_CAP_PRICE_2026
        expected = BIK_CAP_PRICE_2026 * BIK_RATE_2026
        assert result["monthly_bik"] == round(expected, 2)

    def test_zero_price(self):
        result = calc_bik_2026(0, "ev")
        assert result["monthly_bik"] == 0  # max(0, negative) = 0

    def test_very_cheap_ev_floors_at_zero(self):
        """Very cheap EV: BIK = price*rate - 1380. If negative, floor at 0."""
        result = calc_bik_2026(10000, "ev")
        raw = 10000 * BIK_RATE_2026 - 1380
        assert result["monthly_bik"] == max(0, round(raw, 2))

    def test_unknown_powertrain_defaults_to_ice(self):
        result = calc_bik_2026(200000, "invalid_type")
        assert result["powertrain"] == "ice"
        assert result["discount"] == 0


class TestInvertListPrice:
    def test_known_powertrain_ev(self):
        result = invert_list_price_from_bik(3000, "ev")
        assert result["powertrain"] == "ev"
        expected_price = (3000 + 1380) / BIK_RATE_2026
        assert result["estimated_list_price"] == round(expected_price)
        assert result["capped"] is False

    def test_known_powertrain_ice(self):
        result = invert_list_price_from_bik(5000, "ice")
        assert result["powertrain"] == "ice"

    def test_capped_result(self):
        """Very high BIK should return capped flag."""
        huge_bik = BIK_CAP_PRICE_2026 * BIK_RATE_2026 + 1000
        result = invert_list_price_from_bik(huge_bik, "ice")
        assert result["capped"] is True
        assert result["estimated_list_price"] == BIK_CAP_PRICE_2026

    def test_unknown_returns_ranges(self):
        result = invert_list_price_from_bik(3000, "unknown")
        assert result["powertrain"] == "unknown"
        assert "ranges" in result
        assert "ev" in result["ranges"]
        assert "ice" in result["ranges"]


# ── Catalog & Candidate Selection Tests ───────────────────────────────


class TestCatalog:
    def test_load_catalog_returns_list(self):
        catalog = load_catalog()
        assert isinstance(catalog, list)
        assert len(catalog) > 0

    def test_catalog_rows_have_required_fields(self):
        catalog = load_catalog()
        for row in catalog[:5]:
            assert "make" in row
            assert "model" in row
            assert "list_price_ils" in row
            assert "powertrain" in row

    def test_select_candidates_no_filter(self):
        catalog = load_catalog()
        candidates = select_candidates(catalog)
        assert len(candidates) == len(catalog)

    def test_select_candidates_filter_ev(self):
        catalog = load_catalog()
        candidates = select_candidates(catalog, powertrain="ev")
        assert all(c["powertrain"] == "ev" for c in candidates)

    def test_select_candidates_filter_max_bik(self):
        catalog = load_catalog()
        candidates = select_candidates(catalog, max_bik=3000)
        for c in candidates:
            if c.get("bik"):
                assert c["bik"]["monthly_bik"] <= 3000

    def test_select_candidates_filter_body_type(self):
        catalog = load_catalog()
        candidates = select_candidates(catalog, body_type="suv")
        assert all(c.get("body_type", "").lower() == "suv" for c in candidates)


# ── File Parsing Tests ────────────────────────────────────────────────


class TestFileParser:
    def _make_csv_file(self, content, filename="test.csv"):
        from werkzeug.datastructures import FileStorage
        return FileStorage(
            stream=io.BytesIO(content.encode("utf-8")),
            filename=filename,
            content_type="text/csv",
        )

    def test_parse_csv_basic(self):
        csv_content = "make,model,list_price_ils,powertrain\nToyota,Corolla,150000,ice\n"
        f = self._make_csv_file(csv_content)
        rows, err = parse_company_file(f)
        assert err is None
        assert len(rows) == 1
        assert rows[0]["make"] == "Toyota"
        assert rows[0]["list_price_ils"] == 150000

    def test_parse_csv_hebrew_columns(self):
        csv_content = "יצרן,דגם,מחיר מחירון,הנעה\nהיונדאי,איוניק 5,250000,ev\n"
        f = self._make_csv_file(csv_content)
        rows, err = parse_company_file(f)
        assert err is None
        assert len(rows) == 1

    def test_parse_csv_empty(self):
        csv_content = "make,model\n"
        f = self._make_csv_file(csv_content)
        rows, err = parse_company_file(f)
        # Empty CSV (header only) should return 0 rows, no error
        assert len(rows) == 0

    def test_parse_unsupported_format(self):
        from werkzeug.datastructures import FileStorage
        f = FileStorage(
            stream=io.BytesIO(b"data"),
            filename="test.pdf",
            content_type="application/pdf",
        )
        rows, err = parse_company_file(f)
        assert err is not None
        assert "Unsupported" in err

    def test_parse_file_too_large(self):
        from werkzeug.datastructures import FileStorage
        big_data = b"x" * (6 * 1024 * 1024)  # 6 MB
        f = FileStorage(
            stream=io.BytesIO(big_data),
            filename="test.csv",
        )
        rows, err = parse_company_file(f)
        assert err is not None
        assert "5 MB" in err


# ── Legal Gating Tests ────────────────────────────────────────────────


class TestLeasingLegalGating:
    def test_recommend_requires_legal_acceptance(self, logged_in_client, app):
        """POST /api/leasing/recommend should be blocked without legal acceptance."""
        client, user_id = logged_in_client
        resp = client.post(
            "/api/leasing/recommend",
            json={
                "candidates": [{"make": "Toyota", "model": "Corolla"}],
                "prefs": {"driving_type": "city"},
                "legal_confirm": True,
            },
            headers={"Origin": "http://localhost"},
        )
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["error"] == "TERMS_NOT_ACCEPTED"

    def test_recommend_allowed_after_acceptance(self, logged_in_client, app):
        """After accepting legal terms, the endpoint should not return 403."""
        client, user_id = logged_in_client

        # Accept legal terms
        with app.app_context():
            db.session.add(LegalAcceptance(
                user_id=user_id,
                terms_version=TERMS_VERSION,
                privacy_version=PRIVACY_VERSION,
                accepted_at=datetime.utcnow(),
                accepted_ip="1.2.3.0",
            ))
            db.session.commit()

        resp = client.post(
            "/api/leasing/recommend",
            json={
                "candidates": [{"make": "Toyota", "model": "Corolla"}],
                "prefs": {"driving_type": "city"},
                "legal_confirm": True,
            },
            headers={"Origin": "http://localhost"},
        )
        # Should not be 403 (legal gating). May be 502 due to no AI client.
        assert resp.status_code != 403

    def test_leasing_page_accessible_without_legal(self, logged_in_client):
        """GET /leasing should work without legal acceptance (browsing allowed)."""
        client, _ = logged_in_client
        resp = client.get("/leasing")
        assert resp.status_code == 200


# ── Quota Enforcement Tests ───────────────────────────────────────────


class TestLeasingQuota:
    def test_quota_enforced_on_recommend(self, logged_in_client, app):
        """After exhausting daily quota, recommend should return DAILY_LIMIT_REACHED."""
        client, user_id = logged_in_client

        # Accept legal terms
        with app.app_context():
            db.session.add(LegalAcceptance(
                user_id=user_id,
                terms_version=TERMS_VERSION,
                privacy_version=PRIVACY_VERSION,
                accepted_at=datetime.utcnow(),
                accepted_ip="1.2.3.0",
            ))
            db.session.commit()

        # Fill up quota (set count to 5)
        from datetime import date
        with app.app_context():
            quota = DailyQuotaUsage(
                user_id=user_id,
                day=date.today(),
                count=5,
                updated_at=datetime.utcnow(),
            )
            db.session.add(quota)
            db.session.commit()

        resp = client.post(
            "/api/leasing/recommend",
            json={
                "candidates": [{"make": "Toyota", "model": "Corolla"}],
                "prefs": {"driving_type": "city"},
                "legal_confirm": True,
            },
            headers={"Origin": "http://localhost"},
        )
        assert resp.status_code == 429
        data = resp.get_json()
        assert data["error"]["code"] == "DAILY_LIMIT_REACHED"


# ── Frame Endpoint Tests ──────────────────────────────────────────────


class TestFrameEndpoint:
    def test_frame_returns_candidates_from_catalog(self, logged_in_client):
        """POST /api/leasing/frame with manual input should return candidates."""
        client, _ = logged_in_client
        resp = client.post(
            "/api/leasing/frame",
            json={"max_bik": 5000, "powertrain": "ev"},
            headers={"Origin": "http://localhost"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "data" in data
        assert "candidates" in data["data"]
        assert isinstance(data["data"]["candidates"], list)

    def test_frame_no_filter_returns_all(self, logged_in_client):
        """POST /api/leasing/frame with no filters returns full catalog."""
        client, _ = logged_in_client
        resp = client.post(
            "/api/leasing/frame",
            json={},
            headers={"Origin": "http://localhost"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["data"]["candidates"]) > 0

    def test_frame_with_list_price(self, logged_in_client):
        """POST /api/leasing/frame with list_price computes BIK frame."""
        client, _ = logged_in_client
        resp = client.post(
            "/api/leasing/frame",
            json={"list_price": 200000, "powertrain": "hybrid"},
            headers={"Origin": "http://localhost"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        frame = data["data"]["frame"]
        assert frame["source"] == "list_price"
        assert "computed_bik" in frame

    def test_frame_invalid_bik_rejected(self, logged_in_client):
        """Invalid BIK values should be rejected."""
        client, _ = logged_in_client
        resp = client.post(
            "/api/leasing/frame",
            json={"max_bik": -100},
            headers={"Origin": "http://localhost"},
        )
        assert resp.status_code == 400

    def test_frame_invalid_list_price_rejected(self, logged_in_client):
        """Invalid list price should be rejected."""
        client, _ = logged_in_client
        resp = client.post(
            "/api/leasing/frame",
            json={"list_price": 5000},  # too low (min 10000)
            headers={"Origin": "http://localhost"},
        )
        assert resp.status_code == 400


# ── Recommend Endpoint Validation Tests ───────────────────────────────


class TestRecommendValidation:
    def _accept_legal(self, app, user_id):
        with app.app_context():
            db.session.add(LegalAcceptance(
                user_id=user_id,
                terms_version=TERMS_VERSION,
                privacy_version=PRIVACY_VERSION,
                accepted_at=datetime.utcnow(),
                accepted_ip="1.2.3.0",
            ))
            db.session.commit()

    def test_recommend_requires_json(self, logged_in_client, app):
        client, user_id = logged_in_client
        self._accept_legal(app, user_id)
        # Send non-JSON content - gets blocked by legal enforcement since
        # legal_confirm cannot be parsed from text/plain body
        resp = client.post(
            "/api/leasing/recommend",
            data="not json",
            content_type="text/plain",
            headers={"Origin": "http://localhost"},
        )
        # Legal enforcement checks for legal_confirm in body;
        # text/plain body can't contain it, so 403 is expected
        assert resp.status_code in (403, 415)

    def test_recommend_requires_candidates(self, logged_in_client, app):
        client, user_id = logged_in_client
        self._accept_legal(app, user_id)
        resp = client.post(
            "/api/leasing/recommend",
            json={"prefs": {}, "legal_confirm": True},
            headers={"Origin": "http://localhost"},
        )
        assert resp.status_code == 400

    def test_recommend_requires_prefs(self, logged_in_client, app):
        client, user_id = logged_in_client
        self._accept_legal(app, user_id)
        resp = client.post(
            "/api/leasing/recommend",
            json={"candidates": [{"make": "X"}], "legal_confirm": True},
            headers={"Origin": "http://localhost"},
        )
        assert resp.status_code == 400


# ── Legal Status Endpoint Tests ───────────────────────────────────────


class TestLegalStatus:
    def test_legal_status_not_accepted(self, logged_in_client):
        client, _ = logged_in_client
        resp = client.get("/api/legal/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["accepted"] is False

    def test_legal_status_accepted(self, logged_in_client, app):
        client, user_id = logged_in_client
        with app.app_context():
            db.session.add(LegalAcceptance(
                user_id=user_id,
                terms_version=TERMS_VERSION,
                privacy_version=PRIVACY_VERSION,
                accepted_at=datetime.utcnow(),
                accepted_ip="1.2.3.0",
            ))
            db.session.commit()
        resp = client.get("/api/legal/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["accepted"] is True


# ── Migration Sanity Test ─────────────────────────────────────────────


class TestMigrationSanity:
    def test_leasing_history_table_exists(self, app):
        """Verify leasing_advisor_history table exists after create_all."""
        from sqlalchemy import inspect
        with app.app_context():
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            assert "leasing_advisor_history" in tables

    def test_leasing_history_columns(self, app):
        """Verify expected columns exist."""
        from sqlalchemy import inspect
        with app.app_context():
            inspector = inspect(db.engine)
            cols = {c["name"] for c in inspector.get_columns("leasing_advisor_history")}
            expected = {"id", "user_id", "created_at", "frame_input_json",
                        "candidates_json", "prefs_json", "gemini_response_json",
                        "request_id", "duration_ms"}
            assert expected.issubset(cols)
