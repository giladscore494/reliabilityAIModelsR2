# -*- coding: utf-8 -*-
"""Offline fakes for Comparison V3: ``vehicle-facts/1`` records as TRIPY serves them (government facts + open-data
facts with standard / definition / source / licence), a fake TRIPY HTTP session, a fake row-explanation writer and
the dependency wiring. No network, no paid calls."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional

from comparison_v2_fakes import FakeSummaryWriter, FakeTypeSafeSession

VERSIONS = {"contract": "vehicle-facts/1", "admission": "admission-test-1", "zero_semantics": "zero-test-1",
            "snapshots_sha": "sha-test-1", "matcher": "matcher-test-1"}

_GOV = {"source": "government", "source_level": "government", "identity_level": "exact_market_trim",
        "basis": "variant_identity_key", "licence": "Israeli government open data", "row_ids": ["gov-1"]}
_EEA = {"source": "eea_co2_cars", "source_level": "open_data", "identity_level": "exact_technical_variant",
        "basis": "type_code_match+co2", "licence": "CC-BY-4.0", "row_ids": ["eea-1"], "dataset_year": 2023,
        "attribution": "European Environment Agency (EEA), CO2 emissions from new passenger cars; processed by TRIPY"}
_TC = {"source": "tc_cvs", "source_level": "open_data", "identity_level": "body_powertrain", "basis": "unique",
       "licence": "OGL-Canada", "row_ids": ["tc-1"],
       "attribution": "Contains information licensed under the Open Government Licence – Canada."}
_EPA = {"source": "epa_fueleconomy", "source_level": "open_data", "identity_level": "body_powertrain",
        "basis": "unique", "licence": "US public domain", "row_ids": ["epa-1"]}


# TRIPY #77 (vehicle-facts/1.1): the data.gov.il government datasets, source_level "government_dataset"
_GOV_DATASETS = {
    "gov_new_car_prices": {"resource_id": "39f455bf-6db0-4926-859d-017f34eacbcb", "licence": "Other (Open)",
                           "identity_level": "model_year", "basis": "tozeret_cd+degem_cd+shnat_yitzur",
                           "attribution": "מקור: משרד התחבורה והבטיחות בדרכים, data.gov.il — מחירון רכב חדש "
                                          "(39f455bf-6db0-4926-859d-017f34eacbcb), נכון ל-2026-10-03"},
    "gov_recall_notices": {"resource_id": "2c33523f-87aa-44ec-a736-edbb0a82975e", "licence": "CC BY",
                           "identity_level": "model", "basis": "canonical_make+recall_model_map+production_range",
                           "attribution": "מקור: משרד התחבורה והבטיחות בדרכים, data.gov.il — קריאות שירות (ריקול) "
                                          "(2c33523f-87aa-44ec-a736-edbb0a82975e), נכון ל-2026-10-03. רישיון CC BY"},
    "gov_road_survival": {"resource_id": ["851ecab1-0622-4dbe-a6c7-f950cf82abf9", "4e6b9724-4c1e-43f0-909a-154d4cc4e046",
                                          "ec8cbc34-72e1-4b69-9c48-22821ba0bd6c", "053cea08-09bc-40ec-8f7a-156f0677aff3"],
                          "licence": "Other (Open)", "identity_level": "model_year",
                          "basis": "tozeret_cd+degem_cd+first_road_year",
                          "attribution": "מקור: משרד התחבורה והבטיחות בדרכים, data.gov.il — ביטולים סופיים של כלי רכב "
                                         "+ מאגר כלי רכב פעילים, נכון ל-2026-10-03"},
}


def govds(source: str, **fields):
    """A data.gov.il government-dataset fact as TRIPY #77 serves it."""
    return {"source": source, "source_level": "government_dataset", "dataset_built_at": "2026-10-03",
            **_GOV_DATASETS[source], **fields}


def list_price(value=None, *, range=None, n_prices=None):
    extra = {"value": value} if value is not None else {"range": range, "n_prices": n_prices}
    return govds("gov_new_car_prices", unit="ILS", label_he="מחיר מחירון חדש מקורי", price_type="new_car_list_price",
                 **extra)


