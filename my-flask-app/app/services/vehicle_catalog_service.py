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
from typing import Any, Dict, List, Mapping, Optional, Tuple

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


def normalize_fuel_label(value: Any) -> str:
    """Return a Hebrew-friendly label for a catalog fuel value."""
    text = str(value or "").strip()
    key = text.lower().replace("-", "_").replace(" ", "_")
    return {
        "petrol": "בנזין",
        "gasoline": "בנזין",
        "diesel": "דיזל",
        "hybrid": "היברידי",
        "mild_hybrid": "מיקרו-היברידי / mild hybrid",
        "plug_in_hybrid": "פלאג-אין",
        "plugin_hybrid": "פלאג-אין",
        "phev": "פלאג-אין",
        "electric": "חשמלי",
        "bev": "חשמלי",
    }.get(key, text)


def normalize_transmission_label(value: Any) -> str:
    """Return a Hebrew-friendly label for a catalog transmission value."""
    text = str(value or "").strip()
    lowered = text.lower().replace("_", "-")
    if not lowered:
        return ""
    speed_match = __import__("re").search(r"(\d+)\s*-?speed", lowered)
    suffix = f" {speed_match.group(1)} הילוכים" if speed_match else ""
    if "single-speed" in lowered or "single speed" in lowered or lowered == "single_speed":
        return "הילוך יחיד"
    if "dual-clutch" in lowered or "dual clutch" in lowered or "dct" in lowered:
        return f"רובוטית כפולת מצמדים{suffix}"
    if "cvt" in lowered or "continuously variable" in lowered:
        return "רציפה"
    if "manual" in lowered:
        return f"ידנית{suffix}"
    if "automatic" in lowered or lowered == "auto":
        return f"אוטומטית{suffix}"
    return text


