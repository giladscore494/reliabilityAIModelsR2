# -*- coding: utf-8 -*-
"""Contracts and repository boundaries for the comparison V2 engine.

Everything after vehicle resolution works against ``CanonicalVehicleSnapshot``
dictionaries, never against raw Ministry-of-Transport column names. The two
abstract repositories below are the mandatory seams for the future MILO
integration: swapping ``DemoVehicleCatalogRepository`` for a
``MiloCatalogRepository`` (and the live enrichment repository for one that
reads canonical MILO Level 2 first) must not require touching the
deterministic engine, the JEV client or the UI.
"""

from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional

# V2/2: JEV answers many narrow Score questions; code composes the decision.
ENGINE_VERSION = "comparison-v2/2"
# Stored V2/1 rows (broad JEV Choice decisions) stay readable through their
# own result contract; new comparisons never use it.
ENGINE_VERSION_V21 = "comparison-v2/1"
READABLE_ENGINE_VERSIONS = (ENGINE_VERSION_V21, ENGINE_VERSION)
# /2: per-car work split into technical / commercial tasks, cross-check
# fields no longer requested, ``group_freshness`` (meaningful-observation
# freshness) in the cached payload. Part of every cache key: /1 rows — which
# could hold empty results marked fresh — are never read again.
# /3: claim year metadata split into ``source_publication_year`` (provenance
# only) and ``vehicle_model_year`` (explicitly stated model year; the only
# year that may be matched); identity evidence gains body / generation /
# seating; ``value_qualifier``; research-required retry; "empty" only after
# an official source was actually retrieved.
ENRICHMENT_CONTRACT_VERSION = "official-enrichment/3"
SNAPSHOT_CONTRACT_VERSION = "canonical-vehicle-snapshot/1"

SLOT_KEYS = ("car_1", "car_2", "car_3")
MIN_CARS = 2
MAX_CARS = 3

# Decision vocabulary shared by JEV, the summary validator and the UI.
CHOICE_TIE = "tie"
CHOICE_INSUFFICIENT = "insufficient_evidence"
DECISION_UNAVAILABLE = "decision_unavailable"
NOT_APPLICABLE = "not_applicable"

# Provenance vocabulary.
SOURCE_LEVEL_GOVERNMENT = "1.5"
SOURCE_LEVEL_OFFICIAL = "2"
SOURCE_TYPE_GOVERNMENT = "government"
SOURCE_TYPE_IMPORTER = "official_importer"
SOURCE_TYPE_MANUFACTURER = "manufacturer"

VARIANT_SCOPE_VARIANT = "variant"
VARIANT_SCOPE_MODEL_GENERIC = "model_generic"

# Progress stages streamed to the UI (order matters).
PROGRESS_STAGES = (
    "resolving_vehicles",
    "loading_government_data",
    "enriching_car_1",
    "enriching_car_2",
    "enriching_car_3",
    "validating_sources",
    "comparing_facts",
    "evaluating_decision",
    "writing_summary",
    "complete",
)

PROGRESS_LABELS_HE = {
    "resolving_vehicles": "מזהה גרסאות",
    "loading_government_data": "טוען נתונים רשמיים",
    "enriching_car_1": "משלים מידע מאתרי היצרן — רכב 1",
    "enriching_car_2": "משלים מידע מאתרי היצרן — רכב 2",
    "enriching_car_3": "משלים מידע מאתרי היצרן — רכב 3",
    "validating_sources": "מאמת את המקורות",
    "comparing_facts": "משווה את הנתונים",
    "evaluating_decision": "שוקל את ההבדלים לפי הצרכים שלך",
    "writing_summary": "מנסח את הסיכום",
    "complete": "הושלם",
}


def choices_for(slot_keys: List[str]) -> List[str]:
    """Structured choice vocabulary for N cars (car slots + tie + insufficient)."""
    return list(slot_keys) + [CHOICE_TIE, CHOICE_INSUFFICIENT]


class VehicleCatalogRepository(abc.ABC):
    """Resolves exact variants by their stable internal identity.

    ``variant_identity_key`` is the only stable identifier. Implementations must
    never key anything on ``upstream_record_id``.
    """

    @abc.abstractmethod
    def get_variant(self, variant_identity_key: str) -> Optional[Dict[str, Any]]:
        """Return the Level 1.5 record for this variant, or None."""

    @abc.abstractmethod
    def list_variants(self) -> List[Dict[str, Any]]:
        """Return the selectable variants (for the compare page picker)."""


class OfficialEnrichmentRepository(abc.ABC):
    """Returns validated official Level 2 data for one canonical snapshot.

    Today: cache first, then one live grounded Gemini extraction per car.
    Future: read canonical MILO Level 2 first and live-search only when a
    field group is missing/stale and policy allows.
    """

    @abc.abstractmethod
    def get_or_enrich(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Return an enrichment outcome dict (see ``official_enrichment``)."""


def empty_official_enrichment() -> Dict[str, Any]:
    return {
        "level": SOURCE_LEVEL_OFFICIAL,
        "status": "not_requested",
        "facts": {},
        "claims": [],
        "rejected_claims": [],
        "conflicts": [],
        "government_conflicts": [],
        "model_generic_claims": [],
        "extra_official_equipment": [],
        "missing": [],
        "sources": [],
        "ignored_grounding_hosts": [],
        "observed_at": {},
        "model": None,
    }