def recall(recall_id, year, system, fault, repair, built_from="2022-01", built_to="2023-06"):
    return {"recall_id": recall_id, "recall_year": year, "affected_system": system, "fault_description": fault,
            "repair_method": repair, "production_range": {"from": built_from, "to": built_to}}


def recall_facts(notices, count=None):
    """``recalls`` + ``recall_count`` of a RESOLVED model (an unresolved model gets neither field)."""
    return {"recalls": govds("gov_recall_notices", value=list(notices)),
            "recall_count": govds("gov_recall_notices", value=len(notices) if count is None else count)}


def road_survival(shares, cohort_year=2023, cohort_size=4200):
    return govds("gov_road_survival", value={
        "cohort_size": cohort_size, "cancelled_share_by_age": shares, "final_cancellation_rate": 0.012,
        "definition_he": "שיעור הרכבים מהמחזור שבוטלו סופית עד הגיל הנתון", "cohort_basis": "first_road_year",
        "cohort_year": cohort_year, "reference_month": "2026-09"})


def vkey(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def gov(value, unit=None, **extra):
    return {**_GOV, "value": value, **({"unit": unit} if unit else {}), **extra}


def eea(value, unit=None, **extra):
    return {**_EEA, "value": value, **({"unit": unit} if unit else {}), **extra}


def tc(value, unit=None, **extra):
    return {**_TC, "value": value, **({"unit": unit} if unit else {}), **extra}


def epa(value, unit=None, **extra):
    return {**_EPA, "value": value, **({"unit": unit} if unit else {}), **extra}


ADAS_FOUR = {"reverse_camera": True, "adaptive_cruise_control": True, "blind_spot_monitoring": False,
             "lane_keeping_assist": True}


def record(name: str, *, manufacturer: str, model: str, year: int, trim: str, propulsion: str, fuel_type: str,
           facts: Dict[str, Any], adas: Optional[Dict[str, bool]] = None, sug_tkina: str = "european",
           route_level: str = "exact_technical_variant") -> Dict[str, Any]:
    all_facts = {"propulsion": gov(propulsion), "fuel_type": gov(fuel_type), **facts}
    for flag, value in (adas if adas is not None else ADAS_FOUR).items():
        all_facts[f"adas.{flag}"] = gov(value)
    return {
        "variant_identity_key": vkey(name), "status": "ok",
        "identity": {"manufacturer": manufacturer, "model": model, "model_year": year, "trim": trim,
                     "degem_nm": name.upper()[:8], "sug_tkina": sug_tkina, "propulsion": propulsion, "fuel_type": fuel_type},
        "facts": all_facts,
        "open_data_match": {"route": sug_tkina, "level": route_level},
        "versions": dict(VERSIONS),
    }


def _combustion_gov(hp, cc, airbags, score, level, green, group, seats=5, braked=1500, unbraked=700, co2=None,
                    drivetrain="two_wheel_drive", body="sedan"):
    out = {"horsepower": gov(hp, "hp"), "engine_cc": gov(cc, "cc"), "airbags": gov(airbags), "safety_score": gov(score),
           "safety_equipment_level": gov(level), "green_index": gov(green), "pollution_group": gov(group),
           "seats": gov(seats), "towing_braked_kg": gov(braked, "kg"), "towing_unbraked_kg": gov(unbraked, "kg"),
           "automatic": gov(True), "drivetrain": gov(drivetrain), "body_style": gov(body)}
    if co2 is not None:
        out["co2_wltp"] = gov(co2, "g/km", standard="WLTP")
    return out


OCTAVIA = record("octavia-2023", manufacturer="סקודה", model="OCTAVIA", year=2023, trim="STYLE 1.5 TSI",
                 propulsion="conventional", fuel_type="petrol", facts={
                     **_combustion_gov(150, 1498, 7, 6, 5, 120, 9, co2=139),
                     "curb_weight_kg": eea(1390, "kg", definition="eu_running_order"),
                     "wheelbase_mm": eea(2686, "mm"),
                     "fuel_consumption_combined_l_100km": eea(6.1, "L/100km", standard="WLTP"),
                     "original_new_price_ils": list_price(160000),
                     **recall_facts([recall("R-1001", 2023, "בלמים", "דליפה בצינור בלם", "החלפת צינור בלם")]),
                     "road_survival": road_survival({"3": 0.01, "5": 0.02}),
                 })
GOLF = record("golf-2023", manufacturer="פולקסווגן", model="GOLF", year=2023, trim="LIFE 1.5 ETSI",
              propulsion="conventional", fuel_type="petrol", facts={
                  **_combustion_gov(130, 1498, 8, 5, 4, 125, 10, braked=1400, unbraked=670, co2=141,
                                    body="hatchback"),
                  "curb_weight_kg": eea(1325, "kg", definition="eu_running_order"),
                  "wheelbase_mm": eea(2636, "mm"),
                  "fuel_consumption_combined_l_100km": eea(5.4, "L/100km", standard="WLTP"),
                  "original_new_price_ils": list_price(range=[150000, 175000], n_prices=4),
                  **recall_facts([recall("R-2002", 2023, "כריות אוויר", "תקלה במודול הכרית", "החלפת מודול"),
                                  recall("R-1990", 2022, "חשמל", "תקלת תוכנה ביחידת הבקרה", "עדכון תוכנה")]),
                  "road_survival": road_survival({"3": 0.015, "4": 0.02}),
              })
CIVIC_NO_WHEELBASE = record("civic-2023", manufacturer="הונדה", model="CIVIC", year=2023, trim="SPORT",
                            propulsion="conventional", fuel_type="petrol", facts={
                                **_combustion_gov(182, 1498, 8, 6, 5, 130, 10, co2=150),
                                "curb_weight_kg": eea(1400, "kg", definition="eu_running_order"),
                                "fuel_consumption_combined_l_100km": eea(6.4, "L/100km", standard="WLTP"),
                            })
FOCUS_NEDC = record("focus-2017", manufacturer="פורד", model="FOCUS", year=2017, trim="TREND",
                    propulsion="conventional", fuel_type="petrol", facts={
                        **_combustion_gov(125, 999, 6, 4, 3, 150, 11),
                        "curb_weight_kg": eea(1300, "kg", definition="eu_running_order"),
                        "wheelbase_mm": eea(2648, "mm"),
                        "fuel_consumption_combined_l_100km": eea(5.1, "L/100km", standard="NEDC"),
                        "co2_nedc_g_km": eea(117, "g/km", standard="NEDC"),
                    })
BMW_530E = record("bmw-530e-2022", manufacturer="ב מ וו", model="530E", year=2022, trim="M SPORT",
                  propulsion="plug_in", fuel_type="petrol", facts={
                      **_combustion_gov(292, 1998, 8, 7, 6, 40, 2, braked=2000, unbraked=750, co2=48),
                      "curb_weight_kg": eea(1935, "kg", definition="eu_running_order"),
                      "wheelbase_mm": eea(2975, "mm"),
                      "fuel_consumption_combined_l_100km": eea(2.1, "L/100km", standard="WLTP"),
                      "electric_range_km": eea(57, "km", standard="WLTP"),
                  })
OUTLANDER_PHEV = record("outlander-phev-2022", manufacturer="מיצובישי", model="OUTLANDER PHEV", year=2022,
                        trim="INSTYLE", propulsion="plug_in", fuel_type="petrol", facts={
                            **_combustion_gov(306, 2360, 9, 6, 5, 45, 3, braked=1600, unbraked=750, co2=46,
                                              drivetrain="four_wheel_drive", body="suv"),
                            "curb_weight_kg": eea(2090, "kg", definition="eu_running_order"),
                            "wheelbase_mm": eea(2705, "mm"),
                            "fuel_consumption_combined_l_100km": eea(0.8, "L/100km", standard="WLTP"),
                            "electric_range_km": eea(86, "km", standard="WLTP"),
                        })
ESCAPE_US = record("escape-us-2022", manufacturer="פורד", model="ESCAPE", year=2022, trim="SE",
                   propulsion="conventional", fuel_type="petrol", sug_tkina="american", route_level="body_powertrain",
                   facts={
                       **_combustion_gov(181, 1499, 7, 5, 4, 140, 11, body="suv"),
                       "curb_weight_kg": tc(1590, "kg", definition="na_curb"),
                       "wheelbase_mm": tc(2710, "mm"),
                       "length_mm": tc(4585, "mm"), "width_mm": tc(1882, "mm"), "height_mm": tc(1680, "mm"),
                       "gearbox_type": epa("automatic"), "gear_count": epa(8),
                   })


# ---------------------------------------------------------------------------
# the first live comparison (2026-10-10): the TRIPY records of MILO 22082 / 13323 / 17715 (facts_preview), trimmed
# to the facts the comparison reads. The Alfa has no safety score / level (registry null) and no EEA match above
# body_powertrain (no curb weight, no wheelbase, no consumption).
# ---------------------------------------------------------------------------
_LIVE_ADAS_FLAGS = ("bakarat_mehirut_isa", "bakarat_shyut_adaptivit_ind", "bakarat_stiya_activ_s",
                    "bakarat_stiya_menativ_ind", "blima_otomatit_nesia_leahor", "blimat_hirum_lifnei_holhei_regel_ofanaim",
                    "hayshaney_hagorot_ind", "hayshaney_lahatz_avir_batzmigim_ind", "hitnagshut_cad_shetah_met",
                    "maarechet_ezer_labalam_ind", "matzlemat_reverse_ind", "nitur_merhak_milfanim_ind",
                    "shlita_automatit_beorot_gvohim_ind", "teura_automatit_benesiya_kadima_ind", "zihuy_beshetah_nistar_ind",
                    "zihuy_holchey_regel_ind", "zihuy_matzav_hitkarvut_mesukenet_ind", "zihuy_rechev_do_galgali",
                    "zihuy_tamrurey_tnua_ind")


def _live_adas(*present):
    return {flag: flag in present for flag in _LIVE_ADAS_FLAGS}


def _live_gov(hp, cc, seats, braked, unbraked, co2, group, green, airbags, drivetrain, body, doors, score=None, level=None):
    out = {"horsepower": gov(hp, "hp"), "engine_cc": gov(cc, "cc"), "seats": gov(seats, "count"),
           "doors": gov(doors, "count"), "towing_braked_kg": gov(braked, "kg"), "towing_unbraked_kg": gov(unbraked, "kg"),
           "co2_wltp": gov(co2, "g/km", standard="WLTP"), "pollution_group": gov(group), "green_index": gov(green),
           "airbags": gov(airbags, "count"), "automatic": gov(True), "drivetrain": gov(drivetrain), "body_style": gov(body)}
    if score is not None:
        out["safety_score"] = gov(score)
    if level is not None:
        out["safety_equipment_level"] = gov(level)
    return out


LIVE_BMW_530E = record("live-22082", manufacturer="ב מ וו", model="530E", year=2020, trim="M SPORT",
                       propulsion="plug_in", fuel_type="plug_in_hybrid",
                       adas=_live_adas("bakarat_stiya_menativ_ind", "hayshaney_lahatz_avir_batzmigim_ind",
                                       "maarechet_ezer_labalam_ind", "matzlemat_reverse_ind", "nitur_merhak_milfanim_ind",
                                       "teura_automatit_benesiya_kadima_ind", "zihuy_beshetah_nistar_ind",
                                       "zihuy_holchey_regel_ind", "zihuy_matzav_hitkarvut_mesukenet_ind"),
                       facts={
                           **_live_gov(184, 1998, 5, 1700, 750, 34, 2, 63, 6, "two_wheel_drive", "sedan", 4, score=1, level=1),
                           "curb_weight_kg": eea(1910, "kg", definition="eu_running_order"),
                           "energy_consumption_kwh_100km": eea(16.9, "kWh/100km", standard="WLTP"),
                           "fuel_consumption_combined_l_100km": eea(1.5, "l/100km", standard="WLTP"),
                           "wheelbase_mm": eea(2975, "mm"),
                           "original_new_price_ils": list_price(420000),
                           **recall_facts([]),
                       })
LIVE_AUDI_A7 = record("live-13323", manufacturer="אאודי", model="A7 SPORTBACK", year=2020, trim="",
                      propulsion="plug_in", fuel_type="plug_in_hybrid",
                      adas=_live_adas("bakarat_shyut_adaptivit_ind", "bakarat_stiya_activ_s", "bakarat_stiya_menativ_ind",
                                      "hayshaney_lahatz_avir_batzmigim_ind", "maarechet_ezer_labalam_ind",
                                      "matzlemat_reverse_ind", "nitur_merhak_milfanim_ind",
                                      "shlita_automatit_beorot_gvohim_ind", "teura_automatit_benesiya_kadima_ind",
                                      "zihuy_beshetah_nistar_ind", "zihuy_holchey_regel_ind",
                                      "zihuy_matzav_hitkarvut_mesukenet_ind", "zihuy_rechev_do_galgali"),
                      facts={
                          **_live_gov(299, 1984, 5, 2000, 750, 40, 2, 65, 6, "awd", "hatchback", 5, score=2, level=2),
                          "curb_weight_kg": eea(2140, "kg", definition="eu_running_order"),
                          "energy_consumption_kwh_100km": eea(17.8, "kWh/100km", standard="WLTP"),
                          "wheelbase_mm": eea(2930, "mm"),
                          "original_new_price_ils": list_price(530000),
                      })
LIVE_ALFA_GIULIA = record("live-17715", manufacturer="אלפא רומיאו", model="GIULIA", year=2020, trim="MILANO",
                          propulsion="conventional", fuel_type="petrol", route_level="body_powertrain",
                          adas=_live_adas("bakarat_stiya_menativ_ind", "hayshaney_lahatz_avir_batzmigim_ind",
                                          "maarechet_ezer_labalam_ind", "nitur_merhak_milfanim_ind",
                                          "teura_automatit_benesiya_kadima_ind", "zihuy_holchey_regel_ind",
                                          "zihuy_matzav_hitkarvut_mesukenet_ind"),
                          facts={
                              **_live_gov(200, 1995, 5, 1600, 745, 162, 15, 272, 6, "two_wheel_drive", "sedan", 4),
                              "original_new_price_ils": list_price(249900),
                          })
LIVE_TRIO = (LIVE_BMW_530E, LIVE_AUDI_A7, LIVE_ALFA_GIULIA)


ALL_RECORDS: List[Dict[str, Any]] = [OCTAVIA, GOLF, CIVIC_NO_WHEELBASE, FOCUS_NEDC, BMW_530E, OUTLANDER_PHEV, ESCAPE_US, *LIVE_TRIO]
KEYS = {name: rec["variant_identity_key"] for name, rec in {
    "octavia": OCTAVIA, "golf": GOLF, "civic": CIVIC_NO_WHEELBASE, "focus_nedc": FOCUS_NEDC, "bmw_530e": BMW_530E,
    "outlander_phev": OUTLANDER_PHEV, "escape_us": ESCAPE_US, "live_bmw": LIVE_BMW_530E, "live_audi": LIVE_AUDI_A7,
    "live_alfa": LIVE_ALFA_GIULIA}.items()}


def snapshots_for(*records_and_prices) -> Dict[str, Dict[str, Any]]:
    """{car_1: snapshot, ...} from (record, price) pairs or bare records."""
    from app.services.comparison_v3.snapshot import build_snapshot

    out = {}
    for idx, item in enumerate(records_and_prices):
        rec, price = item if isinstance(item, tuple) else (item, None)
        out[f"car_{idx + 1}"] = build_snapshot(copy.deepcopy(rec), f"car_{idx + 1}", price)
    return out


# ---------------------------------------------------------------------------
# TRIPY HTTP fake
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, status: int, body: Any):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return copy.deepcopy(self._body)


