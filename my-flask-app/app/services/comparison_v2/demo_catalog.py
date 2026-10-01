# -*- coding: utf-8 -*-
"""Demo Level 1.5 catalog backed by fixtures copied from MILO Production.

The fixtures live in ``app/data/comparison_v2_demo_catalog.json``. They are
never "corrected" from the web. A future ``MiloCatalogRepository`` implements
the same ``VehicleCatalogRepository`` interface against Supabase.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.comparison_v2.contracts import VehicleCatalogRepository
from app.services.comparison_v2.source_registry import brand_display

_FIXTURE_PATH = Path(__file__).resolve().parents[2] / "data" / "comparison_v2_demo_catalog.json"
_IDENTITY_KEY_RE = re.compile(r"^[0-9a-f]{64}$")

FUEL_LABELS_HE = {
    "petrol": "בנזין",
    "diesel": "דיזל",
    "electric": "חשמלי",
    "lpg": "גפ״מ",
}
PROPULSION_LABELS_HE = {
    "conventional": "מנוע בעירה",
    "hybrid": "היברידי",
    "plug_in_hybrid": "פלאג-אין",
    "battery_electric": "חשמלי",
}
DRIVETRAIN_LABELS = {
    "awd": "AWD",
    "two_wheel_drive": "2WD",
}
BODY_LABELS_HE = {
    "suv": "SUV",
    "sedan": "סדאן",
    "hatchback": "האצ'בק",
    "mpv": "רב-מושבי",
    "coupe": "קופה",
}


def is_valid_identity_key(value: Any) -> bool:
    return isinstance(value, str) and bool(_IDENTITY_KEY_RE.match(value))


@lru_cache(maxsize=1)
def _load_fixtures() -> Dict[str, Any]:
    with _FIXTURE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def picker_entry(record: Dict[str, Any]) -> Dict[str, Any]:
    """Compact, display-ready picker row (no raw MoT column names)."""
    make = brand_display(record.get("manufacturer"))
    model = record.get("display_model") or record.get("model")
    hp = record.get("horsepower")
    fuel = record.get("fuel_type")
    propulsion = record.get("propulsion")
    fuel_label = PROPULSION_LABELS_HE.get(propulsion) if propulsion in ("hybrid", "plug_in_hybrid", "battery_electric") else None
    return {
        "variant_identity_key": record["variant_identity_key"],
        "make": make,
        "make_he": record.get("manufacturer"),
        "model": model,
        "government_model": record.get("model"),
        "year": record.get("model_year"),
        "trim": record.get("trim"),
        "horsepower": hp,
        "drivetrain": DRIVETRAIN_LABELS.get(record.get("drivetrain"), record.get("drivetrain")),
        "fuel_label": fuel_label or FUEL_LABELS_HE.get(fuel, fuel),
        "body_label": BODY_LABELS_HE.get(record.get("body_style"), record.get("body_style")),
        "seats": record.get("seats"),
        "display_name": f"{make} {model}".strip(),
    }


class DemoVehicleCatalogRepository(VehicleCatalogRepository):
    """Read-only in-memory catalog of the demo variants."""

    def __init__(self, records: Optional[List[Dict[str, Any]]] = None):
        source = records if records is not None else _load_fixtures()["variants"]
        self._records: Dict[str, Dict[str, Any]] = {}
        for rec in source:
            key = rec.get("variant_identity_key")
            if is_valid_identity_key(key):
                self._records[key] = rec

    def get_variant(self, variant_identity_key: str) -> Optional[Dict[str, Any]]:
        rec = self._records.get(variant_identity_key or "")
        return copy.deepcopy(rec) if rec else None

    def list_variants(self) -> List[Dict[str, Any]]:
        return [copy.deepcopy(rec) for rec in self._records.values()]

    def picker_entries(self) -> List[Dict[str, Any]]:
        rows = [picker_entry(rec) for rec in self._records.values()]
        return sorted(rows, key=lambda r: (r["make"], r["model"], r["year"] or 0))


def fixture_provenance() -> Dict[str, Any]:
    return dict(_load_fixtures().get("source") or {})
