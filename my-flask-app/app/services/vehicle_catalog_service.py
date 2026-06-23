# -*- coding: utf-8 -*-
"""Central loader for the Israeli vehicle technical catalog.

Provides cached access to ``model_technical_catalog_il.json`` and helpers
for resolving a user selection to a specific technical variant.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "data")
_CATALOG_PATH = os.path.join(_DATA_DIR, "model_technical_catalog_il.json")

_catalog_cache: Optional[Dict[str, Any]] = None
_ui_data_cache: Optional[Dict[str, Any]] = None
_flat_cache: Optional[List[Dict[str, Any]]] = None


def load_vehicle_catalog() -> Dict[str, Any]:
    """Load and cache the full catalog JSON.

    Returns an empty dict with ``models: []`` on any IO/parse error so the
    application can keep running without the catalog.
    """
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache

    try:
        with open(_CATALOG_PATH, encoding="utf-8") as fh:
            _catalog_cache = json.load(fh)
        logger.info(
            "[CATALOG] Loaded %d models from %s",
            len(_catalog_cache.get("models", [])),
            _CATALOG_PATH,
        )
    except FileNotFoundError:
        logger.warning("[CATALOG] File not found: %s — running without catalog", _CATALOG_PATH)
        _catalog_cache = {"models": []}
    except Exception:
        logger.exception("[CATALOG] Failed to parse catalog")
        _catalog_cache = {"models": []}
    return _catalog_cache


def get_vehicle_catalog_ui_data() -> Dict[str, Any]:
    """Return a structure optimised for cascading selects in the frontend.

    Shape::

        {
          "Toyota": {
            "Corolla": {
              "year_start": 1992,
              "year_end": 2026,
              "variants": [
                {
                  "variant_id": "<deterministic hash>",
                  "label": "1.6L 132hp Automatic FWD",
                  "version_or_trim": "Comfort",
                  "body_type": "Sedan",
                  "fuel_type": "petrol",
                  "engine": "1.6L",
                  "horsepower_hp": 132,
                  "transmission": "automatic",
                  "drivetrain": "FWD",
                  "year_start": 2019,
                  "year_end": 2023
                },
                ...
              ]
            }
          }
        }
    """
    global _ui_data_cache
    if _ui_data_cache is not None:
        return _ui_data_cache

    catalog = load_vehicle_catalog()
    ui: Dict[str, Any] = {}

    for entry in catalog.get("models", []):
        make = entry.get("make", "")
        model = entry.get("model", "")
        if not make or not model:
            continue

        if make not in ui:
            ui[make] = {}

        if model not in ui[make]:
            ui[make][model] = {
                "year_start": entry.get("year_start"),
                "year_end": entry.get("year_end"),
                "variants": [],
            }
        else:
            existing = ui[make][model]
            entry_ys = entry.get("year_start")
            entry_ye = entry.get("year_end")
            if entry_ys and (existing["year_start"] is None or entry_ys < existing["year_start"]):
                existing["year_start"] = entry_ys
            if entry_ye and (existing["year_end"] is None or entry_ye > existing["year_end"]):
                existing["year_end"] = entry_ye

        for v in entry.get("technical_variants_il", []):
            variant_id = _compute_variant_id(make, model, v)
            label = _build_variant_label(v)
            ui[make][model]["variants"].append({
                "variant_id": variant_id,
                "label": label,
                "version_or_trim": v.get("version_or_trim"),
                "body_type": v.get("body_type"),
                "fuel_type": v.get("fuel_type"),
                "engine": v.get("engine"),
                "horsepower_hp": v.get("horsepower_hp"),
                "transmission": v.get("transmission"),
                "drivetrain": v.get("drivetrain"),
                "year_start": v.get("year_start"),
                "year_end": v.get("year_end"),
            })

    _ui_data_cache = ui
    return _ui_data_cache


def get_flat_vehicle_catalog() -> List[Dict[str, Any]]:
    """Return a flat list of ``{make, model, display, year_start, year_end}``
    suitable for autocomplete search on the comparison page.
    """
    global _flat_cache
    if _flat_cache is not None:
        return _flat_cache

    ui_data = get_vehicle_catalog_ui_data()
    result: List[Dict[str, Any]] = []
    for make, models in ui_data.items():
        for model_name, info in models.items():
            result.append({
                "make": make,
                "model": model_name,
                "display": f"{make} {model_name}",
                "year_start": info["year_start"],
                "year_end": info["year_end"],
            })
    _flat_cache = result
    return _flat_cache


def resolve_vehicle_variant(selection: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Look up a variant by ``variant_id`` and return the full variant dict.

    Falls back to matching by make/model/year + fuel/transmission when no
    exact ``variant_id`` is supplied.  Returns ``None`` when unresolved.
    """
    variant_id = selection.get("variant_id")
    make = (selection.get("make") or "").strip()
    model = (selection.get("model") or "").strip()

    ui_data = get_vehicle_catalog_ui_data()
    model_info = (ui_data.get(make) or {}).get(model)
    if not model_info:
        return None

    variants = model_info.get("variants", [])

    if variant_id:
        for v in variants:
            if v["variant_id"] == variant_id:
                return dict(v)
        return None

    year = selection.get("year")
    fuel = (selection.get("fuel_type") or selection.get("catalog_fuel_type") or "").strip().lower()
    trans = (selection.get("transmission") or selection.get("catalog_transmission") or "").strip().lower()

    best: Optional[Dict[str, Any]] = None
    best_score = -1

    for v in variants:
        score = 0
        if year is not None:
            vy_start = v.get("year_start") or 0
            vy_end = v.get("year_end") or 9999
            try:
                y = int(year)
            except (ValueError, TypeError):
                y = 0
            if vy_start <= y <= vy_end:
                score += 2
            else:
                continue
        if fuel and v.get("fuel_type", "").lower() == fuel:
            score += 1
        if trans and v.get("transmission", "").lower() == trans:
            score += 1
        if score > best_score:
            best_score = score
            best = v

    return dict(best) if best else None


