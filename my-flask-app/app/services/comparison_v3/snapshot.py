# -*- coding: utf-8 -*-
"""``vehicle-facts/1`` (TRIPY) -> ``CanonicalVehicleSnapshot`` (``canonical-vehicle-snapshot/2``).

* Government facts become Level 1.5 (``source_level`` "1.5"); open-data facts become ``open_data``.
* Every fact keeps ``source``, ``standard``, ``definition``, ``identity_level``, ``attribution`` and ``licence`` as
  TRIPY sent them. A field absent from the response is absent from the snapshot: never a null placeholder.
* TRIPY's open-data offer names are mapped to this repository's metric names (``FIELD_RENAMES``).
* The user's asking price is a fact of its own (``asking_price_ils``, provenance ``user_supplied``).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.comparison_v3.contracts import (
    SNAPSHOT_CONTRACT_VERSION,
    SOURCE_LEVEL_GOVERNMENT,
    SOURCE_LEVEL_OPEN_DATA,
    SOURCE_LEVEL_USER,
)
from app.services.comparison_v3.labels import FUEL_LABELS_HE, PROPULSION_LABELS_HE, brand_display

# TRIPY offer name -> metric name of this repository (D2). Everything else keeps its name.
FIELD_RENAMES = {"fuel_consumption_combined_l_100km": "fuel_consumption_l_100km"}
FACT_KEYS = ("value", "unit", "standard", "definition", "source", "identity_level", "basis", "attribution",
             "licence", "row_ids", "dataset_year", "corroborated_by")
ADAS_PREFIX = "adas."


def powertrain_family(propulsion: Optional[str]) -> str:
    """ev | phev | combustion | unknown from the government propulsion (MILO: conventional, hybrid, plug_in,
    battery_electric)."""
    if propulsion == "battery_electric":
        return "ev"
    if propulsion in ("plug_in", "plug_in_hybrid"):
        return "phev"
    if propulsion in ("hybrid", "conventional"):
        return "combustion"
    return "unknown"


def _fact(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict) or raw.get("value") in (None, "") and not raw.get("range"):
        return None
    fact = {k: raw[k] for k in FACT_KEYS if raw.get(k) not in (None, "", [], {})}
    for extra in ("range", "count"):                    # government history facts (original price range)
        if raw.get(extra) not in (None, "", [], {}):
            fact[extra] = raw[extra]
    level = raw.get("source_level")
    fact["source_level"] = SOURCE_LEVEL_GOVERNMENT if level == "government" or raw.get("source") == "government" \
        else SOURCE_LEVEL_OPEN_DATA
    return fact


def build_snapshot(record: Dict[str, Any], slot: str, asking_price_ils: Optional[int] = None) -> Dict[str, Any]:
    """One canonical snapshot (only the facts the record carries)."""
    identity_raw = record.get("identity") or {}
    facts: Dict[str, Dict[str, Any]] = {}
    equipment: Dict[str, bool] = {}
    for name, raw in (record.get("facts") or {}).items():
        fact = _fact(raw)
        if fact is None:
            continue
        if name.startswith(ADAS_PREFIX):
            equipment[name[len(ADAS_PREFIX):]] = bool(fact["value"])
            continue
        facts[FIELD_RENAMES.get(name, name)] = fact
    if asking_price_ils is not None:
        facts["asking_price_ils"] = {"value": int(asking_price_ils), "unit": "ILS", "source": "user_supplied",
                                     "source_level": SOURCE_LEVEL_USER, "provenance": "user_supplied"}
    manufacturer = identity_raw.get("manufacturer") or ""
    model = identity_raw.get("model") or ""
    make = brand_display(manufacturer)
    model_display = record.get("display_model") or model
    propulsion = (facts.get("propulsion") or {}).get("value") or identity_raw.get("propulsion")
    family = powertrain_family(propulsion)
    identity = {k: v for k, v in {
        "variant_identity_key": record.get("variant_identity_key"),
        "manufacturer": manufacturer or None, "make_display": make or None, "model": model or None,
        "model_display": model_display or None, "model_year": identity_raw.get("model_year"),
        "trim": identity_raw.get("trim"), "official_model_code": identity_raw.get("degem_nm"),
        "sug_tkina": identity_raw.get("sug_tkina") or "unknown",
        "display_name": f"{make} {model_display}".strip() or None,
    }.items() if v not in (None, "")}
    fuel = (facts.get("fuel_type") or {}).get("value")
    return {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "vehicle_id": record.get("variant_identity_key"),
        "slot": slot,
        "identity": identity,
        "facts": facts,
        "equipment": equipment,
        "open_data_match": record.get("open_data_match") or {},
        "derived": {
            "powertrain_family": family,
            "is_plugin": family in ("ev", "phev"),
            "propulsion_label_he": PROPULSION_LABELS_HE.get(propulsion) if propulsion else None,
            "fuel_label_he": FUEL_LABELS_HE.get(fuel) if fuel else None,
        },
    }


def fact_value(snapshot: Dict[str, Any], key: str) -> Any:
    return (snapshot["facts"].get(key) or {}).get("value")
