# -*- coding: utf-8 -*-
"""Deterministic validation + canonical merge of official Level 2 claims.

Order per claim: contract field -> source domain allowlist -> grounding
corroboration (``grounding.GroundingIndex``: url / host / site tier) ->
Israeli-source requirement -> variant scope -> identity match at the
field's identity scope (``field_registry.IDENTITY_SCOPES``) -> exact-value
qualifier -> type/unit normalization -> plausible range -> applicability.
Surviving
claims are grouped per field; disagreeing official sources become a conflict
(excluded from the comparison) unless the field is local and an Israeli
official source outranks a global one.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.comparison_v2.contracts import (
    SOURCE_LEVEL_OFFICIAL,
    VARIANT_SCOPE_MODEL_GENERIC,
    VARIANT_SCOPE_VARIANT,
)
from app.services.comparison_v2.field_registry import (
    FIELD_SPECS,
    GOVERNMENT_CROSSCHECK_SPECS,
    FieldSpec,
    applies_to_family,
    normalize_unit_token,
    pattern_ok,
)
from app.services.comparison_v2.grounding import GroundingIndex
from app.services.comparison_v2.official_variant_matcher import MATCH_STRONG, SCOPE_EXACT_VARIANT, OfficialVariantMatcher
from app.services.comparison_v2.source_registry import (
    MARKET_IL,
    check_official_url,
)

logger = logging.getLogger("comparison_v2")

REJECT_FIELD_UNKNOWN = "FIELD_NOT_IN_CONTRACT"
REJECT_NOT_GROUNDED = "SOURCE_NOT_GROUNDED"
REJECT_ISRAELI_SOURCE_REQUIRED = "ISRAELI_OFFICIAL_SOURCE_REQUIRED"
REJECT_MODEL_GENERIC = "MODEL_GENERIC_NOT_VARIANT"
REJECT_SCOPE_INVALID = "VARIANT_SCOPE_INVALID"
REJECT_TYPE = "VALUE_TYPE_INVALID"
REJECT_UNIT = "UNIT_NOT_ALLOWED"
REJECT_RANGE = "VALUE_OUT_OF_RANGE"
REJECT_NOT_APPLICABLE = "FIELD_NOT_APPLICABLE"
REJECT_STANDARD_MISSING = "MEASUREMENT_STANDARD_MISSING"
REJECT_CURRENCY = "CURRENCY_NOT_ILS"
REJECT_MALFORMED = "CLAIM_MALFORMED"
REJECT_NOT_EXACT = "VALUE_NOT_EXACT"

# ``value_qualifier`` the model reports per claim. Only an exact published
# specification may become a fact: one end of a published range, one of
# several wheel/option-dependent values, or an approximation is rejected
# (never averaged, never the best case).
VALUE_QUALIFIERS = ("exact", "one_of_several", "approximate")

MAX_CLAIMS = 80
MAX_EXTRA_EQUIPMENT = 20

# Bump whenever acceptance semantics change: it is part of every enrichment
# and whole-comparison cache key, so results produced under older semantics
# are never served as if validated by the current rules.
#   /2: grounding correlated by url/host/site tier (redirect targets,
#       URL-context retrievals, bare-host titles; ``domain`` is never needed);
#       powertrain-level identity for trim-independent technical fields;
#       a conflict in a non-selected range standard no longer hides the range.
#   /3: the page publication year never takes part in variant matching (only
#       an explicitly stated vehicle model year does); identity scopes per
#       field (exact_variant / powertrain / powertrain_body /
#       model_generation); combustion output establishes the engine when no
#       displacement is printed; body / generation / seating contradictions;
#       exact model code needs the model name; non-exact values rejected
#       (consumption / range need an explicit 'exact'); several outputs,
#       displacements, generation codes or model years establish none;
#       whole-word trims; diesel vs petrol.
FIELD_VALIDATOR_VERSION = "field-validator/3"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _short(value: Any, limit: int = 160) -> Any:
    if isinstance(value, str):
        return value[:limit]
    return value


def grounded_registry_hosts(manufacturer: str, grounded_sources: Iterable[Dict[str, Any]]) -> Tuple[set, List[str]]:
    """Official hosts present in the grounding evidence, plus ignored hosts."""
    index = GroundingIndex(manufacturer, grounded_sources)
    return set(index.official_hosts), index.ignored


def _normalize_number(spec: FieldSpec, value: Any, unit: Any) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """Return (normalized_value, normalized_unit, reject_reason)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None, None, REJECT_TYPE
    token = normalize_unit_token(unit)
    if spec.group == "commercial" and spec.unit == "ILS" and token not in spec.units:
        return None, None, REJECT_CURRENCY
    converter = spec.units.get(token)
    if converter is None:
        return None, None, REJECT_UNIT
    try:
        normalized = float(converter(float(value)))
    except (ZeroDivisionError, ValueError, OverflowError):
        return None, None, REJECT_TYPE
    if spec.value_type == "int":
        if abs(normalized - round(normalized)) > 1e-6:
            return None, None, REJECT_TYPE
        normalized = int(round(normalized))
    else:
        normalized = round(normalized, 2)
    if (spec.min_value is not None and normalized < spec.min_value) or (
        spec.max_value is not None and normalized > spec.max_value
    ):
        return None, None, REJECT_RANGE
    return normalized, spec.unit, None