class FakeTripySession:
    """Answers POST /api/facts/v1/vehicles from ``records``; ``statuses`` scripts HTTP statuses per call."""

    def __init__(self, records: Optional[List[Dict[str, Any]]] = None, statuses: Optional[List[int]] = None,
                 raise_exc: Optional[Exception] = None):
        self.records = {r["variant_identity_key"]: r for r in (records if records is not None else ALL_RECORDS)}
        self.statuses = list(statuses or [])
        self.raise_exc = raise_exc
        self.calls: List[Dict[str, Any]] = []

    def _status(self) -> int:
        return self.statuses.pop(0) if self.statuses else 200

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002 - mirrors requests
        self.calls.append({"method": "POST", "url": url, "json": copy.deepcopy(json), "headers": headers, "timeout": timeout})
        if self.raise_exc:
            raise self.raise_exc
        status = self._status()
        if status != 200:
            return _Resp(status, {"detail": "error"})
        vehicles = [self.records.get(k) or {"variant_identity_key": k, "status": "not_found", "http_status": 404}
                    for k in json["variant_identity_keys"]]
        return _Resp(200, {"contract": "vehicle-facts/1", "vehicles": vehicles})

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"method": "GET", "url": url, "params": params, "headers": headers, "timeout": timeout})
        if self.raise_exc:
            raise self.raise_exc
        status = self._status()
        if status != 200:
            return _Resp(status, {"detail": "error"})
        return _Resp(200, {"manufacturers": [{"manufacturer": "סקודה", "variants": 1}]})