def _normalize_match_value(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _indexed_sources(entry: Mapping[str, Any]) -> Dict[int, Dict[str, Any]]:
    return {int(src.get("source_index")): src for src in entry.get("sources", []) if isinstance(src, dict) and src.get("source_index") is not None}


def _source_summary(entry: Mapping[str, Any], variant: Mapping[str, Any]) -> List[Dict[str, Any]]:
    sources_by_index = _indexed_sources(entry)
    indexes = variant.get("source_indexes") or []
    if not indexes:
        indexes = list(sources_by_index.keys())[:5]
    summary = []
    for idx in indexes[:6]:
        src = sources_by_index.get(int(idx)) if isinstance(idx, int) or str(idx).isdigit() else None
        if src:
            summary.append({"title": src.get("title"), "url": src.get("url"), "source_name": src.get("source_name")})
    return summary


def _variant_with_entry(entry: Mapping[str, Any], variant: Mapping[str, Any], variant_id: str) -> Dict[str, Any]:
    merged = dict(variant)
    merged.update({
        "variant_id": variant_id,
        "make": entry.get("make"),
        "model": entry.get("model"),
        "canonical_model": entry.get("canonical_model"),
        "profile_confidence": entry.get("profile_confidence"),
        "model_sources": entry.get("sources", []),
        "model_notes": entry.get("notes", []),
    })
    merged["source_summary"] = _source_summary(entry, variant)
    return merged


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
                "fuel_label": normalize_fuel_label(v.get("fuel_type")),
                "transmission_label": normalize_transmission_label(v.get("transmission")),
                "version_or_trim": v.get("version_or_trim"),
                "body_type": v.get("body_type"),
                "fuel_type": v.get("fuel_type"),
                "engine": v.get("engine"),
                "engine_displacement_l": v.get("engine_displacement_l"),
                "horsepower_hp": v.get("horsepower_hp"),
                "transmission": v.get("transmission"),
                "drivetrain": v.get("drivetrain"),
                "year_start": v.get("year_start"),
                "year_end": v.get("year_end"),
                "support_level": v.get("support_level"),
                "missing_grounded_fields": v.get("missing_grounded_fields", []),
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
    """Resolve a user selection to a deterministic catalog variant.

    Exact ``variant_id`` matches win. Fallback requires make/model plus a
    compatible year and at least one explicit technical discriminator
    (fuel/transmission/engine); ambiguous or weak matches return ``None``.
    """
    variant_id = str(selection.get("variant_id") or "").strip()
    make = str(selection.get("make") or "").strip()
    model = str(selection.get("model") or "").strip()
    catalog = load_vehicle_catalog()

    candidates: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []
    for entry in catalog.get("models", []):
        if make and str(entry.get("make") or "").strip() != make:
            continue
        if model and str(entry.get("model") or "").strip() != model:
            continue
        for variant in entry.get("technical_variants_il", []):
            vid = _compute_variant_id(entry.get("make", ""), entry.get("model", ""), variant)
            if variant_id and vid == variant_id:
                return _variant_with_entry(entry, variant, vid)
            candidates.append((entry, variant, vid))

    if variant_id:
        return None

    year_raw = selection.get("year")
    try:
        selected_year = int(year_raw) if year_raw not in (None, "") else None
    except (TypeError, ValueError):
        selected_year = None
    fuel = _normalize_match_value(selection.get("catalog_fuel_type") or selection.get("fuel_type") or selection.get("engine_type"))
    trans = _normalize_match_value(selection.get("catalog_transmission") or selection.get("transmission") or selection.get("gearbox"))
    engine = _normalize_match_value(selection.get("catalog_engine") or selection.get("engine"))

    strong: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []
    for entry, variant, vid in candidates:
        if selected_year is not None:
            ys = variant.get("year_start") or entry.get("year_start") or 0
            ye = variant.get("year_end") or entry.get("year_end") or 9999
            if not (ys <= selected_year <= ye):
                continue
        score = 0
        if fuel and fuel in {_normalize_match_value(variant.get("fuel_type")), _normalize_match_value(normalize_fuel_label(variant.get("fuel_type")))}:
            score += 1
        if trans and trans in {_normalize_match_value(variant.get("transmission")), _normalize_match_value(normalize_transmission_label(variant.get("transmission")))}:
            score += 1
        if engine and engine == _normalize_match_value(variant.get("engine")):
            score += 1
        if score > 0:
            strong.append((entry, variant, vid))

    if len(strong) == 1:
        entry, variant, vid = strong[0]
        return _variant_with_entry(entry, variant, vid)
    return None


def build_vehicle_identity_snapshot(selection: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a compact catalog-first identity snapshot for prompts/UI adapters."""
    variant = resolve_vehicle_variant(selection)
    if variant is None:
        return {
            "match_type": "unmatched",
            "make": selection.get("make"),
            "model": selection.get("model"),
            "canonical_model": None,
            "selected_year": selection.get("year"),
            "version_or_trim": selection.get("version_or_trim"),
            "body_type": selection.get("body_type"),
            "fuel_type": selection.get("catalog_fuel_type") or selection.get("fuel_type"),
            "engine": selection.get("catalog_engine") or selection.get("engine"),
            "engine_displacement_l": None,
            "horsepower_hp": selection.get("catalog_horsepower_hp"),
            "transmission": selection.get("catalog_transmission") or selection.get("transmission") or selection.get("gearbox"),
            "drivetrain": selection.get("catalog_drivetrain"),
            "year_start": None,
            "year_end": None,
            "support_level": None,
            "profile_confidence": None,
            "source_summary": [],
            "missing_grounded_fields": [],
            "notes": ["לא נמצאה התאמה מדויקת במאגר הטכני המקומי."],
        }
    exact = bool(selection.get("variant_id") and selection.get("variant_id") == variant.get("variant_id"))
    return {
        "match_type": "exact" if exact else "ambiguous",
        "make": variant.get("make") or selection.get("make"),
        "model": variant.get("model") or selection.get("model"),
        "canonical_model": variant.get("canonical_model"),
        "selected_year": selection.get("year"),
        "version_or_trim": variant.get("version_or_trim"),
        "body_type": variant.get("body_type"),
        "fuel_type": variant.get("fuel_type"),
        "fuel_label": normalize_fuel_label(variant.get("fuel_type")),
        "engine": variant.get("engine"),
        "engine_displacement_l": variant.get("engine_displacement_l"),
        "horsepower_hp": variant.get("horsepower_hp"),
        "transmission": variant.get("transmission"),
        "transmission_label": normalize_transmission_label(variant.get("transmission")),
        "drivetrain": variant.get("drivetrain"),
        "year_start": variant.get("year_start"),
        "year_end": variant.get("year_end"),
        "support_level": variant.get("support_level"),
        "profile_confidence": variant.get("profile_confidence"),
        "source_summary": variant.get("source_summary", []),
        "missing_grounded_fields": variant.get("missing_grounded_fields", []),
        "notes": variant.get("model_notes", [])[:3],
    }


def build_vehicle_catalog_context(selection: Mapping[str, Any]) -> Dict[str, Any]:
    """Build a prompt-injection-safe catalog context block for a single car."""
    snapshot = build_vehicle_identity_snapshot(selection)
    match_type = snapshot.get("match_type") or "unmatched"
    identity_json = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    if match_type == "exact":
        rule = "Exact catalog match: catalog controls technical identity; web search is only for analytical claims. Report identity conflicts instead of changing identity."
    elif match_type == "ambiguous":
        rule = "Ambiguous catalog match: use catalog as candidate identity, verify with web, and report uncertainty/conflicts."
    else:
        rule = "Unmatched: web may resolve identity, but label it web-resolved/uncertain."
    prompt_block = (
        "LOCAL_VEHICLE_CATALOG_CONTEXT:\n"
        f"{identity_json}\n"
        f"CATALOG_FIRST_RULE: {rule} Mandatory web grounding remains required for reliability, faults, recalls, prices, trims, license fee, safety, warranty, supply, reviews, and ownership costs."
    )
    return {
        "match_type": match_type,
        "catalog_entry": snapshot if match_type != "unmatched" else None,
        "identity_snapshot": snapshot,
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
    """Hebrew-friendly compact label for a variant."""
    parts: List[str] = []
    fuel = normalize_fuel_label(variant.get("fuel_type"))
    if fuel:
        parts.append(fuel)
    if variant.get("engine"):
        parts.append(str(variant["engine"]))
    if variant.get("horsepower_hp"):
        parts.append(f"{variant['horsepower_hp']} כ״ס")
    trans = normalize_transmission_label(variant.get("transmission"))
    if trans:
        parts.append(trans)
    if variant.get("drivetrain"):
        parts.append(str(variant["drivetrain"]))
    if variant.get("version_or_trim"):
        parts.append(str(variant["version_or_trim"]))
    return " · ".join(parts) if parts else "גרסה סטנדרטית"
