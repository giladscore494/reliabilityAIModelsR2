# -*- coding: utf-8 -*-
"""TypeSafe JEV client: ONE structured-judgment call per comparison.

Flow: validated canonical vehicle data -> deterministic evidence -> one
``POST /v1/systemone`` call carrying every applicable category (plus
``overall``) as ``choice`` questions -> structured decisions.

JEV never discovers facts and never validates sources. Its state contains
only validated data: sanitized canonical snapshots, deterministic atomic
comparisons, coverage, missing/conflicted field names and the buyer profile.
No HTML, no web search results, no Gemini prose, no source URLs, no rejected
or model-generic claims.

The model id is taken from ``JEV_MODEL`` and verified against
``GET /v1/models`` before use; an unverified id is never used.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.services.comparison_v2.contracts import (
    CHOICE_INSUFFICIENT,
    CHOICE_TIE,
    DECISION_UNAVAILABLE,
    choices_for,
)
from app.services.comparison_v2.deterministic_engine import CATEGORY_LABELS_HE

logger = logging.getLogger("comparison_v2")

DEFAULT_TYPESAFE_BASE_URL = "https://api.typesafe.ai"
MODELS_CACHE_TTL_SEC = 6 * 3600

CATEGORY_GUIDANCE_EN = {
    "safety": (
        "Level 1.5 government safety data is the base. safety_score and safety_equipment_level share the "
        "correlation group gov_safety_rating and the 19 driver-assistance systems are one group (adas_equipment): "
        "count each correlation group once, not each metric."
    ),
    "performance": (
        "Power, torque, 0-100 km/h and top speed. There is no power-to-weight ratio: gross_weight_kg is gross "
        "permitted mass, not curb weight."
    ),
    "efficiency": (
        "Fuel consumption (L/100km) and electric energy consumption (kWh/100km) are different units and are never "
        "compared with each other. Only results with status 'compared' are like-for-like."
    ),
    "electric_and_charging": (
        "Battery, electric range and charging. Electric range is comparable only under the same measurement "
        "standard; results marked not_comparable must not decide the category."
    ),
    "practicality": (
        "Seats, doors, body style and dimensions are contextual: a larger vehicle is not automatically better. "
        "Cargo volume can favour practical use. Use buyer_profile (family size, cargo needs, main use) to judge fit."
    ),
    "towing_and_utility": "Government towing capacities (braked/unbraked). gross_weight_kg is descriptive only.",
    "environment": (
        "Government emission data. co2_wltp and co2_city/co2_highway are different measurements and are only "
        "compared within the same measurement. A missing emission value is missing, not zero."
    ),
    "official_price_and_warranty": (
        "Only Israeli official prices and warranties. Warranty terms are not evidence of reliability."
    ),
}

OVERALL_WITH_PROFILE = (
    "Based only on the validated evidence available in this comparison and the user's stated preferences "
    "(state.buyer_profile), which vehicle currently has the stronger evidence-supported fit?"
)
OVERALL_NO_PROFILE = (
    "Based only on the available validated evidence, which vehicle has the stronger overall evidence-supported "
    "package, while treating missing data neutrally?"
)

COMMON_RULES_EN = (
    "Use only the data in state. Missing data is neutral: never count a missing value against a car, and never "
    "treat higher data coverage as an advantage. Conflicted fields were excluded and must not be inferred. "
    "Use buyer_profile only to weigh what matters; it never changes facts. Do not use outside knowledge about "
    "these vehicles. Choose insufficient_evidence when the compared evidence does not support a decision, and tie "
    "when differences are balanced or not meaningful."
)

_OFFICIAL_FACT_KEYS = ("value", "unit", "source_level", "source_type", "source_market", "measurement_standard", "currency")
_ATOMIC_KEYS = ("metric", "kind", "direction", "unit", "correlation_group", "values", "status", "leader", "margin", "reason", "missing", "conflicted")


class JevUnavailable(Exception):
    pass


def jev_model_configured() -> str:
    return (os.environ.get("JEV_MODEL") or "").strip()


def typesafe_base_url() -> str:
    return (os.environ.get("TYPESAFE_BASE_URL") or DEFAULT_TYPESAFE_BASE_URL).rstrip("/")


# ---------------------------------------------------------------------------
# state construction (validated data only)
# ---------------------------------------------------------------------------
def sanitize_snapshot_for_jev(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    identity = snapshot["identity"]
    official = snapshot.get("official_enrichment") or {}
    official_facts = {}
    for key, fact in (official.get("facts") or {}).items():
        if not fact.get("validated") or fact.get("variant_scope") != "variant":
            continue
        official_facts[key] = {k: fact.get(k) for k in _OFFICIAL_FACT_KEYS if fact.get(k) is not None}
    return {
        "identity": {
            "display_name": identity.get("display_name"),
            "manufacturer": identity.get("make_display"),
            "model": identity.get("model"),
            "model_year": identity.get("model_year"),
            "trim": identity.get("trim"),
            "official_model_code": identity.get("official_model_code"),
        },
        "government_level_1_5": {
            "facts": {k: v for k, v in snapshot["government"]["facts"].items() if v is not None},
            "driver_assistance_systems": {k: v for k, v in snapshot["government"]["equipment"].items() if v is not None},
        },
        "official_level_2": {
            "facts": official_facts,
            "conflicted_fields": sorted({c.get("field") for c in official.get("conflicts") or [] if c.get("field")}),
            "missing_fields": list(official.get("missing") or []),
            "enrichment_status": official.get("status"),
        },
        "derived": {
            "powertrain_family": snapshot["derived"].get("powertrain_family"),
            "is_plugin": snapshot["derived"].get("is_plugin"),
        },
    }


def _compact_atomic(result: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: result.get(k) for k in _ATOMIC_KEYS if result.get(k) not in (None, [], {})}
    prov = result.get("provenance") or {}
    out["source_level"] = {slot: (p or {}).get("source_level") for slot, p in prov.items() if p}
    if result.get("details"):
        out["details"] = result["details"]
    return out


def build_deterministic_evidence(comparison: Dict[str, Any], categories: List[str]) -> Dict[str, Any]:
    evidence = {}
    for cat in categories:
        ev = comparison["categories"][cat]
        evidence[cat] = {
            "atomic_results": [_compact_atomic(r) for r in ev["atomic_results"] if r["status"] != "descriptive"],
            "correlation_groups": ev["group_results"],
            "contextual_facts": [{"metric": c["metric"], "values": c["values"]} for c in ev["contextual_facts"]],
            "not_comparable": [{"metric": n["metric"], "reason": n["reason"]} for n in ev["not_comparable"]],
            "missing_metrics": ev["missing_metrics"],
            "conflicted_metrics": ev["conflicted_metrics"],
            "coverage": ev["coverage"],
        }
    return evidence


def build_jev_request(
    model: str,
    snapshots: Dict[str, Dict[str, Any]],
    comparison: Dict[str, Any],
    ask_categories: List[str],
    buyer_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    slots = list(snapshots.keys())
    state: Dict[str, Any] = {slot: sanitize_snapshot_for_jev(snap) for slot, snap in snapshots.items()}
    state["deterministic_evidence"] = build_deterministic_evidence(comparison, ask_categories)
    state["coverage"] = comparison["coverage"]
    state["buyer_profile"] = buyer_context or {}

    questions: Dict[str, Any] = {}
    for cat in ask_categories:
        questions[cat] = {
            "type": "choice",
            "instructions": (
                f"Category: {cat}. Using only state.deterministic_evidence.{cat} and the validated vehicle data in "
                f"state, which vehicle has a meaningful evidence-supported advantage in {cat}? "
                f"{CATEGORY_GUIDANCE_EN.get(cat, '')} {COMMON_RULES_EN}"
            ),
            "criteria": _criteria(slots, snapshots, scope=cat),
        }
    questions["overall"] = {
        "type": "choice",
        "instructions": f"{OVERALL_WITH_PROFILE if buyer_context else OVERALL_NO_PROFILE} {COMMON_RULES_EN}",
        "criteria": _criteria(slots, snapshots, scope="overall"),
    }
    return {"model": model, "state": state, "questions": questions}


def _criteria(slots: List[str], snapshots: Dict[str, Dict[str, Any]], scope: str) -> Dict[str, str]:
    criteria = {}
    for slot in slots:
        name = snapshots[slot]["identity"].get("display_name") or slot
        number = slot.split("_")[-1]
        criteria[slot] = f"Meaningful evidence-supported advantage for car {number} ({name})"
    criteria[CHOICE_TIE] = "Differences are balanced or not meaningful"
    criteria[CHOICE_INSUFFICIENT] = "Available evidence is insufficient"
    assert list(criteria) == choices_for(slots)
    return criteria


# ---------------------------------------------------------------------------
# response parsing
# ---------------------------------------------------------------------------
def parse_choice_answer(answer: Any, allowed: List[str]) -> Optional[Dict[str, Any]]:
    """Keep JEV's own choice/confidence/probabilities; never compute them."""
    if not isinstance(answer, dict):
        return None
    choice = answer.get("choice")
    if choice not in allowed:
        return None
    confidence = answer.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = None
    probs_raw = answer.get("probabilities")
    probabilities = None
    if isinstance(probs_raw, dict):
        probabilities = {
            str(k): float(v) for k, v in probs_raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
    return {
        "type": answer.get("type") or "choice",
        "choice": choice,
        "confidence": float(confidence) if confidence is not None else None,
        "probabilities": probabilities,
    }


class TypeSafeJevClient:
    """Thin HTTP client. ``session`` is injectable (tests never hit the network)."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, base_url: Optional[str] = None,
                 session: Any = None, timeout_sec: Optional[float] = None):
        self.api_key = api_key if api_key is not None else (os.environ.get("TYPESAFE_API_KEY") or "")
        self.model = model if model is not None else jev_model_configured()
        self.base_url = (base_url or typesafe_base_url()).rstrip("/")
        self.timeout_sec = timeout_sec or float(os.environ.get("JEV_TIMEOUT_SEC", "30"))
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.calls = 0  # systemone calls made by this client

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"}

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    def list_models(self) -> List[str]:
        resp = self.session.get(f"{self.base_url}/v1/models", headers=self._headers(), timeout=min(self.timeout_sec, 15))
        if getattr(resp, "status_code", 500) >= 400:
            raise JevUnavailable(f"models_http_{resp.status_code}")
        return extract_model_ids(resp.json())

    def systemone(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        resp = self.session.post(f"{self.base_url}/v1/systemone", json=payload, headers=self._headers(), timeout=self.timeout_sec)
        if getattr(resp, "status_code", 500) >= 400:
            raise JevUnavailable(f"systemone_http_{resp.status_code}")
        data = resp.json()
        if not isinstance(data, dict):
            raise JevUnavailable("systemone_invalid_json")
        return data


def extract_model_ids(payload: Any) -> List[str]:
    """Collect ids and aliases from a /v1/models response of unknown exact shape."""
    ids: List[str] = []

    def add(val):
        if isinstance(val, str) and val.strip() and val not in ids:
            ids.append(val.strip())

    items = []
    if isinstance(payload, dict):
        for key in ("data", "models", "items"):
            if isinstance(payload.get(key), list):
                items.extend(payload[key])
    elif isinstance(payload, list):
        items = payload
    for item in items:
        if isinstance(item, str):
            add(item)
        elif isinstance(item, dict):
            for key in ("id", "name", "model", "slug"):
                add(item.get(key))
            for key in ("aliases", "alias"):
                val = item.get(key)
                if isinstance(val, list):
                    for alias in val:
                        add(alias)
                else:
                    add(val)
    return ids


_verified_lock = threading.Lock()
_verified: Dict[Tuple[str, str], float] = {}


def reset_model_verification_cache() -> None:
    with _verified_lock:
        _verified.clear()


def verify_model(client: TypeSafeJevClient) -> Tuple[bool, Optional[str]]:
    """True only when JEV_MODEL appears in GET /v1/models for this account."""
    if not client.configured:
        return False, "jev_not_configured"
    cache_key = (client.base_url, client.model)
    now = time.monotonic()
    with _verified_lock:
        seen = _verified.get(cache_key)
        if seen and now - seen < MODELS_CACHE_TTL_SEC:
            return True, None
    try:
        available = client.list_models()
    except Exception as exc:
        return False, f"models_unavailable:{type(exc).__name__}"
    if client.model not in available:
        logger.warning("comparison_v2 jev_model_not_available configured=%s available_count=%s", client.model, len(available))
        return False, "jev_model_not_in_account_models"
    with _verified_lock:
        _verified[cache_key] = now
    return True, None


def unavailable_decisions(categories: List[str], reason: str) -> Dict[str, Any]:
    return {
        "status": "failed",
        "reason": reason,
        "decisions": {cat: {"choice": DECISION_UNAVAILABLE, "confidence": None, "probabilities": None} for cat in categories + ["overall"]},
        "response_model": None,
        "usage": None,
    }


def evaluate_with_jev(
    client: Optional[TypeSafeJevClient],
    snapshots: Dict[str, Dict[str, Any]],
    comparison: Dict[str, Any],
    ask_categories: List[str],
    buyer_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Exactly one systemone call (or zero on failure/unconfigured)."""
    if client is None:
        return unavailable_decisions(ask_categories, "jev_disabled")
    ok, reason = verify_model(client)
    if not ok:
        return unavailable_decisions(ask_categories, reason or "jev_unverified")
    payload = build_jev_request(client.model, snapshots, comparison, ask_categories, buyer_context)
    started = time.perf_counter()
    try:
        data = client.systemone(payload)
    except Exception as exc:
        logger.warning("comparison_v2 jev_failed error=%s", type(exc).__name__)
        out = unavailable_decisions(ask_categories, f"jev_error:{type(exc).__name__}")
        out["duration_ms"] = int((time.perf_counter() - started) * 1000)
        return out

    allowed = choices_for(list(snapshots.keys()))
    answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    decisions: Dict[str, Any] = {}
    for qid in list(ask_categories) + ["overall"]:
        parsed = parse_choice_answer(answers.get(qid), allowed)
        decisions[qid] = parsed or {"choice": DECISION_UNAVAILABLE, "confidence": None, "probabilities": None}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {
        "status": "ok",
        "reason": None,
        "decisions": decisions,
        "response_model": data.get("model") if isinstance(data.get("model"), str) else None,
        "requested_model": client.model,
        "usage": {
            "input_tokens": usage.get("input_tokens") if isinstance(usage.get("input_tokens"), int) else None,
            "output_tokens": usage.get("output_tokens") if isinstance(usage.get("output_tokens"), int) else None,
        },
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def redacted_request_shape(payload: Dict[str, Any]) -> Dict[str, Any]:
    """For docs/diagnostics: headers are never part of the payload; the key never appears."""
    return {
        "endpoint": "POST {TYPESAFE_BASE_URL}/v1/systemone",
        "headers": {"Authorization": "Bearer [REDACTED]", "Content-Type": "application/json"},
        "body": {
            "model": payload.get("model"),
            "state_keys": sorted(payload.get("state", {}).keys()),
            "questions": {qid: {"type": q["type"], "criteria": list(q["criteria"])} for qid, q in payload.get("questions", {}).items()},
        },
    }


def category_label_he(category: str) -> str:
    return CATEGORY_LABELS_HE.get(category, category)
