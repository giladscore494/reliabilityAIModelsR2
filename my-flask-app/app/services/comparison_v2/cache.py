# -*- coding: utf-8 -*-
"""Per-vehicle official Level 2 cache (separate from whole-comparison history).

Key = (variant_identity_key, enrichment_contract_version,
source_registry_version, enrichment_model). Freshness is tracked per field
group (technical / price / warranty) so a stale price never forces the
technical data to be searched again, and an Audi Q3 enriched once is reused
whether it is later compared with a Tucson or a BMW.
"""

from __future__ import annotations

import abc
import copy
import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services.comparison_v2.field_registry import FRESHNESS_TTL_SECONDS


def build_cache_key(variant_identity_key: str, contract_version: str, registry_version: str, model: str) -> str:
    raw = "|".join([variant_identity_key or "", contract_version, registry_version, model or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def stale_groups(observed_at: Dict[str, Any], now: datetime, ttl: Optional[Dict[str, int]] = None) -> List[str]:
    ttl = ttl or FRESHNESS_TTL_SECONDS
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    out = []
    for group, seconds in ttl.items():
        seen = _parse_iso((observed_at or {}).get(group))
        if seen is None or now - seen > timedelta(seconds=seconds):
            out.append(group)
    return out


class VehicleOfficialEnrichmentCache(abc.ABC):
    @abc.abstractmethod
    def get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        ...

    @abc.abstractmethod
    def set(self, cache_key: str, variant_identity_key: str, outcome: Dict[str, Any], model: str) -> None:
        ...


class InProcessEnrichmentCache(VehicleOfficialEnrichmentCache):
    """Thread-safe in-memory cache (tests, offline demo, fallback)."""

    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, cache_key):
        with self._lock:
            value = self._data.get(cache_key)
            return copy.deepcopy(value) if value else None

    def set(self, cache_key, variant_identity_key, outcome, model):
        with self._lock:
            self._data[cache_key] = copy.deepcopy(outcome)


_PROCESS_CACHE = InProcessEnrichmentCache()


def process_cache() -> InProcessEnrichmentCache:
    return _PROCESS_CACHE


class DatabaseEnrichmentCache(VehicleOfficialEnrichmentCache):
    """Persistent cache in ``vehicle_official_enrichment_cache``.

    Must be used from the request thread (Flask app context). Failures are
    swallowed by the caller: the cache is an optimization, never a gate.
    """

    def __init__(self, contract_version: str, registry_version: str):
        self.contract_version = contract_version
        self.registry_version = registry_version

    def get(self, cache_key):
        from app.models import VehicleOfficialEnrichmentCacheRow

        row = VehicleOfficialEnrichmentCacheRow.query.filter_by(cache_key=cache_key).first()
        if not row:
            return None
        payload = row.payload
        if isinstance(payload, str):  # tolerate rows written as JSON text
            try:
                payload = json.loads(payload)
            except ValueError:
                return None
        return payload if isinstance(payload, dict) else None

    def set(self, cache_key, variant_identity_key, outcome, model):
        from app.extensions import db
        from app.models import VehicleOfficialEnrichmentCacheRow

        observed = outcome.get("observed_at") or {}
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        row = VehicleOfficialEnrichmentCacheRow.query.filter_by(cache_key=cache_key).first()
        payload = json.loads(json.dumps(outcome, ensure_ascii=False))
        if row is None:
            row = VehicleOfficialEnrichmentCacheRow(
                cache_key=cache_key,
                variant_identity_key=variant_identity_key,
                enrichment_contract_version=self.contract_version,
                source_registry_version=self.registry_version,
                enrichment_model=(model or "")[:64],
                created_at=now,
            )
            db.session.add(row)
        row.payload = payload
        row.technical_observed_at = _naive(observed.get("technical")) or row.technical_observed_at
        row.price_observed_at = _naive(observed.get("price")) or row.price_observed_at
        row.warranty_observed_at = _naive(observed.get("warranty")) or row.warranty_observed_at
        row.updated_at = now
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise


def _rollback_quietly() -> None:
    try:
        from app.extensions import db

        db.session.rollback()
    except Exception:
        pass


def _naive(value: Any) -> Optional[datetime]:
    dt = _parse_iso(value)
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt else None


class LayeredEnrichmentCache(VehicleOfficialEnrichmentCache):
    """In-process first, then the database."""

    def __init__(self, primary: VehicleOfficialEnrichmentCache, secondary: Optional[VehicleOfficialEnrichmentCache]):
        self.primary = primary
        self.secondary = secondary

    def get(self, cache_key):
        value = self.primary.get(cache_key)
        if value is not None or self.secondary is None:
            return value
        try:
            value = self.secondary.get(cache_key)
        except Exception:
            _rollback_quietly()
            return None
        if value is not None:
            self.primary.set(cache_key, "", value, "")
        return value

    def set(self, cache_key, variant_identity_key, outcome, model):
        self.primary.set(cache_key, variant_identity_key, outcome, model)
        if self.secondary is not None:
            self.secondary.set(cache_key, variant_identity_key, outcome, model)