def build_vehicle_catalog_context(selection: Mapping[str, Any]) -> Dict[str, Any]:
    """Build a prompt-injection-safe catalog context block for a single car.

    Returns a dict with keys:
    - ``match_type``: ``"exact"`` | ``"ambiguous"`` | ``"unmatched"``
    - ``catalog_entry``: the resolved variant dict (or ``None``)
    - ``prompt_block``: Hebrew text block for injection into prompts
    """
    variant = resolve_vehicle_variant(selection)

    if variant is None:
        return {
            "match_type": "unmatched",
            "catalog_entry": None,
            "prompt_block": (
                "LOCAL_VEHICLE_CATALOG_CONTEXT:\n"
                "match_type: unmatched\n"
                "לא נמצאה התאמה במאגר הטכני המקומי. "
                "השתמש בחיפוש אינטרנטי לקביעת כל פרטי הזהות הטכנית."
            ),
        }

    variant_id = selection.get("variant_id")
    if variant_id and variant.get("variant_id") == variant_id:
        match_type = "exact"
    else:
        match_type = "ambiguous"

    identity_lines = [
        f"make: {selection.get('make', '')}",
        f"model: {selection.get('model', '')}",
    ]
    for field, label in [
        ("version_or_trim", "version_or_trim"),
        ("body_type", "body_type"),
        ("fuel_type", "fuel_type"),
        ("engine", "engine"),
        ("horsepower_hp", "horsepower_hp"),
        ("transmission", "transmission"),
        ("drivetrain", "drivetrain"),
        ("year_start", "year_start"),
        ("year_end", "year_end"),
    ]:
        val = variant.get(field)
        if val is not None:
            identity_lines.append(f"{label}: {val}")

    identity_block = "\n".join(identity_lines)

    if match_type == "exact":
        rule = (
            "CATALOG-FIRST RULE (exact match):\n"
            "הנתונים לעיל הם מקור אמת לזהות הטכנית של הרכב. "
            "אסור למודל להשתמש באינטרנט כדי להחליף או לסתור נתוני זהות אלה.\n"
            "MANDATORY WEB GROUNDING:\n"
            "החיפוש האינטרנטי נשאר חובה לכל החלקים האנליטיים: "
            "אמינות, תקלות, עלויות, ריקולים, בטיחות, מחירים, ביקורות."
        )
    else:
        rule = (
            "CATALOG-FIRST RULE (ambiguous match):\n"
            "נמצאה התאמה חלקית במאגר. השתמש בנתוני הזהות כנקודת התחלה, "
            "אך אמת אותם באמצעות חיפוש אינטרנטי. אם יש סתירה - דווח data_conflict=true.\n"
            "MANDATORY WEB GROUNDING:\n"
            "החיפוש האינטרנטי נשאר חובה לכל החלקים האנליטיים."
        )

    prompt_block = (
        f"LOCAL_VEHICLE_CATALOG_CONTEXT:\n"
        f"match_type: {match_type}\n"
        f"{identity_block}\n\n"
        f"{rule}"
    )

    return {
        "match_type": match_type,
        "catalog_entry": variant,
        "prompt_block": prompt_block,
    }


def _compute_variant_id(make: str, model: str, variant: Dict[str, Any]) -> str:
    """Deterministic hash for a variant."""
    key_parts = [
        make,
        model,
        str(variant.get("version_or_trim") or ""),
        str(variant.get("body_type") or ""),
        str(variant.get("fuel_type") or ""),
        str(variant.get("engine") or ""),
        str(variant.get("horsepower_hp") or ""),
        str(variant.get("transmission") or ""),
        str(variant.get("drivetrain") or ""),
        str(variant.get("year_start") or ""),
        str(variant.get("year_end") or ""),
    ]
    raw = "|".join(key_parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _build_variant_label(variant: Dict[str, Any]) -> str:
    """Human-readable label for a variant."""
    parts: List[str] = []
    if variant.get("engine"):
        parts.append(str(variant["engine"]))
    if variant.get("horsepower_hp"):
        parts.append(f"{variant['horsepower_hp']}hp")
    if variant.get("transmission"):
        t = str(variant["transmission"]).capitalize()
        parts.append(t)
    if variant.get("drivetrain"):
        parts.append(str(variant["drivetrain"]))
    if variant.get("version_or_trim"):
        parts.append(f"({variant['version_or_trim']})")
    return " ".join(parts) if parts else "Standard"
