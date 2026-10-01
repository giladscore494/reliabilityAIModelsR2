# -*- coding: utf-8 -*-
"""Offline fakes for comparison V2 (no Gemini / Google Search / TypeSafe calls).

The "official page" claims below are MOCKED provider outputs used to exercise
the validation pipeline. They are not real specifications of these vehicles.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List

AUDI_Q3 = "182116040879ae9539249b243610e1db750726454cbe0eb645a7b696bf96abdf"
XPENG_P7I = "0195742780b50926c1f19aeca63601d1f6c4b17aeaa063f3dbdc00df6bd24b88"
BMW_I4 = "6107a336e30d19eca5b76f5b7ca1c6ed8b67ee6a4c70c3040a0e3b34708c66e2"
TOYOTA_SIEENA = "1fe0f557428d8114fbf3af6a317fe8a0972d8e2e7a7bdbd7915051a891d14bd2"
HYUNDAI_TUCSON = "16bb9f3825573c1745b6741cd18b23814b0c8315e16a898daf2f2092e1e57572"
MERCEDES_CLE = "ebbcdf940d27890fc82da0264e12b23f2a0eb86ca8faaad156ed11c08498bd20"
CADILLAC_ESCALADE_IQ = "c3249a13a6e515e4be50fb50bb15b653552d09b2f781027b7e080c8987bfc735"

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"


def _claim(field, value, unit, url, market, evidence, *, scope="variant", year=None, standard=None, title="עמוד רשמי"):
    claim = {
        "field": field,
        "value": value,
        "unit": unit,
        "source_url": url,
        "source_title": title,
        "source_market": market,
        # The fixtures' ``year`` is the model year the page states for the
        # specification (publication year is provenance only).
        "vehicle_model_year": year,
        "value_qualifier": "exact",
        "variant_scope": scope,
        "identity_evidence": evidence,
    }
    if standard:
        claim["measurement_standard"] = standard
    return claim


def _grounded(*hosts: str) -> List[Dict[str, Any]]:
    return [{"uri": REDIRECT + str(i), "title": h, "domain": h} for i, h in enumerate(hosts)]


_TUCSON_EV = {"model": "Tucson Hybrid", "trim": "Excellence", "powertrain": "1.6 T-GDi Hybrid 230hp", "drivetrain": "AWD HTRAC"}
_TUCSON_URL = "https://www.hyundaimotors.co.il/models/tucson-hybrid/specs"
_AUDI_EV = {"model": "Q3", "trim": "S line", "powertrain": "40 TFSI 2.0 petrol 190hp", "drivetrain": "quattro"}
_AUDI_URL = "https://www.audi.co.il/models/q3/specs"
_BMW_EV = {"model": "i4 eDrive35", "trim": "Pure", "powertrain": "electric 286hp", "drivetrain": "RWD"}
_BMW_URL = "https://www.bmw.co.il/he/all-models/i4/technical-data.html"
_XPENG_EV = {"model": "P7i", "trim": "Wing Edition", "powertrain": "electric dual motor 473hp", "drivetrain": "AWD"}
_XPENG_URL = "https://www.heyxpeng.co.il/p7i/specs"
_ESC_EV = {"model": "Escalade IQ", "trim": "Premium SPO", "powertrain": "electric dual motor 750hp", "drivetrain": "eAWD"}

MOCK_PROVIDER_OUTPUTS: Dict[str, Dict[str, Any]] = {
    HYUNDAI_TUCSON: {
        "raw": {
            "claims": [
                _claim("torque_nm", 350, "Nm", _TUCSON_URL, "IL", _TUCSON_EV, year=2026),
                _claim("acceleration_0_100_s", 8.0, "s", _TUCSON_URL, "IL", _TUCSON_EV, year=2026),
                _claim("fuel_consumption_l_100km", 6.2, "L/100km", _TUCSON_URL, "IL", _TUCSON_EV, year=2026),
                _claim("cargo_volume_l", 616, "L", _TUCSON_URL, "IL", _TUCSON_EV, year=2026),
                _claim("length_mm", 4520, "mm", _TUCSON_URL, "IL", _TUCSON_EV, year=2026),
                _claim("official_price_ils", 219990, "ILS", _TUCSON_URL, "IL", _TUCSON_EV, year=2026),
                _claim("warranty_vehicle_years", 5, "years", _TUCSON_URL, "IL", _TUCSON_EV, year=2026),
                # wrong drivetrain -> rejected
                _claim("top_speed_kmh", 193, "km/h", _TUCSON_URL, "IL", {**_TUCSON_EV, "drivetrain": "2WD"}, year=2026),
                # third-party dealer -> rejected
                _claim("wheel_size_in", 19, "in", "https://www.carzone.co.il/hyundai/tucson", "IL", _TUCSON_EV, year=2026),
                # model-generic -> diagnostics only
                _claim("height_mm", 1650, "mm", _TUCSON_URL, "IL", {"model": "Tucson"}, scope="model_generic"),
            ],
            "extra_official_equipment": [{"name": "Bose audio", "value": True, "source_url": _TUCSON_URL}],
        },
        "grounded_sources": _grounded("hyundaimotors.co.il", "carzone.co.il"),
    },
    AUDI_Q3: {
        "raw": {
            "claims": [
                _claim("torque_nm", 320, "Nm", _AUDI_URL, "IL", _AUDI_EV, year=2024),
                _claim("acceleration_0_100_s", 7.4, "s", _AUDI_URL, "IL", _AUDI_EV, year=2024),
                _claim("top_speed_kmh", 222, "km/h", _AUDI_URL, "IL", _AUDI_EV, year=2024),
                _claim("fuel_consumption_l_100km", 8.4, "L/100km", _AUDI_URL, "IL", _AUDI_EV, year=2024),
                _claim("cargo_volume_l", 530, "L", _AUDI_URL, "IL", _AUDI_EV, year=2024),
                # foreign price -> rejected (Israeli official source required)
                _claim("official_price_ils", 299000, "ILS", "https://www.audi.com/en/models/q3.html", "GLOBAL", _AUDI_EV, year=2024),
                # malicious suffix host -> rejected
                _claim("warranty_vehicle_years", 4, "years", "https://audi.co.il.evil.com/q3", "IL", _AUDI_EV, year=2024),
            ],
        },
        "grounded_sources": _grounded("audi.co.il", "audi.com"),
    },
    BMW_I4: {
        "raw": {
            "claims": [
                _claim("torque_nm", 400, "Nm", _BMW_URL, "IL", _BMW_EV, year=2024),
                _claim("acceleration_0_100_s", 6.0, "s", _BMW_URL, "IL", _BMW_EV, year=2024),
                _claim("energy_consumption_kwh_100km", 16.1, "kWh/100km", _BMW_URL, "IL", _BMW_EV, year=2024),
                _claim("battery_capacity_net_kwh", 67.1, "kWh", _BMW_URL, "IL", _BMW_EV, year=2024),
                _claim("electric_range_km", 483, "km", _BMW_URL, "IL", _BMW_EV, year=2024, standard="WLTP"),
                _claim("dc_charging_power_kw", 180, "kW", _BMW_URL, "IL", _BMW_EV, year=2024),
                _claim("ac_charging_power_kw", 11, "kW", _BMW_URL, "IL", _BMW_EV, year=2024),
            ],
        },
        "grounded_sources": _grounded("bmw.co.il"),
    },
    XPENG_P7I: {
        "raw": {
            "claims": [
                _claim("acceleration_0_100_s", 3.9, "s", _XPENG_URL, "IL", _XPENG_EV, year=2023),
                _claim("battery_capacity_net_kwh", 86.2, "kWh", _XPENG_URL, "IL", _XPENG_EV, year=2023),
                _claim("electric_range_km", 610, "km", _XPENG_URL, "IL", _XPENG_EV, year=2023, standard="CLTC"),
                _claim("dc_charging_power_kw", 175, "kW", _XPENG_URL, "IL", _XPENG_EV, year=2023),
                _claim("dc_charging_power_kw", 270, "kW", "https://www.xpeng.com/p7i", "GLOBAL", _XPENG_EV, year=2023),
                # newer model year page -> rejected (outdated exact trim handling)
                _claim("energy_consumption_kwh_100km", 15.0, "kWh/100km", _XPENG_URL, "IL", _XPENG_EV, year=2024),
            ],
        },
        "grounded_sources": _grounded("heyxpeng.co.il", "xpeng.com"),
    },
    TOYOTA_SIEENA: {
        "raw": {
            "claims": [
                # The official site spells it "Sienna"; the government record says SIEENA.
                _claim("cargo_volume_l", 949, "L", "https://www.toyota.co.il/models/sienna", "IL",
                       {"model": "Sienna", "trim": "LE Plus", "powertrain": "2.5 hybrid 245hp", "drivetrain": "FWD"}, year=2023),
            ],
        },
        "grounded_sources": _grounded("toyota.co.il"),
    },
    CADILLAC_ESCALADE_IQ: {
        "raw": {
            "claims": [
                _claim("battery_capacity_kwh", 205, "kWh", "https://www.cadillac.com/electric/escalade-iq", "GLOBAL", _ESC_EV, year=2026),
                _claim("electric_range_km", 460, "mi", "https://www.cadillac.com/electric/escalade-iq", "GLOBAL", _ESC_EV, year=2026, standard="EPA"),
                _claim("dc_charging_power_kw", 350, "kW", "https://www.cadillac.com/electric/escalade-iq", "GLOBAL", _ESC_EV, year=2026),
                # US price -> rejected
                _claim("official_price_ils", 130000, "USD", "https://www.cadillac.com/electric/escalade-iq", "GLOBAL", _ESC_EV, year=2026),
            ],
        },
        "grounded_sources": _grounded("cadillac.com"),
    },
}


class FakeEnrichmentProvider:
    """Mimics GeminiOfficialEnrichmentProvider without any network access."""

    name = "fake"
    model_id = "gemini-3.8-flash"

    def __init__(self, outputs: Dict[str, Dict[str, Any]] = None, fail_for=()):
        self.outputs = outputs if outputs is not None else MOCK_PROVIDER_OUTPUTS
        self.fail_for = set(fail_for)
        self.calls: List[Dict[str, Any]] = []

    def enrich(self, snapshot, groups):
        key = snapshot["vehicle_id"]
        self.calls.append({"vehicle_id": key, "groups": tuple(groups)})
        if key in self.fail_for:
            return {"raw": None, "grounded_sources": [], "error_code": "PROVIDER_ERROR:Timeout", "model": self.model_id}
        out = copy.deepcopy(self.outputs.get(key) or {"raw": {"claims": []}, "grounded_sources": []})
        out.setdefault("error_code", None)
        out["model"] = self.model_id
        out["duration_ms"] = 1
        return out


class _Resp:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


# Fake JEV levels: a stand-in for real judgments, driven ONLY by the profile
# and validated values in the state (never by slot names), so swapping
# vehicles yields the same readings.
FAKE_BASE_MATERIALITY = {
    "gov_safety_rating": 3, "adas_equipment": 2, "passive_safety": 2, "stability_basics": 1,
    "power_output": 2, "acceleration": 2, "top_speed": 1,
    "fuel_use": 2, "energy_use": 2,
    "battery_size": 1, "electric_range": 2, "dc_charging": 2, "ac_charging": 1,
    "cargo": 2, "towing": 1,
    "co2_wltp": 2, "co2_city_highway": 1, "pollution_class": 1, "tailpipe_other": 1,
    "price": 2, "vehicle_warranty": 1, "battery_warranty": 1,
}


def fake_materiality(group: str, buyer: Dict[str, Any]) -> int:
    level = FAKE_BASE_MATERIALITY.get(group, 2)
    use = buyer.get("main_use")
    if group in ("power_output", "acceleration", "top_speed") and use in ("highway", "long_trips"):
        level += 1
    if group == "cargo" and buyer.get("cargo_need"):
        level = {"low": 0, "medium": 2, "high": 4}[buyer["cargo_need"]]
    if group in ("fuel_use", "energy_use") and (buyer.get("annual_km") or 0) >= 25000:
        level += 1
    if group == "towing" and buyer.get("towing_braked_required_kg"):
        level = 4
    if group == "price" and buyer.get("budget_max_ils"):
        level = 3
    return max(0, min(4, level))


def fake_fit(fit_type: str, vehicle: Dict[str, Any], buyer: Dict[str, Any], factor_values: Dict[str, Any], slot: str) -> int:
    if fit_type == "parking_fit":
        length = vehicle.get("length_mm") or 0
        if buyer.get("parking_constraint") == "tight":
            return 4 if length < 4500 else 3 if length < 4700 else 2 if length < 4900 else 1
        return 3 if length < 4900 else 2
    if fit_type == "body_use_fit":
        table = {("family", "suv"): 4, ("family", "mpv"): 4, ("family", "sedan"): 3, ("city", "hatchback"): 4,
                 ("city", "suv"): 2, ("highway", "sedan"): 4, ("long_trips", "sedan"): 4, ("work", "suv"): 3}
        return table.get((buyer.get("main_use"), vehicle.get("body_style")), 2)
    if fit_type == "ground_clearance_fit":
        gc = vehicle.get("ground_clearance_mm") or 0
        return 4 if gc >= 200 else 3 if gc >= 170 else 1
    if fit_type == "charging_routine_fit":
        rng = vehicle.get("electric_range_km") or ((factor_values.get("electric_range") or {}).get("electric_range_km") or {}).get(slot) or 0
        access = buyer.get("charging_access")
        if access in ("home", "home_and_work"):
            return 4 if rng >= 400 else 3
        return {"work": 3, "public_only": 2, "none": 0}.get(access, 2)
    return 2


def score_answer(level: int, levels: int = 5, peak: float = 0.85) -> Dict[str, Any]:
    neighbours = [i for i in (level - 1, level + 1) if 0 <= i < levels]
    probs = {str(i): 0.0 for i in range(levels)}
    probs[str(level)] = peak
    for n in neighbours:
        probs[str(n)] = round((1 - peak) / len(neighbours), 6)
    expected = sum(int(k) * v for k, v in probs.items())
    return {"type": "score", "score": round(expected, 6), "confidence": peak, "probabilities": probs,
            "legend": {str(i): f"level {i}" for i in range(levels)}}


class FakeTypeSafeSession:
    """Records requests; answers /v1/models and /v1/systemone deterministically.

    Every question must be a ``score`` question; the fake answers materiality
    from the group + buyer context and fit from validated vehicle values. It
    is only a stand-in for JEV.
    """

    def __init__(self, models=None, answers_override=None, fail_systemone=False, response_model="jev-1.13.0",
                 materiality_fn=None):
        self.models = models if models is not None else {"data": [{"id": "jev-1.13.0", "aliases": ["jev-latest"]}]}
        self.answers_override = answers_override or {}
        self.fail_systemone = fail_systemone
        self.response_model = response_model
        self.materiality_fn = materiality_fn or fake_materiality
        self.get_calls: List[Dict[str, Any]] = []
        self.post_calls: List[Dict[str, Any]] = []

    def get(self, url, headers=None, timeout=None):
        self.get_calls.append({"url": url, "headers": headers})
        return _Resp(200, self.models)

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002 - mirrors requests
        self.post_calls.append({"url": url, "json": copy.deepcopy(json), "headers": headers})
        if self.fail_systemone:
            return _Resp(503, {"error": "unavailable"})
        state = json["state"]
        buyer = state["buyer_profile"]
        factor_values = state["pairwise_objective_evidence"]["factor_values"]
        answers = {}
        for qid, q in json["questions"].items():
            assert q["type"] == "score", qid
            parts = qid.split("__")
            if parts[0] == "materiality":
                level = self.materiality_fn(parts[3], buyer)
            else:
                slot, fit_type = parts[1], parts[2]
                level = fake_fit(fit_type, state["contextual_vehicle_evidence"].get(slot, {}), buyer, factor_values, slot)
            answers[qid] = score_answer(level, len(q["criteria"]))
        answers.update(copy.deepcopy(self.answers_override))
        return _Resp(200, {"model": self.response_model, "answers": answers, "usage": {"input_tokens": 1234, "output_tokens": 0}})


class FakeSummaryWriter:
    """Mimics GeminiSummaryWriter; returns a scripted output."""

    model_id = "gemini-3.8-flash"

    def __init__(self, output_fn=None, error=None):
        self.output_fn = output_fn
        self.error = error
        self.calls = 0
        self.payloads: List[Dict[str, Any]] = []

    def write(self, payload):
        self.calls += 1
        self.payloads.append(json.loads(json.dumps(payload)))
        if self.error:
            return {"output": None, "error_code": self.error, "duration_ms": 1}
        if self.output_fn:
            return {"output": self.output_fn(payload), "error_code": None, "duration_ms": 1}
        name = payload.get("recommended_name")
        if name:
            text = f"לפי הנתונים הזמינים והעדיפויות שהגדרת, {name} מתאים יותר. ההכרעה מבוססת על הנתונים המאומתים בלבד."
        else:
            text = "אין כרגע הכרעה מותאמת חד-משמעית. ההשוואה מציגה את הנתונים המאומתים בלבד."
        return {"output": {"stated_outcome": payload["outcome"], "summary_he": text}, "error_code": None, "duration_ms": 1}


def build_fake_deps(provider=None, session=None, writer=None, history=None, jev_model="jev-1.13.0"):
    from app.services.comparison_v2.cache import InProcessEnrichmentCache
    from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
    from app.services.comparison_v2.jev_client import TypeSafeJevClient, reset_model_verification_cache
    from app.services.comparison_v2.official_enrichment import LiveOfficialEnrichmentRepository
    from app.services.comparison_v2.pipeline import PipelineDeps

    reset_model_verification_cache()
    provider = provider or FakeEnrichmentProvider()
    session = session or FakeTypeSafeSession()
    jev = TypeSafeJevClient(api_key="ts-test-secret-key", model=jev_model, base_url="https://jev.test", session=session)
    return PipelineDeps(
        catalog=DemoVehicleCatalogRepository(),
        enrichment=LiveOfficialEnrichmentRepository(provider, InProcessEnrichmentCache()),
        jev_client=jev,
        summary_writer=writer if writer is not None else FakeSummaryWriter(),
        history=history,
        provider_meta={"enrichment_model": "gemini-3.8-flash", "summary_model": "gemini-3.8-flash", "jev_model": jev_model},
    )
