# -*- coding: utf-8 -*-
"""Level 1.5 (government) canonical vehicle snapshots.

Level 1.5 is the base source of truth. Values are copied as-is from the
catalog record; a missing column stays ``None`` (null never becomes zero).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.comparison_v2.contracts import (
    SNAPSHOT_CONTRACT_VERSION,
    SOURCE_LEVEL_GOVERNMENT,
    SOURCE_TYPE_GOVERNMENT,
    empty_official_enrichment,
)
from app.services.comparison_v2.demo_catalog import (
    BODY_LABELS_HE,
    DRIVETRAIN_LABELS,
    FUEL_LABELS_HE,
    PROPULSION_LABELS_HE,
)
from app.services.comparison_v2.source_registry import brand_display

GOVERNMENT_SOURCE_TITLE_HE = "משרד התחבורה — מאגר הדגמים (WLTP)"

# Identity fields: never overwritten by Level 2.
IDENTITY_FIELDS = (
    "variant_identity_key",
    "manufacturer",
    "model",
    "model_year",
    "trim",
    "official_model_code",
    "vehicle_segment",
)

GOVERNMENT_FACT_FIELDS = (
    "fuel_type",
    "propulsion",
    "drivetrain",
    "body_style",
    "engine_cc",
    "horsepower",
    "automatic",
    "doors",
    "seats",
    "gross_weight_kg",
    "towing_braked_kg",
    "towing_unbraked_kg",
    "co2_wltp",
    "nox_wltp",
    "co_wltp",
    "hc_wltp",
    "co2_city",
    "co2_highway",
    "pollution_group",
    "green_index",
    "safety_score",
    "safety_equipment_level",
    "airbags",
    "abs",
    "esc",
)

# The 19 driver-assistance indicators, in MILO ``EQUIPMENT_INDICATORS`` order.
ADAS_FIELDS = (
    "bakarat_mehirut_isa",
    "bakarat_shyut_adaptivit_ind",
    "bakarat_stiya_activ_s",
    "bakarat_stiya_menativ_ind",
    "blima_otomatit_nesia_leahor",
    "blimat_hirum_lifnei_holhei_regel_ofanaim",
    "hayshaney_hagorot_ind",
    "hayshaney_lahatz_avir_batzmigim_ind",
    "hitnagshut_cad_shetah_met",
    "maarechet_ezer_labalam_ind",
    "matzlemat_reverse_ind",
    "nitur_merhak_milfanim_ind",
    "shlita_automatit_beorot_gvohim_ind",
    "teura_automatit_benesiya_kadima_ind",
    "zihuy_beshetah_nistar_ind",
    "zihuy_holchey_regel_ind",
    "zihuy_matzav_hitkarvut_mesukenet_ind",
    "zihuy_rechev_do_galgali",
    "zihuy_tamrurey_tnua_ind",
)

ADAS_LABELS_HE = {
    "bakarat_mehirut_isa": "בקרת מהירות חכמה (ISA)",
    "bakarat_shyut_adaptivit_ind": "בקרת שיוט אדפטיבית",
    "bakarat_stiya_activ_s": "שמירה אקטיבית על נתיב",
    "bakarat_stiya_menativ_ind": "התרעת סטייה מנתיב",
    "blima_otomatit_nesia_leahor": "בלימה אוטומטית בנסיעה לאחור",
    "blimat_hirum_lifnei_holhei_regel_ofanaim": "בלימת חירום מול הולכי רגל ורוכבי אופניים",
    "hayshaney_hagorot_ind": "חיישני חגורות בטיחות",
    "hayshaney_lahatz_avir_batzmigim_ind": "חיישני לחץ אוויר בצמיגים",
    "hitnagshut_cad_shetah_met": "מניעת התנגשות צידית בשטח מת",
    "maarechet_ezer_labalam_ind": "מערכת עזר לבלימה",
    "matzlemat_reverse_ind": "מצלמת רוורס",
    "nitur_merhak_milfanim_ind": "ניטור מרחק מלפנים",
    "shlita_automatit_beorot_gvohim_ind": "שליטה אוטומטית באורות גבוהים",
    "teura_automatit_benesiya_kadima_ind": "תאורה אוטומטית בנסיעה קדימה",
    "zihuy_beshetah_nistar_ind": "זיהוי רכב בשטח מת",
    "zihuy_holchey_regel_ind": "זיהוי הולכי רגל",
    "zihuy_matzav_hitkarvut_mesukenet_ind": "זיהוי התקרבות מסוכנת",
    "zihuy_rechev_do_galgali": "זיהוי רכב דו-גלגלי",
    "zihuy_tamrurey_tnua_ind": "זיהוי תמרורי תנועה",
}

PLUGIN_PROPULSIONS = frozenset({"battery_electric", "plug_in_hybrid"})
COMBUSTION_PROPULSIONS = frozenset({"conventional", "hybrid", "plug_in_hybrid"})


def _clean(value: Any) -> Any:
    """Keep the government value; only empty strings become None."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def powertrain_family(propulsion: Optional[str]) -> str:
    if propulsion == "battery_electric":
        return "ev"
    if propulsion == "plug_in_hybrid":
        return "phev"
    if propulsion in ("hybrid", "conventional"):
        return "combustion"
    return "unknown"


