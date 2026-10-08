# -*- coding: utf-8 -*-
"""TRIPY data layer: the only source of vehicle data for V3.

    TripyClient              ``Authorization: Bearer {TRIPY_FACTS_TOKEN}`` against ``TRIPY_BASE_URL`` (Render
                             secrets); timeout 5 s, one retry on a 5xx; any other failure is ``TripyUnavailable``
    TripyCatalogRepository   the picker cascade: /api/facts/v1/catalog/{manufacturers,models,years,trims}
                             (cached in process for CATALOG_CACHE_TTL_SEC like TRIPY's own picker cache)
    TripyFactsRepository     POST /api/facts/v1/vehicles -> one ``vehicle-facts/1`` record per key + TRIPY versions

Offline mode (``COMPARISON_V2_OFFLINE_MODE``) and the tests use ``DemoFactsRepository`` / ``DemoCatalogRepository``,
which turn the Level 1.5 demo fixtures into the same ``vehicle-facts/1`` shape (government facts only). There is
never a fallback from TRIPY to demo or cached data: TRIPY unavailable is ``facts_unavailable``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.comparison_v2.contracts import VehicleCatalogRepository
from app.services.comparison_v3.contracts import FACTS_CONTRACT, MAX_CARS
from app.services.comparison_v3.labels import brand_display

logger = logging.getLogger("comparison_v3")

TIMEOUT_SEC = 5.0
CATALOG_CACHE_TTL_SEC = 600.0
FACTS_PATH = "/api/facts/v1/vehicles"
CATALOG_PATH = "/api/facts/v1/catalog/"


class TripyUnavailable(Exception):
    """TRIPY could not answer (not configured, network, timeout, 5xx after the retry, 401/429, bad JSON)."""


def tripy_base_url() -> str:
    return (os.environ.get("TRIPY_BASE_URL") or "").strip().rstrip("/")


def tripy_token() -> str:
    return (os.environ.get("TRIPY_FACTS_TOKEN") or "").strip()


class TripyClient:
    """Thin HTTP client; ``session`` is injectable (tests never hit the network)."""

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None, session: Any = None,
                 timeout_sec: float = TIMEOUT_SEC):
        self.base_url = (base_url if base_url is not None else tripy_base_url()).rstrip("/")
        self.token = token if token is not None else tripy_token()
        self.timeout_sec = timeout_sec
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.calls = 0

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json", "Content-Type": "application/json"}

    def request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.configured:
            raise TripyUnavailable("tripy_not_configured")
        url = f"{self.base_url}{path}"
        last = "tripy_error"
        for attempt in range(2):                       # one retry, on a 5xx only
            self.calls += 1
            try:
                if method == "GET":
                    resp = self.session.get(url, params=params, headers=self._headers(), timeout=self.timeout_sec)
                else:
                    resp = self.session.post(url, json=body, headers=self._headers(), timeout=self.timeout_sec)
            except Exception as exc:  # noqa: BLE001 - network / timeout: unavailable, no retry
                raise TripyUnavailable(f"tripy_request_failed:{type(exc).__name__}") from None
            status = int(getattr(resp, "status_code", 500))
            if status >= 500:
                last = f"tripy_http_{status}"
                continue
            if status >= 400:
                raise TripyUnavailable(f"tripy_http_{status}")
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                raise TripyUnavailable("tripy_invalid_json") from None
            if not isinstance(data, dict):
                raise TripyUnavailable("tripy_invalid_json")
            return data
        raise TripyUnavailable(last)


# ---------------------------------------------------------------------------
# facts
# ---------------------------------------------------------------------------
class FactsRepository:
    """``get_records(keys) -> {"records": {key: vehicle-facts/1 record}, "versions": {...}}``."""

    def get_records(self, keys: List[str]) -> Dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError


class TripyFactsRepository(FactsRepository):
    def __init__(self, client: TripyClient):
        self.client = client

    def get_records(self, keys: List[str]) -> Dict[str, Any]:
        keys = [str(k) for k in keys][:MAX_CARS]
        data = self.client.request("POST", FACTS_PATH, body={"variant_identity_keys": keys})
        if data.get("contract") != FACTS_CONTRACT or not isinstance(data.get("vehicles"), list):
            raise TripyUnavailable("tripy_contract_mismatch")
        records = {str(r.get("variant_identity_key")): r for r in data["vehicles"] if isinstance(r, dict)}
        versions = next((r.get("versions") for r in data["vehicles"] if isinstance(r, dict) and r.get("status") == "ok"),
                        None) or {"contract": FACTS_CONTRACT}
        return {"records": records, "versions": versions}


# ---------------------------------------------------------------------------
# catalog (picker)
# ---------------------------------------------------------------------------
class _TtlCache:
    def __init__(self, ttl_sec: float = CATALOG_CACHE_TTL_SEC, clock=time.monotonic):
        self.ttl_sec, self.clock = ttl_sec, clock
        self._data: Dict[Tuple, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Tuple, compute):
        now = self.clock()
        with self._lock:
            hit = self._data.get(key)
            if hit and now - hit[0] < self.ttl_sec:
                return hit[1]
        value = compute()
        with self._lock:
            self._data[key] = (now, value)
        return value


class PickerCatalog(VehicleCatalogRepository):
    """The cascading picker (manufacturer -> model -> year -> trim item with its variant_identity_key)."""

    def manufacturers(self) -> List[Dict[str, Any]]:  # pragma: no cover - interface
        raise NotImplementedError

    def models(self, manufacturer: str) -> List[Dict[str, Any]]:  # pragma: no cover - interface
        raise NotImplementedError

    def years(self, manufacturer: str, model: str) -> List[Dict[str, Any]]:  # pragma: no cover - interface
        raise NotImplementedError

    def trims(self, manufacturer: str, model: str, year: int) -> List[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def list_variants(self) -> List[Dict[str, Any]]:
        return []                                       # the catalogue is browsed, never listed whole


def _with_display(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for item in items:
        item = dict(item)
        if item.get("manufacturer"):
            item["display"] = brand_display(item["manufacturer"])
        out.append(item)
    return out


class TripyCatalogRepository(PickerCatalog):
    def __init__(self, client: TripyClient, cache: Optional[_TtlCache] = None, facts: Optional[FactsRepository] = None):
        self.client = client
        self.cache = cache or _TtlCache()
        self.facts = facts or TripyFactsRepository(client)

    def _list(self, kind: str, key: str, **params) -> List[Dict[str, Any]]:
        def compute():
            data = self.client.request("GET", CATALOG_PATH + kind, params=params or None)
            items = data.get(key)
            if not isinstance(items, list):
                raise TripyUnavailable("tripy_contract_mismatch")
            return items
        return self.cache.get((kind, tuple(sorted(params.items()))), compute)

    def manufacturers(self) -> List[Dict[str, Any]]:
        return _with_display(self._list("manufacturers", "manufacturers"))

    def models(self, manufacturer: str) -> List[Dict[str, Any]]:
        return self._list("models", "models", manufacturer=manufacturer)

    def years(self, manufacturer: str, model: str) -> List[Dict[str, Any]]:
        return self._list("years", "years", manufacturer=manufacturer, model=model)

    def trims(self, manufacturer: str, model: str, year: int) -> List[Dict[str, Any]]:
        return self._list("trims", "trims", manufacturer=manufacturer, model=model, year=int(year))

    def get_variant(self, variant_identity_key: str) -> Optional[Dict[str, Any]]:
        record = self.facts.get_records([variant_identity_key])["records"].get(variant_identity_key)
        return record if record and record.get("status") == "ok" else None


# ---------------------------------------------------------------------------
# offline (demo fixtures) — same shapes, government facts only
# ---------------------------------------------------------------------------
_DEMO_GOV_FIELDS = {
    "fuel_type": None, "propulsion": None, "drivetrain": None, "body_style": None, "engine_cc": "cc",
    "horsepower": "hp", "automatic": None, "doors": "count", "seats": "count", "gross_weight_kg": "kg",
    "towing_braked_kg": "kg", "towing_unbraked_kg": "kg", "co2_wltp": "g/km", "nox_wltp": "mg/km",
    "co_wltp": "mg/km", "hc_wltp": "mg/km", "co2_city": "g/km", "co2_highway": "g/km", "pollution_group": None,
    "green_index": None, "safety_score": None, "safety_equipment_level": None, "airbags": "count", "abs": None,
    "esc": None,
}
_DEMO_ZERO_UNKNOWN = {"towing_braked_kg", "towing_unbraked_kg", "airbags", "co2_wltp", "nox_wltp", "co_wltp", "hc_wltp",
                      "co2_city", "co2_highway"}
_DEMO_STANDARD = {"co2_wltp": "WLTP", "nox_wltp": "WLTP", "co_wltp": "WLTP", "hc_wltp": "WLTP"}
_BOOL_FIELDS = {"automatic", "abs", "esc"}


def demo_facts_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """A Level 1.5 demo fixture as a ``vehicle-facts/1`` record (the zero semantics TRIPY applies, applied here)."""
    base = {"source": "government", "source_level": "government", "identity_level": "exact_market_trim",
            "basis": "variant_identity_key", "licence": "Israeli government open data"}
    facts: Dict[str, Any] = {}
    for name, unit in _DEMO_GOV_FIELDS.items():
        value = rec.get(name)
        if value is None or value == "":
            continue
        if name in _DEMO_ZERO_UNKNOWN and not isinstance(value, bool) and value == 0:
            continue
        if name in _BOOL_FIELDS:
            value = bool(value)
        fact = {**base, "value": value}
        if unit:
            fact["unit"] = unit
        if name in _DEMO_STANDARD:
            fact["standard"] = _DEMO_STANDARD[name]
        facts[name] = fact
    for flag, value in (rec.get("equipment") or {}).items():
        if value is not None:
            facts[f"adas.{flag}"] = {**base, "value": bool(value)}
    identity = {k: v for k, v in {
        "manufacturer": rec.get("manufacturer"), "model": rec.get("model"), "model_year": rec.get("model_year"),
        "trim": rec.get("trim"), "degem_nm": rec.get("official_model_code"),
        "sug_tkina": rec.get("sug_tkina") or "unknown", "body_style": rec.get("body_style"),
        "fuel_type": rec.get("fuel_type"), "propulsion": rec.get("propulsion"), "drivetrain": rec.get("drivetrain"),
        "engine_cc": rec.get("engine_cc"), "horsepower": rec.get("horsepower"),
        "automatic": bool(rec["automatic"]) if rec.get("automatic") is not None else None,
    }.items() if v not in (None, "")}
    return {"variant_identity_key": rec["variant_identity_key"], "status": "ok", "identity": identity, "facts": facts,
            "open_data_match": {}, "versions": DEMO_VERSIONS, "display_model": rec.get("display_model")}


DEMO_VERSIONS = {"contract": FACTS_CONTRACT, "admission": "offline-demo", "snapshots_sha": "offline-demo",
                 "matcher": "offline-demo"}


def _demo_records() -> List[Dict[str, Any]]:
    from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository

    return DemoVehicleCatalogRepository().list_variants()


class DemoFactsRepository(FactsRepository):
    """Offline mode and tests: the demo fixtures as ``vehicle-facts/1``. ``overrides`` replaces records per key."""

    def __init__(self, records: Optional[Iterable[Dict[str, Any]]] = None,
                 overrides: Optional[Dict[str, Dict[str, Any]]] = None):
        base = list(records) if records is not None else [demo_facts_record(r) for r in _demo_records()]
        self.records = {r["variant_identity_key"]: r for r in base}
        self.records.update(overrides or {})
        self.calls = 0

    def get_records(self, keys: List[str]) -> Dict[str, Any]:
        import copy

        self.calls += 1
        out = {}
        for key in keys:
            rec = self.records.get(key)
            out[key] = copy.deepcopy(rec) if rec else {"variant_identity_key": key, "status": "not_found",
                                                         "http_status": 404}
        versions = next((r.get("versions") for r in out.values() if r.get("status") == "ok"), None) or DEMO_VERSIONS
        return {"records": out, "versions": versions}


class DemoCatalogRepository(PickerCatalog):
    """The picker cascade over the demo fixtures."""

    def __init__(self, facts: Optional[DemoFactsRepository] = None):
        self.facts = facts or DemoFactsRepository()

    def _ok(self) -> List[Dict[str, Any]]:
        return [r for r in self.facts.records.values() if r.get("status") == "ok"]

    def manufacturers(self) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = {}
        for r in self._ok():
            counts[r["identity"]["manufacturer"]] = counts.get(r["identity"]["manufacturer"], 0) + 1
        return _with_display([{"manufacturer": m, "variants": n} for m, n in sorted(counts.items())])

    def models(self, manufacturer: str) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = {}
        for r in self._ok():
            if r["identity"]["manufacturer"] == manufacturer:
                counts[r["identity"]["model"]] = counts.get(r["identity"]["model"], 0) + 1
        return [{"model": m, "variants": n} for m, n in sorted(counts.items())]

    def years(self, manufacturer: str, model: str) -> List[Dict[str, Any]]:
        counts: Dict[int, int] = {}
        for r in self._ok():
            i = r["identity"]
            if i["manufacturer"] == manufacturer and i["model"] == model:
                counts[i["model_year"]] = counts.get(i["model_year"], 0) + 1
        return [{"year": y, "variants": n} for y, n in sorted(counts.items(), reverse=True)]

    def trims(self, manufacturer: str, model: str, year: int) -> List[Dict[str, Any]]:
        out = []
        for r in self._ok():
            i = r["identity"]
            if i["manufacturer"] == manufacturer and i["model"] == model and int(i["model_year"]) == int(year):
                parts = [i.get("trim"), f"{i['horsepower']} hp" if i.get("horsepower") else None,
                         f"{i['engine_cc']} cc" if i.get("engine_cc") else None, i.get("propulsion"), i.get("degem_nm")]
                out.append({"variant_identity_key": r["variant_identity_key"], "trim": i.get("trim"),
                            "label": " · ".join(str(p) for p in parts if p), "horsepower": i.get("horsepower"),
                            "propulsion": i.get("propulsion")})
        return sorted(out, key=lambda t: t["label"])

    def get_variant(self, variant_identity_key: str) -> Optional[Dict[str, Any]]:
        rec = self.facts.records.get(variant_identity_key)
        return rec if rec and rec.get("status") == "ok" else None