def normalize_claim_value(spec: FieldSpec, value: Any, unit: Any) -> Tuple[Any, Optional[str], Optional[str]]:
    if spec.value_type in ("number", "int"):
        return _normalize_number(spec, value, unit)
    if spec.value_type == "bool":
        if isinstance(value, bool):
            return value, None, None
        return None, None, REJECT_TYPE
    if spec.value_type == "enum":
        text = str(value).strip() if isinstance(value, str) else None
        if not text:
            return None, None, REJECT_TYPE
        for allowed in spec.enum_values:
            if text.lower() == allowed.lower():
                return allowed, None, None
        return None, None, REJECT_TYPE
    if spec.value_type == "str":
        if not isinstance(value, str) or not value.strip() or len(value) > 60 or not pattern_ok(spec, value):
            return None, None, REJECT_TYPE
        return value.strip().upper(), None, None
    return None, None, REJECT_TYPE


def _values_agree(spec: FieldSpec, a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        diff = abs(float(a) - float(b))
        if diff <= spec.conflict_abs:
            return True
        base = max(abs(float(a)), abs(float(b)), 1e-9)
        return diff / base <= spec.conflict_rel
    return a == b


class FieldValidator:
    """Validates one car's raw extraction against its Level 1.5 snapshot."""

    def __init__(self, matcher: Optional[OfficialVariantMatcher] = None):
        self.matcher = matcher or OfficialVariantMatcher()

    def validate(
        self,
        snapshot: Dict[str, Any],
        raw_output: Any,
        grounded_sources: List[Dict[str, Any]],
        *,
        requested_fields: Optional[Iterable[str]] = None,
        observed_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        manufacturer = snapshot["identity"]["manufacturer"]
        family = snapshot["derived"]["powertrain_family"]
        observed_at = observed_at or _utcnow_iso()
        index = GroundingIndex(manufacturer, grounded_sources)
        requested = set(requested_fields) if requested_fields else set(FIELD_SPECS)

        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        model_generic: List[Dict[str, Any]] = []
        government_conflicts: List[Dict[str, Any]] = []

        claims = raw_output.get("claims") if isinstance(raw_output, dict) else None
        if not isinstance(claims, list):
            claims = []

        for raw in claims[:MAX_CLAIMS]:
            verdict = self._validate_claim(snapshot, raw, family, index, observed_at)
            status = verdict.pop("_status")
            if status == "accepted":
                if verdict["field"] in requested:
                    accepted.append(verdict)
            elif status == "model_generic":
                model_generic.append(verdict)
            elif status == "government":
                if verdict.get("conflict"):
                    government_conflicts.append(verdict)
            else:
                rejected.append(verdict)
                years = (verdict.get("variant_match") or {}).get("years") or {}
                logger.info(
                    "comparison_v2 official_claim_rejected vehicle=%s field=%s reason=%s host=%s scope=%s%s",
                    snapshot["vehicle_id"][:12],
                    verdict.get("field"),
                    verdict.get("reason"),
                    verdict.get("host"),
                    (verdict.get("variant_match") or {}).get("scope"),
                    (" government_model_year=%s claimed_vehicle_model_year=%s source_publication_year=%s" % (
                        years.get("government_model_year"), years.get("claimed_vehicle_model_year"),
                        years.get("source_publication_year"))) if verdict.get("reason") == "VARIANT_YEAR_MISMATCH" else "",
                )

        facts, conflicts, superseded, standard_conflicts = self._merge(accepted)
        facts = self._post_merge_checks(facts, conflicts, rejected)
        extra_equipment = self._extra_equipment(raw_output, index)
        for conflict in conflicts:
            logger.info(
                "comparison_v2 official_claim_conflict vehicle=%s field=%s values=%s",
                snapshot["vehicle_id"][:12],
                conflict["field"],
                [c.get("normalized_value") for c in conflict["claims"]],
            )
        for gov in government_conflicts:
            logger.info(
                "comparison_v2 official_claim_conflict vehicle=%s field=%s kind=government level15=%s official=%s",
                snapshot["vehicle_id"][:12],
                gov["field"],
                gov.get("level15_value"),
                gov.get("normalized_value"),
            )

        applicable = [k for k, s in FIELD_SPECS.items() if applies_to_family(s, family) and k in requested]
        conflicted_fields = {c["field"] for c in conflicts}
        missing = [k for k in applicable if k not in facts and k not in conflicted_fields]

        sources: Dict[str, Dict[str, Any]] = {}
        for fact in facts.values():
            for src in fact["sources"]:
                sources.setdefault(src["source_url"], src)

        return {
            "facts": facts,
            "claims": accepted,
            "rejected_claims": rejected,
            "conflicts": conflicts,
            "superseded_claims": superseded,
            "government_conflicts": government_conflicts,
            "model_generic_claims": model_generic,
            "extra_official_equipment": extra_equipment,
            "missing": missing,
            "sources": list(sources.values()),
            "ignored_grounding_hosts": index.ignored,
            "grounded_official_hosts": index.official_hosts,
            "grounded_official_markets": index.official_markets,
            "grounding_index": index.summary(),
            "range_standard_conflicts": standard_conflicts,
        }

    # ------------------------------------------------------------------
    def _validate_claim(self, snapshot, raw, family, index: GroundingIndex, observed_at) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return {"_status": "rejected", "field": None, "reason": REJECT_MALFORMED}
        field = raw.get("field")
        base = {
            "field": field if isinstance(field, str) else None,
            "raw_value": _short(raw.get("value")),
            "raw_unit": _short(raw.get("unit"), 24),
            "source_url": _short(raw.get("source_url"), 400),
            "source_title": _short(raw.get("source_title"), 160),
        }
        spec = FIELD_SPECS.get(field) if isinstance(field, str) else None
        gov_spec = GOVERNMENT_CROSSCHECK_SPECS.get(field) if isinstance(field, str) else None
        if spec is None and gov_spec is None:
            return {"_status": "rejected", **base, "reason": REJECT_FIELD_UNKNOWN}

        manufacturer = snapshot["identity"]["manufacturer"]
        url_verdict = check_official_url(manufacturer, raw.get("source_url"))
        base["host"] = url_verdict.get("host")
        if not url_verdict["allowed"]:
            return {"_status": "rejected", **base, "reason": url_verdict["reason"]}
        # The allowlist alone is never enough: Google's own grounding evidence
        # must show this URL / host / official site was actually retrieved.
        tier, grounded_host = index.correlate(raw.get("source_url"))
        if not index.accepts(tier):
            return {"_status": "rejected", **base, "reason": REJECT_NOT_GROUNDED}
        base["grounding_tier"] = tier
        base["grounded_host"] = grounded_host

        # Market and source type come from the registry, never from the model.
        market = url_verdict["market"]
        base.update({"source_market": market, "source_type": url_verdict["source_type"]})
        model_market = raw.get("source_market")
        if isinstance(model_market, str) and model_market.upper() not in (market, ""):
            base["model_reported_market"] = model_market[:8]

        variant_scope = raw.get("variant_scope")
        if variant_scope == VARIANT_SCOPE_MODEL_GENERIC:
            return {"_status": "model_generic", **base, "reason": REJECT_MODEL_GENERIC}
        if variant_scope != VARIANT_SCOPE_VARIANT:
            return {"_status": "rejected", **base, "reason": REJECT_SCOPE_INVALID}

        active_spec = spec or gov_spec
        if spec is not None and spec.israeli_only and market != MARKET_IL:
            return {"_status": "rejected", **base, "reason": REJECT_ISRAELI_SOURCE_REQUIRED}

        scope = spec.identity_scope if spec is not None else SCOPE_EXACT_VARIANT
        match = self.matcher.match(snapshot, raw, scope)
        base["variant_match"] = {"status": match["status"], "matched_by": match["matched_by"], "reasons": match["reasons"],
                                 "scope": scope, "years": match.get("years") or {}}
        if match["status"] != MATCH_STRONG:
            return {"_status": "rejected", **base, "reason": match["status"] if match["status"] != "VARIANT_MISMATCH" else (match["reasons"] or ["VARIANT_MISMATCH"])[0]}

        qualifier = raw.get("value_qualifier")
        qualifier = qualifier.strip().lower() if isinstance(qualifier, str) else ""
        if (qualifier not in ("", "exact")) or (spec is not None and spec.requires_exact_qualifier and qualifier != "exact"):
            return {"_status": "rejected", **base, "reason": REJECT_NOT_EXACT, "value_qualifier": qualifier[:24] or None}

        value, unit, err = normalize_claim_value(active_spec, raw.get("value"), raw.get("unit"))
        if err:
            return {"_status": "rejected", **base, "reason": err}

        if gov_spec is not None:
            level15 = snapshot["government"]["facts"].get(field)
            conflict = level15 is not None and not _values_agree(gov_spec, level15, value)
            return {
                "_status": "government",
                **base,
                "normalized_value": value,
                "normalized_unit": unit,
                "level15_value": level15,
                "conflict": conflict,
                "reason": "GOVERNMENT_FIELD_NOT_OVERRIDABLE",
            }

        if not applies_to_family(spec, family):
            return {"_status": "rejected", **base, "reason": REJECT_NOT_APPLICABLE}

        standard = None
        if spec.requires_standard:
            standard = raw.get("measurement_standard")
            if not isinstance(standard, str) or standard.upper() not in ("WLTP", "EPA", "NEDC", "CLTC"):
                return {"_status": "rejected", **base, "reason": REJECT_STANDARD_MISSING}
            standard = standard.upper()

        years = match.get("years") or {}
        claim = {
            "_status": "accepted",
            **base,
            "normalized_value": value,
            "normalized_unit": unit,
            # provenance only — never used for matching
            "source_publication_year": years.get("source_publication_year"),
            "vehicle_model_year": years.get("claimed_vehicle_model_year"),
            "identity_scope": scope,
            "variant_scope": VARIANT_SCOPE_VARIANT,
            "source_level": SOURCE_LEVEL_OFFICIAL,
            "validated": True,
            "observed_at": observed_at,
            "freshness_group": spec.freshness,
        }
        if standard:
            claim["measurement_standard"] = standard
        if spec.group == "commercial" and spec.unit == "ILS":
            claim["currency"] = "ILS"
        return claim

    # ------------------------------------------------------------------
    def _merge(self, accepted: List[Dict[str, Any]]):
        by_field: Dict[str, List[Dict[str, Any]]] = {}
        for claim in accepted:
            key = claim["field"]
            if key == "electric_range_km":
                key = f"electric_range_km::{claim.get('measurement_standard')}"
            by_field.setdefault(key, []).append(claim)

        facts: Dict[str, Dict[str, Any]] = {}
        conflicts: List[Dict[str, Any]] = []
        superseded: List[Dict[str, Any]] = []

        range_groups = {k: v for k, v in by_field.items() if k.startswith("electric_range_km::")}
        for key in list(range_groups):
            by_field.pop(key)

        for field, claims in by_field.items():
            spec = FIELD_SPECS[field]
            candidates = claims
            if spec.local_authority:
                il_claims = [c for c in claims if c["source_market"] == MARKET_IL]
                if il_claims and len(il_claims) != len(claims):
                    superseded.extend(
                        {**c, "reason": "SUPERSEDED_BY_ISRAELI_OFFICIAL_SOURCE"}
                        for c in claims if c["source_market"] != MARKET_IL
                    )
                    candidates = il_claims
            merged = self._merge_group(spec, field, candidates)
            if merged.get("status") == "conflict":
                conflicts.append(merged)
            else:
                facts[field] = merged

        # Electric range: one value per measurement standard; WLTP preferred.
        # Different standards are different facts, never a conflict and never
        # converted into one another.
        standard_values: Dict[str, Dict[str, Any]] = {}
        for key, claims in range_groups.items():
            standard = key.split("::", 1)[1]
            merged = self._merge_group(FIELD_SPECS["electric_range_km"], "electric_range_km", claims)
            if merged.get("status") == "conflict":
                merged["measurement_standard"] = standard
                conflicts.append(merged)
            else:
                merged["measurement_standard"] = standard
                standard_values[standard] = merged
        range_conflicts = [c for c in conflicts if c["field"] == "electric_range_km"]
        standard_conflicts: List[Dict[str, Any]] = []
        for preferred in ("WLTP", "EPA", "CLTC", "NEDC"):
            if preferred in standard_values:
                chosen = standard_values.pop(preferred)
                chosen["alternate_standards"] = {
                    std: {"value": v["value"], "sources": v["sources"]} for std, v in standard_values.items()
                }
                facts["electric_range_km"] = chosen
                facts["electric_range_standard"] = {
                    "field": "electric_range_standard",
                    "value": preferred,
                    "unit": None,
                    "status": "validated",
                    "sources": chosen["sources"],
                    **{k: chosen[k] for k in ("source_level", "source_type", "source_url", "source_title", "source_market", "validated", "variant_scope", "observed_at", "freshness_group")},
                }
                # A disagreement inside ANOTHER standard does not make the
                # selected standard's value uncertain. Keeping it in
                # ``conflicts`` (keyed by field name) would hide the valid
                # selected range from the comparison, so it is reported
                # separately instead.
                if range_conflicts:
                    standard_conflicts = range_conflicts
                    conflicts = [c for c in conflicts if c["field"] != "electric_range_km"]
                    chosen["conflicting_standards"] = sorted({c.get("measurement_standard") for c in range_conflicts if c.get("measurement_standard")})
                break
        return facts, conflicts, superseded, standard_conflicts

    def _merge_group(self, spec: FieldSpec, field: str, claims: List[Dict[str, Any]]) -> Dict[str, Any]:
        first = claims[0]
        for other in claims[1:]:
            if not _values_agree(spec, first["normalized_value"], other["normalized_value"]):
                return {
                    "field": field,
                    "status": "conflict",
                    "label_he": spec.label_he,
                    "claims": [
                        {k: c.get(k) for k in ("normalized_value", "normalized_unit", "source_url", "source_title", "source_market", "source_type")}
                        for c in claims
                    ],
                }
        # Prefer an Israeli official source as the primary provenance.
        primary = sorted(claims, key=lambda c: (c["source_market"] != MARKET_IL, c["source_url"] or ""))[0]
        sources = []
        seen = set()
        for c in claims:
            if c["source_url"] in seen:
                continue
            seen.add(c["source_url"])
            sources.append({k: c.get(k) for k in ("source_url", "source_title", "source_market", "source_type", "grounding_tier")})
        fact = {
            "field": field,
            "value": primary["normalized_value"],
            "unit": primary["normalized_unit"],
            "raw_value": primary["raw_value"],
            "raw_unit": primary["raw_unit"],
            "normalized_value": primary["normalized_value"],
            "normalized_unit": primary["normalized_unit"],
            "status": "validated",
            "source_level": SOURCE_LEVEL_OFFICIAL,
            "source_type": primary["source_type"],
            "source_url": primary["source_url"],
            "source_title": primary["source_title"],
            "source_market": primary["source_market"],
            "source_publication_year": primary.get("source_publication_year"),
            "vehicle_model_year": primary.get("vehicle_model_year"),
            "validated": True,
            "variant_scope": VARIANT_SCOPE_VARIANT,
            "observed_at": primary["observed_at"],
            "freshness_group": primary["freshness_group"],
            "grounding_tier": _best_tier(c.get("grounding_tier") for c in claims),
            "identity_match": (primary.get("variant_match") or {}).get("matched_by"),
            "identity_scope": primary.get("identity_scope"),
            "sources": sources,
        }
        if primary.get("currency"):
            fact["currency"] = primary["currency"]
            fact["market"] = primary["source_market"]
        return fact

    def _post_merge_checks(self, facts, conflicts, rejected):
        """Cross-field sanity: DC charge window must be ordered."""
        lo = facts.get("dc_charge_from_pct")
        hi = facts.get("dc_charge_to_pct")
        if lo and hi and lo["value"] >= hi["value"]:
            for key in ("dc_charge_from_pct", "dc_charge_to_pct", "dc_charge_time_minutes"):
                fact = facts.pop(key, None)
                if fact:
                    rejected.append({"field": key, "reason": "DC_CHARGE_WINDOW_INVALID", "source_url": fact["source_url"]})
        if "dc_charge_time_minutes" in facts and not (lo and hi):
            # A charge time without its window is not comparable; keep it
            # descriptive only.
            facts["dc_charge_time_minutes"]["comparable"] = False
        return facts

    def _extra_equipment(self, raw_output, index: GroundingIndex) -> List[Dict[str, Any]]:
        items = raw_output.get("extra_official_equipment") if isinstance(raw_output, dict) else None
        out: List[Dict[str, Any]] = []
        for item in (items if isinstance(items, list) else [])[:MAX_EXTRA_EQUIPMENT]:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            verdict = check_official_url(index.manufacturer, item.get("source_url"))
            if not isinstance(name, str) or not name.strip() or not verdict["allowed"]:
                continue
            tier, _ = index.correlate(item.get("source_url"))
            if not index.accepts(tier):
                continue
            out.append(
                {
                    "name": name.strip()[:80],
                    "value": _short(item.get("value"), 60) if isinstance(item.get("value"), (str, int, float, bool)) else None,
                    "source_url": item.get("source_url"),
                    "source_market": verdict["market"],
                    "grounding_tier": tier,
                    "weighted": False,
                }
            )
        return out


_TIER_RANK = {"url": 0, "host": 1, "site": 2}


def _best_tier(tiers) -> Optional[str]:
    ranked = sorted((t for t in tiers if t in _TIER_RANK), key=_TIER_RANK.get)
    return ranked[0] if ranked else None
