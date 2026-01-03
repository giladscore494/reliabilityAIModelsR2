import main
from app.utils.micro_reliability import compute_micro_reliability
from app.utils.timeline_plan import build_timeline_plan


def _base_usage(**overrides):
    usage = {
        "annual_km": 15000,
        "city_pct": 50,
        "terrain": "mixed",
        "climate": "center",
        "parking": "outdoor",
        "driver_style": "normal",
        "load": "family",
    }
    usage.update(overrides)
    return usage


def test_micro_risk_hot_and_city():
    base_report = {"base_score_calculated": 70}
    hot_usage = _base_usage(climate="south_hot", parking="outdoor")
    micro_hot = compute_micro_reliability(base_report, hot_usage)
    subs_hot = [r.get("subsystem") for r in micro_hot.get("top_risks", [])]
    assert "ac" in subs_hot or "battery_electrical" in subs_hot

    city_usage = _base_usage(city_pct=90)
    micro_city = compute_micro_reliability(base_report, city_usage)
    subs_city = [r.get("subsystem") for r in micro_city.get("top_risks", [])]
    assert "brakes" in subs_city or "suspension" in subs_city
    assert 0 <= micro_city["adjusted_score"] <= 100


def test_timeline_brake_risk_pull_in():
    usage = _base_usage()
    micro = {"top_risks": [{"subsystem": "brakes", "level": "high"}], "delta": 0}
    plan = build_timeline_plan(usage, micro, {"mileage_range": "0-50k"})
    actions = [a for ph in plan["phases"] for a in ph.get("actions", [])]
    assert any("brake" in (a.get("name") or "") for a in actions)
    assert "totals_by_phase" in plan
    assert isinstance(plan["totals_by_phase"].get("0_3", [0])[0], int)


def test_analyze_response_includes_new_sections(logged_in_client, monkeypatch):
    client, _ = logged_in_client
    calls = {"count": 0}

    def fake_gemini(_prompt):
        calls["count"] += 1
        return (
            {
                "ok": True,
                "base_score_calculated": 80,
                "search_performed": True,
                "search_queries": [],
                "sources": [],
                "reliability_report": {},
            },
            None,
        )

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_gemini)

    payload = {
        "make": "Toyota",
        "model": "Corolla",
        "year": 2020,
        "mileage_range": "0-50k",
        "fuel_type": "בנזין",
        "transmission": "אוטומטית",
        "sub_model": "",
    }

    resp1 = client.post("/analyze", json=payload)
    data1 = resp1.get_json()
    assert resp1.status_code == 200
    assert data1["data"]["micro_reliability"]
    assert data1["data"]["timeline_plan"]
    assert data1["data"]["sim_model"]

    resp2 = client.post("/analyze", json=payload)
    data2 = resp2.get_json()
    assert resp2.status_code == 200
    assert calls["count"] == 1  # cache hit should skip second AI call
    assert data2["data"]["micro_reliability"]
