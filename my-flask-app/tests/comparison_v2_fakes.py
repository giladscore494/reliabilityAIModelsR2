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
        "source_year": year,
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


class FakeTypeSafeSession:
    """Records requests; answers /v1/models and /v1/systemone deterministically.

    The fake "judgment" picks the car leading the most correlation groups in
    the deterministic evidence it was sent (tie when equal, insufficient when
    there are no compared groups). It is only a stand-in for JEV.
    """

    def __init__(self, models=None, answers_override=None, fail_systemone=False, response_model="jev-1.13.0"):
        self.models = models if models is not None else {"data": [{"id": "jev-1.13.0", "aliases": ["jev-latest"]}]}
        self.answers_override = answers_override or {}
        self.fail_systemone = fail_systemone
        self.response_model = response_model
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
        slots = [k for k in state if k.startswith("car_")]
        answers = {}
        totals = {s: 0 for s in slots}
        for qid, q in json["questions"].items():
            if qid == "overall":
                continue
            groups = state["deterministic_evidence"][qid]["correlation_groups"]
            counts = {s: sum(1 for g in groups if g["lean"] == s) for s in slots}
            answers[qid] = self._answer(slots, counts, bool(groups))
            if answers[qid]["choice"] in totals:
                totals[answers[qid]["choice"]] += 1
        answers["overall"] = self._answer(slots, totals, any(totals.values()))
        answers.update(copy.deepcopy(self.answers_override))
        return _Resp(200, {"model": self.response_model, "answers": answers, "usage": {"input_tokens": 1234, "output_tokens": 0}})

    @staticmethod
    def _answer(slots, counts, has_evidence):
        if not has_evidence:
            choice = "insufficient_evidence"
        else:
            best = max(counts.values())
            leaders = [s for s, c in counts.items() if c == best]
            choice = leaders[0] if len(leaders) == 1 and best > 0 else "tie"
        options = slots + ["tie", "insufficient_evidence"]
        probs = {o: 0.04 for o in options}
        probs[choice] = round(1 - 0.04 * (len(options) - 1), 2)
        return {"type": "choice", "choice": choice, "confidence": probs[choice], "probabilities": probs}


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
        overall = payload["overall"]
        name = overall.get("choice_name")
        if name:
            text = f"לפי הנתונים הזמינים כרגע, ל־{name} יש יתרון כולל. ההכרעה מבוססת על הנתונים הזמינים בלבד."
        else:
            text = "אין כרגע מספיק מידע מאומת להכרעה כוללת. ההשוואה מציגה את הנתונים הזמינים בלבד."
        return {"output": {"stated_overall_choice": overall["choice"], "summary_he": text}, "error_code": None, "duration_ms": 1}


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