def build_level15_snapshot(record: Dict[str, Any], slot_key: Optional[str] = None) -> Dict[str, Any]:
    """Map a Level 1.5 catalog record to a CanonicalVehicleSnapshot."""
    identity = {field: _clean(record.get(field)) for field in IDENTITY_FIELDS}
    make_display = brand_display(identity["manufacturer"])
    model_display = record.get("display_model") or identity["model"]
    identity.update(
        {
            "make_display": make_display,
            "model_display": model_display,
            "display_name": f"{make_display} {model_display}".strip(),
        }
    )

    facts = {field: _clean(record.get(field)) for field in GOVERNMENT_FACT_FIELDS}
    for flag in ("automatic", "abs", "esc"):
        facts[flag] = _to_bool(facts[flag])

    raw_equipment = record.get("equipment") if isinstance(record.get("equipment"), dict) else {}
    equipment = {field: _to_bool(raw_equipment.get(field)) for field in ADAS_FIELDS}
    equipment_sources = dict(record.get("equipment_sources") or {})

    present_facts = [k for k, v in facts.items() if v is not None]
    present_equipment = [k for k, v in equipment.items() if v is not None]

    propulsion = facts.get("propulsion")
    snapshot = {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "vehicle_id": identity["variant_identity_key"],
        "slot": slot_key,
        "identity": identity,
        "government": {
            "level": SOURCE_LEVEL_GOVERNMENT,
            "facts": facts,
            "equipment": equipment,
            "equipment_sources": equipment_sources,
            "provenance": {
                "source_level": SOURCE_LEVEL_GOVERNMENT,
                "source_type": SOURCE_TYPE_GOVERNMENT,
                "source_title": GOVERNMENT_SOURCE_TITLE_HE,
                "source_url": None,
                "source_market": "IL",
                "validated": True,
                "variant_scope": "variant",
            },
        },
        "official_enrichment": empty_official_enrichment(),
        "derived": {
            "powertrain_family": powertrain_family(propulsion),
            "is_plugin": propulsion in PLUGIN_PROPULSIONS,
            "has_combustion_engine": propulsion in COMBUSTION_PROPULSIONS,
            "fuel_label_he": FUEL_LABELS_HE.get(facts.get("fuel_type"), facts.get("fuel_type")),
            "propulsion_label_he": PROPULSION_LABELS_HE.get(propulsion, propulsion),
            "drivetrain_label": DRIVETRAIN_LABELS.get(facts.get("drivetrain"), facts.get("drivetrain")),
            "body_label_he": BODY_LABELS_HE.get(facts.get("body_style"), facts.get("body_style")),
            # No power-to-weight: gross_weight_kg is gross permitted mass, and
            # there is no reliable curb weight in the contract.
            "power_to_weight": None,
        },
        "coverage": {
            "government": {
                "present": len(present_facts) + len(present_equipment),
                "total": len(GOVERNMENT_FACT_FIELDS) + len(ADAS_FIELDS),
            },
            "official": {"present": 0, "total": 0},
        },
    }
    return snapshot


def vehicle_profile_for_enrichment(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """The only vehicle data the enrichment model receives (one car)."""
    identity = snapshot["identity"]
    facts = snapshot["government"]["facts"]
    return {
        "manufacturer": identity["manufacturer"],
        "manufacturer_latin": identity["make_display"],
        "model": identity["model"],
        "year": identity["model_year"],
        "trim": identity["trim"],
        "official_model_code": identity["official_model_code"],
        "fuel_type": facts.get("fuel_type"),
        "propulsion": facts.get("propulsion"),
        "drivetrain": facts.get("drivetrain"),
        "body_style": facts.get("body_style"),
        "engine_cc": facts.get("engine_cc"),
        "horsepower": facts.get("horsepower"),
    }