# ---------------------------------------------------------------------------
# row explanation writer fake
# ---------------------------------------------------------------------------
class FakeExplanationWriter:
    """Mimics GeminiRowExplanationWriter.write: one call, ``{row_id: text}``."""

    def __init__(self, model_id: Optional[str] = None, output_fn=None, error: Optional[str] = None):
        from app.services.comparison.model_config import DEFAULT_COMPARISON_V2_MODEL_ID

        self.model_id = model_id or DEFAULT_COMPARISON_V2_MODEL_ID
        self.output_fn = output_fn
        self.error = error
        self.calls = 0
        self.payloads: List[Any] = []

    def write(self, rows, profile_headline):
        self.calls += 1
        self.payloads.append(json.loads(json.dumps({"rows": rows, "headline": profile_headline}, ensure_ascii=False)))
        if self.error:
            return {"output": None, "error_code": self.error, "duration_ms": 1}
        if self.output_fn:
            return {"output": self.output_fn(rows), "error_code": None, "duration_ms": 1}
        out = {}
        for row in rows:
            text = f"השורה מציגה את {row['label_he']} לכל רכב. ההבדל משמעותי בעיקר לפי השימוש שהגדרת."
            if row["row_id"] == "road_survival":
                text = "השורה מציגה כמה רכבים מהדגם ירדו מהכביש עד אותו גיל. סיבת הירידה מהכביש אינה ידועה."
            out[row["row_id"]] = text
        return {"output": out, "error_code": None, "duration_ms": 1}


def build_v3_deps(records=None, session=None, writer=None, explanation_writer=None, history=None, facts=None):
    from app.services.comparison_v2.jev_client import TypeSafeJevClient, reset_model_verification_cache
    from app.services.comparison_v3.pipeline import PipelineDeps
    from app.services.comparison_v3.tripy import DemoFactsRepository

    reset_model_verification_cache()
    session = session or FakeTypeSafeSession()
    jev = TypeSafeJevClient(api_key="ts-test-secret-key", model="jev-1.13.0", base_url="https://jev.test", session=session)
    return PipelineDeps(
        facts=facts or DemoFactsRepository(records=records if records is not None else ALL_RECORDS),
        jev_client=jev,
        summary_writer=writer if writer is not None else FakeSummaryWriter(),
        explanation_writer=explanation_writer if explanation_writer is not None else FakeExplanationWriter(),
        history=history,
        provider_meta={"summary_model": "gemini-3.8-flash", "explanation_model": "gemini-3.8-flash",
                       "jev_model": "jev-1.13.0"},
    )
