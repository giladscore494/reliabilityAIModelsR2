# -*- coding: utf-8 -*-
"""Prompt builders for comparison Stage A and Stage B calls."""

import json
import os
from typing import Any, Dict, List, Optional

from app.services.vehicle_catalog_service import build_vehicle_catalog_context
from app.services.comparison.constants import CATEGORY_LABELS_HE, COMPARE_CATEGORY_NAMES, DECISION_CATEGORY_DEFINITIONS, _MAX_STAGE_A_SOURCES
from app.services.comparison.normalization import (
    infer_compare_segment_details,
    normalize_compare_writer_winner,
    ordered_compare_slot_keys,
    build_checked_versions,
    build_display_name,
)
from app.utils.prompt_defense import (
    create_data_only_instruction,
    escape_prompt_input,
    wrap_user_input_in_boundary,
)


COMPARE_WRITER_PROMPT_CHAR_CAP = int(
    os.environ.get("COMPARE_WRITER_PROMPT_CHAR_CAP", "16000")
)



COMPARE_SEGMENT_PROMPT_RULES = {
    "city_mini": {
        "focus_more": [
            "urban maneuverability",
            "parking ease",
            "fuel economy / efficiency",
            "low routine running costs",
            "reliability under city use",
            "visibility",
        ],
        "focus_less": [
            "high-speed performance",
            "towing",
            "off-road ability",
        ],
    },
    "supermini_hatch": {
        "focus_more": [
            "efficiency",
            "reliability",
            "city + intercity usability balance",
            "hatch practicality",
            "ease of ownership",
            "cabin/package efficiency",
            "value for money",
        ],
    },
    "family_sedan_hatch_wagon": {
        "focus_more": [
            "safety",
            "rear-seat usability",
            "trunk / cargo usability",
            "ride comfort",
            "highway refinement",
            "fuel economy",
            "ownership stability / reliability",
        ],
        "focus_less": [
            "sporty handling unless this is a sporty trim",
        ],
        "special_note": "If hatchback or wagon hints appear, reward cargo flexibility and easier loading access.",
    },
    "crossover_soft_suv": {
        "focus_more": [
            "family usability",
            "seating height / ease of entry",
            "cargo space",
            "comfort",
            "safety tech",
            "efficiency relative to size",
            "reliability / ownership simplicity",
        ],
        "focus_less": [
            "hardcore off-road capability unless clearly relevant",
        ],
    },
    "three_row_family_suv": {
        "focus_more": [
            "real 3rd-row usability",
            "passenger space in all rows",
            "cargo space with seats up/down",
            "family safety",
            "comfort on long trips",
            "ease of child-seat/family use",
            "efficiency and ownership burden",
            "practical value",
        ],
        "focus_less": [
            "sporty driving feel unless very relevant",
        ],
    },
    "hardcore_4x4": {
        "focus_more": [
            "real 4WD capability",
            "low range if present",
            "locking differentials",
            "ground clearance",
            "approach / breakover / departure angles",
            "durability",
            "tire/underbody readiness",
            "payload / trail utility",
            "reliability in rough use",
        ],
        "focus_less": [
            "ride softness penalties when the vehicle is clearly off-road focused",
        ],
        "special_note": "Do penalize high ownership burden and chronic durability risks.",
    },
    "pickup_truck": {
        "focus_more": [
            "towing",
            "payload",
            "bed utility",
            "drivetrain suitability",
            "work/family mission fit",
            "durability",
            "fuel/running cost",
            "comfort/noise if it is also a daily driver",
        ],
        "focus_less": [
            "family sedan priorities",
        ],
    },
    "minivan_mpv": {
        "focus_more": [
            "passenger space",
            "cargo flexibility",
            "family ergonomics",
            "sliding-door practicality if applicable",
            "comfort",
            "child-seat friendliness",
            "low-stress ownership",
            "value",
        ],
        "focus_less": [
            "sporty styling or handling",
        ],
    },
    "sporty_dynamic": {
        "focus_more": [
            "steering response",
            "steering feedback",
            "body control",
            "balance",
            "braking confidence",
            "traction",
            "throttle/power delivery",
            "driver engagement",
            "stability at speed",
        ],
        "focus_less": [
            "family-car practicality expectations",
        ],
        "special_note": "Still include reliability and running costs, but do not judge it by the same comfort/practicality standard as a family car.",
    },
    "executive_luxury": {
        "focus_more": [
            "refinement",
            "cabin isolation",
            "seat comfort",
            "material quality",
            "tech usability",
            "highway comfort",
            "prestige-appropriate ownership burden",
            "reliability risk of complex systems",
            "resale / ownership cost realism",
        ],
    },
    "general_private_car": {
        "focus_more": [
            "safety",
            "reliability",
            "ownership cost",
            "comfort",
            "practicality",
            "efficiency",
            "drivability",
        ],
    },
}


COMPARE_CATEGORY_BEHAVIOR_RULES = {
    "reliability_risk": (
        "Use segment-aware reliability expectations. Family cars should emphasize reliability consistency, "
        "safety-related faults, gearbox/engine risk, and long-term ownership stress. Hardcore 4x4s should "
        "include drivetrain durability and rugged-use tolerance. Sporty cars should include brake/thermal "
        "stress, drivetrain complexity, and whether performance hardware raises failure exposure."
    ),
    "ownership_cost": (
        "Use segment-aware cost expectations. City cars should emphasize fuel, tires, routine maintenance, "
        "and insurance burden. Family SUVs should include fuel, tires, maintenance, and depreciation pressure. "
        "Sporty/luxury cars should include consumables, tires, brakes, complex systems, and premium repairs. "
        "Pickups/4x4s should include fuel, tires, suspension wear, and drivetrain/service burden."
    ),
    "practicality_comfort": (
        "Evaluate according to mission. Family cars should emphasize rear seat, trunk, and child/family use. "
        "Hatches/wagons should reward flexibility and loading ease. 3-row SUVs should emphasize usable third row "
        "and cargo tradeoffs. Minivans should emphasize family ergonomics and space efficiency. Sporty cars only "
        "need enough daily usability; do not demand SUV practicality."
    ),
    "driving_performance": (
        "Use segment-aware meaning. Family cars should emphasize confidence, smoothness, stability, and easy "
        "drivability. Crossovers/SUVs should emphasize predictability, visibility, comfort, and adequate power. "
        "Sporty cars should emphasize handling precision, balance, response, braking, and engagement. Off-roaders "
        "should include off-road control plus acceptable on-road competence. Pickups should emphasize loaded "
        "stability, torque delivery, and towing confidence when relevant."
    ),
}


def build_compare_grounding_prompt(
    cars: List[Dict[str, str]], region: str = "IL", language: str = "he/en"
) -> str:
    """Build Stage A grounded prompt (sources + factual metrics)."""
    return f"{build_comparison_prompt(cars)}\n\nGrounding scope: region={region}, language={language}."


def build_single_car_prompt(car: Dict, region: str = "IL") -> str:
    """Build Stage A prompt for a SINGLE car using catalog-first compact evidence."""
    sanitized = {
        "make": escape_prompt_input(car.get("make", ""), max_length=50),
        "model": escape_prompt_input(car.get("model", ""), max_length=100),
    }
    if car.get("year"):
        sanitized["year"] = int(car["year"])
    if car.get("engine_type"):
        sanitized["engine_type"] = escape_prompt_input(car["engine_type"], max_length=50)
    if car.get("gearbox"):
        sanitized["gearbox"] = escape_prompt_input(car["gearbox"], max_length=50)
    bounded_car = wrap_user_input_in_boundary(json.dumps(sanitized, ensure_ascii=False), boundary_tag="car_input")
    data_instruction = create_data_only_instruction()
    catalog_block = build_vehicle_catalog_context(car)["prompt_block"]

    # Compact schema built from a Python dict + json.dumps to avoid
    # f-string brace issues and reduce prompt size.
    compact_schema = {
        "car_name": None,
        "car_profile": {
            "catalog_identity": {
                "source": "catalog",
                "match_type": None,
                "make": None,
                "model": None,
                "year": None,
                "version_or_trim": None,
                "body_type": None,
                "fuel_type": None,
                "engine": None,
                "horsepower_hp": None,
                "transmission": None,
                "drivetrain": None,
                "year_start": None,
                "year_end": None,
                "support_level": None,
            },
            "evidence": [
                {
                    "area": None,
                    "claim": None,
                    "confidence": None,
                    "source_urls": [],
                }
            ],
            "facts": {
                "horsepower": None,
                "weight_kg": None,
                "body_type": None,
                "fuel_type": None,
            },
            "research_status": {
                "status": None,
                "checked_areas": [],
                "open_fields": [],
            },
            "uncertainties_conflicts": [],
        },
        "reliability": {
            "overall": None,
            "issue_frequency": None,
            "issue_severity": None,
            "repair_cost_risk": None,
            "recall_risk": None,
            "parts_complexity": None,
        },
        "ownership_cost": {
            "fuel_cost": None,
            "routine_maintenance": None,
            "repair_burden": None,
            "insurance_burden": None,
            "depreciation_risk": None,
        },
        "comfort_practicality": {
            "space": None,
            "ride_comfort": None,
            "trunk_usefulness": None,
            "daily_usability": None,
        },
        "performance_driving": {
            "power_feel": None,
            "power_to_weight": None,
            "braking_confidence": None,
            "handling_agility": None,
            "fun_to_drive": None,
        },
        "facts": {
            "horsepower": None,
            "weight_kg": None,
            "body_type": None,
            "fuel_type": None,
        },
        "short_notes": [],
        "sources": [],
    }
    schema_json = json.dumps(compact_schema, ensure_ascii=False, indent=None)

    return (
        "Return a single valid JSON object only. The first character must be { and the last character must be }.\n"
        "No markdown, no code fences, no explanation, no conversational text.\n"
        "Use only catalog context and grounded source facts; never invent values. Unknown values: null or [].\n"
        f"{data_instruction}\n\n"
        f"{catalog_block}\n\n"
        "Task: Stage A evidence collection for one Israeli-market car.\n"
        "Catalog-first: exact catalog identity fields must come from LOCAL_VEHICLE_CATALOG_CONTEXT. "
        "Web evidence is only for analytical claims; conflicts go in uncertainties_conflicts.\n"
        "Use Google Search grounding for analytical claims; ground them with "
        "official/safety/importer/credible automotive/price sources.\n\n"
        f"{bounded_car}\n"
        f"Region: {region}\n\n"
        f"JSON schema shape:\n{schema_json}\n\n"
        "Do not output placeholder enums like 'high|medium|low'. "
        "Fill car_profile.evidence, compact scoring sections only when supported, top-level facts, and URL sources. "
        "No comparison, scores, winner, or invented facts."
    )


# Single-pass grounded compare prompt body (collect → reason → decide in ONE
# grounded call). Kept as a plain string (not an f-string) so the embedded JSON
# braces need no escaping. Runtime inputs are appended below.
SINGLE_PASS_COMPARE_PROMPT_BODY = r"""ROLE
You are a senior Israeli used-car comparison analyst. Return commercial-quality, practical Hebrew in a SHORT JSON object.
Catalog identity is locked: exact local catalog fields are facts. Use Google Search grounding for non-catalog facts. If a fact is not catalog-backed or source-backed, omit it.

OUTPUT RULES
- Return one valid JSON object only. No markdown. No prose outside JSON. No comments. No trailing commas.
- Short valid JSON is better than long broken JSON.
- No scoring, no /100, no category_winners, no metric_winners, no legacy/scoring hint fields, no important_caveat.
- Do not require exactly 9 categories. category_decisions may be []. Maximum 4 items.
- choose/avoid arrays may be []. Maximum 3 short strings per car.
- key_differences maximum 4 items. competitors_to_consider maximum 3.
- overall_decision.text max 240 Hebrew chars. category_decisions[].why max 180 Hebrew chars. practical_summary max 300 Hebrew chars.
- Do not put citations like [1.1.1] in text fields. Sources only in top-level sources array.
- Do not output these phrases anywhere: מידע חסר, לא מאומת, דורש אימות, מחקר חלקי, אין מספיק מידע, לא נמצא מידע, unknown, unavailable, not verified, missing data, insufficient data.
- If sources is empty and there is no strong catalog-only basis: set label "unknown" and text "לא ניתן להשלים השוואה אמינה כרגע. אפשר לנסות שוב בעוד רגע או לדייק שנתון, מנוע ורמת גימור."

ALLOWED JSON SHAPE
{
  "decision_result": {
    "overall_decision": {"label": "car_1|car_2|car_3|tie|depends|unknown", "text": "short Hebrew decision summary"},
    "category_decisions": [
      {"category_key": "pricing_and_value|fuel_consumption|official_safety|powertrain_and_performance|reliability_and_risk|family_daily_use|resale_and_market_confidence|ownership_cost|comfort_practicality", "category_name_he": "string", "preferred": "car_1|car_2|car_3|tie|depends|unknown", "why": "short Hebrew evidence-based explanation"}
    ],
    "key_differences": [
      {"title": "short Hebrew title", "car_1": "short value", "car_2": "short value", "car_3": "short value or null", "meaning_for_buyer": "short Hebrew meaning"}
    ],
    "choose_car_1_if": [],
    "choose_car_2_if": [],
    "choose_car_3_if": [],
    "avoid_or_check_car_1_if": [],
    "avoid_or_check_car_2_if": [],
    "avoid_or_check_car_3_if": [],
    "competitors_to_consider": [{"model": "string", "why_consider": "short Hebrew reason", "confidence": "high|medium|low"}],
    "practical_summary": "short Hebrew paragraph"
  },
  "checked_versions": {
    "car_1": {"make": "string|null", "model": "string|null", "year": "string|null", "version_or_trim": "string|null", "engine_type": "string|null", "transmission": "string|null", "drivetrain": "string|null", "seats": "string|null", "notes": "string|null"},
    "car_2": {"make": "string|null", "model": "string|null", "year": "string|null", "version_or_trim": "string|null", "engine_type": "string|null", "transmission": "string|null", "drivetrain": "string|null", "seats": "string|null", "notes": "string|null"}
  },
  "sources": ["url"]
}
"""

def build_single_pass_compare_prompt(
    cars: List[Dict[str, str]],
    buyer_profile: Optional[Dict[str, Any]] = None,
    region: str = "IL",
) -> str:
    """Build the ONE grounded compare call (collect + reason + decide).

    Drop-in replacement for the Stage A grounding + Stage B writer pair: a
    single Google-grounded Pro call that returns the existing decision_result
    schema (plus checked_versions + sources). The locked catalog identity for
    each car is injected so the model treats exact matches as facts.
    """
    sanitized_cars: List[Dict[str, Any]] = []
    catalog_blocks: List[str] = []
    for i, car in enumerate(cars):
        sanitized_car = {
            "slot": f"car_{i + 1}",
            "make": escape_prompt_input(car.get("make", ""), max_length=50),
            "model": escape_prompt_input(car.get("model", ""), max_length=100),
        }
        if car.get("year"):
            sanitized_car["year"] = int(car.get("year"))
        if car.get("engine_type"):
            sanitized_car["engine_type"] = escape_prompt_input(
                car.get("engine_type", ""), max_length=50
            )
        if car.get("gearbox"):
            sanitized_car["gearbox"] = escape_prompt_input(
                car.get("gearbox", ""), max_length=50
            )
        sanitized_cars.append(sanitized_car)
        catalog_blocks.append(
            f"[car_{i + 1}] {build_vehicle_catalog_context(car)['prompt_block']}"
        )

    slot_mapping_text = "\n".join(
        f"  car_{i + 1}: {build_display_name(car)}" for i, car in enumerate(cars)
    )
    bounded_cars = wrap_user_input_in_boundary(
        json.dumps(sanitized_cars, ensure_ascii=False), boundary_tag="cars_input"
    )
    buyer_profile_json = json.dumps(buyer_profile or {}, ensure_ascii=False)
    data_instruction = create_data_only_instruction()

    return (
        f"{SINGLE_PASS_COMPARE_PROMPT_BODY}\n\n"
        "================ INPUTS ================\n"
        f"region: {region}\n\n"
        f"{data_instruction}\n\n"
        "locked_catalog[] (per-car catalog identity — exact matches are FACTS):\n"
        f"{chr(10).join(catalog_blocks)}\n\n"
        "Slot keys to use (do not rename):\n"
        f"{slot_mapping_text}\n\n"
        f"User-selected cars:\n{bounded_cars}\n\n"
        f"buyer_profile (preference context only):\n{buyer_profile_json}\n"
    )


def build_comparison_prompt(cars: List[Dict[str, str]]) -> str:
    """Build compact Stage A prompt for multiple cars."""
    sanitized_cars = []
    catalog_blocks = []
    for i, car in enumerate(cars):
        sanitized_car = {"make": escape_prompt_input(car.get("make", ""), max_length=50), "model": escape_prompt_input(car.get("model", ""), max_length=100)}
        if car.get("year"):
            sanitized_car["year"] = int(car.get("year"))
        if car.get("engine_type"):
            sanitized_car["engine_type"] = escape_prompt_input(car.get("engine_type", ""), max_length=50)
        if car.get("gearbox"):
            sanitized_car["gearbox"] = escape_prompt_input(car.get("gearbox", ""), max_length=50)
        sanitized_cars.append(sanitized_car)
        catalog_blocks.append(f"[car_{i+1}] {build_vehicle_catalog_context(car)['prompt_block']}")
    bounded_cars = wrap_user_input_in_boundary(json.dumps(sanitized_cars, ensure_ascii=False), boundary_tag="cars_input")
    data_instruction = create_data_only_instruction()
    slot_mapping_text = "\n".join(f"  car_{i+1}: {build_display_name(car)}" for i, car in enumerate(sanitized_cars))
    return f"""
{data_instruction}

{chr(10).join(catalog_blocks)}

You are Stage A for comparison: per-car grounded evidence only. Do not compare, rank, score, or recommend.
For exact catalog matches, identity fields come from the local catalog. Web search is mandatory for non-identity analysis. Report identity conflicts instead of changing catalog identity.

{bounded_cars}
Use exact slot keys:
{slot_mapping_text}

Return ONLY JSON:
{{
  "grounding_successful": true,
  "search_queries_used": [],
  "assumptions": {{}},
  "cars": {{
    "car_1": {{
      "catalog_identity": {{"match_type":"exact|ambiguous|unmatched","identity_basis":"catalog_exact|catalog_ambiguous|web_resolved|unmatched","make":"","model":"","canonical_model":null,"year":null,"version_or_trim":null,"body_type":null,"fuel_type":null,"engine":null,"engine_displacement_l":null,"horsepower_hp":null,"transmission":null,"drivetrain":null,"year_start":null,"year_end":null,"support_level":null}},
      "pricing": {{"new_price_range_ils":null,"used_price_range_ils":null,"notes":[],"sources":[]}},
      "trim_equipment_summary": {{"trims":[],"summary":null,"sources":[]}},
      "license_running_cost": {{"license_fee":null,"maintenance_cost_pressure":"unknown|low|medium|high","notes":[],"sources":[]}},
      "fuel_energy": {{"official":null,"real_world":null,"notes":[],"sources":[]}},
      "official_safety": {{"rating":null,"organization":null,"test_year":null,"notes":[],"sources":[]}},
      "powertrain_performance": {{"engine":null,"gearbox":null,"drivetrain":null,"horsepower":null,"torque_nm":null,"zero_to_100_sec":null,"notes":[],"sources":[]}},
      "reliability_risks": {{"top_risks":[],"recalls":[],"maintenance_complexity":"unknown|low|medium|high","sources":[]}},
      "practicality": {{"body_type":null,"space":null,"trunk_liters":null,"seats":null,"notes":[],"sources":[]}},
      "resale_market": {{"supply":null,"depreciation_risk":"unknown|low|medium|high","notes":[],"sources":[]}},
      "sources": [],
      "uncertainties_conflicts": []
    }}
  }},
  "sources": [],
  "research_status": {{"status":"complete|partial","checked_areas":[],"sources_found":[],"open_fields":[{{"car_key":"car_1","field":"","missing_source_type":"","why_open":""}}]}}
}}
Rules: Include all selected car_N slots. Unknown visible analytical fields must be null/[] and explained in `research_status.open_fields`; never use generic filler as normal content. No invented strings. Return source URLs for analytical claims.
""".strip()


def build_compare_writer_prompt(
    cars_selected_slots: Dict,
    computed_result: Dict,
    grounded_output: Dict,
    buyer_profile: Optional[Dict[str, Any]] = None,
) -> str:
    """Build Stage B prompt for a decision-based practical report."""

    def _truncate_text(value: Any, max_chars: int = 500) -> str:
        raw = str(value or "").strip()
        return raw[:max_chars]

    def _evidence_snapshot(slot_key: str) -> Dict[str, Any]:
        grounded_car = ((grounded_output or {}).get("cars", {}) or {}).get(slot_key, {})
        return {
            "car_profile": grounded_car.get("car_profile") or {},
            "legacy_labels": {
                "reliability": grounded_car.get("reliability") or {},
                "ownership_cost": grounded_car.get("ownership_cost") or {},
                "comfort_practicality": grounded_car.get("comfort_practicality") or {},
                "performance_driving": grounded_car.get("performance_driving") or {},
            },
            "notes": (grounded_car.get("short_notes") or [])[:3],
            "facts": grounded_car.get("facts") or {},
            "sources": (grounded_car.get("sources") or [])[:3],
        }

    slot_keys = ordered_compare_slot_keys(
        cars_selected_slots,
        (computed_result.get("cars") or {})
        if isinstance(computed_result, dict)
        else {},
        ((grounded_output or {}).get("cars") or {})
        if isinstance(grounded_output, dict)
        else {},
    )
    allowed_labels = slot_keys + ["tie", "depends", "unknown"]
    per_slot_schema_lines = []
    for slot_key in slot_keys:
        per_slot_schema_lines.append(f'    "choose_{slot_key}_if": ["string"],')
        per_slot_schema_lines.append(f'    "avoid_or_check_{slot_key}_if": ["string"],')
    key_difference_fields = ",".join(f'"{slot_key}":"string"' for slot_key in slot_keys)
    deterministic_preferences = {
        "overall": normalize_compare_writer_winner(
            computed_result.get("overall_winner"), slot_keys
        )
        or "depends",
        "legacy_category_winners": {
            category_key: normalize_compare_writer_winner(winner, slot_keys)
            or "depends"
            for category_key, winner in (
                (computed_result.get("category_winners") or {}).items()
            )
        },
        "balanced_comparison": bool(
            ((computed_result.get("comparison_status") or {}).get("balanced", True))
        ),
    }
    checked_version_seed = build_checked_versions(cars_selected_slots, grounded_output)
    model_payload = {
        "cars": {
            slot_key: {
                "label": _truncate_text(
                    ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                        "display_name"
                    )
                    or slot_key,
                    120,
                ),
                "user_selection": {
                    "make": _truncate_text(
                        ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                            "make"
                        ),
                        80,
                    ),
                    "model": _truncate_text(
                        ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                            "model"
                        ),
                        80,
                    ),
                    "year": _truncate_text(
                        ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                            "year"
                        )
                        or ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                            "year_start"
                        ),
                        40,
                    ),
                    "engine_type": _truncate_text(
                        ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                            "engine_type"
                        ),
                        80,
                    ),
                    "transmission": _truncate_text(
                        ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                            "gearbox"
                        ),
                        80,
                    ),
                },
                "checked_version_seed": checked_version_seed.get(slot_key) or {},
                "evidence": _evidence_snapshot(slot_key),
            }
            for slot_key in slot_keys
        },
        "decision_categories": [
            {"category_key": key, "category_name_he": name}
            for key, name in DECISION_CATEGORY_DEFINITIONS
        ],
        "deterministic_preference_hints": deterministic_preferences,
        "buyer_profile": buyer_profile,
        "buyer_profile_rule": "User preference context only; use it only to explain fit. It must not override factual vehicle data.",
        "sources": ((grounded_output or {}).get("sources") or [])[
            : _MAX_STAGE_A_SOURCES * max(1, len(slot_keys))
        ],
    }

    payload_json = json.dumps(model_payload, ensure_ascii=False, separators=(",", ":"))
    prompt = f"""You are a neutral Israeli-market car comparison writer.
Write simple, practical Hebrew for decision support. Use only MODEL_PAYLOAD below and do not invent facts.
MODEL_PAYLOAD contains grounded per-car facts and user preference context, if supplied.

MODEL_PAYLOAD:
{payload_json}

Return ONLY valid JSON with EXACTLY this top-level schema:
{{
  "decision_result": {{
    "overall_decision": {{"label":"{
        "|".join(allowed_labels)
    }","text":"Hebrew practical decision summary without scores"}},
{chr(10).join(per_slot_schema_lines)}
    "category_decisions": [
      {{"category_key":"pricing_and_value","category_name_he":"מחיר ותמורה","preferred":"{
        "|".join(allowed_labels)
    }","why":"string","important_caveat":"string|null"}}
    ],
    "key_differences": [{{"title":"string",{
        key_difference_fields
    },"meaning_for_buyer":"string"}}],
    "competitors_to_consider": [{{"model":"string","why_consider":"string","confidence":"high|medium|low"}}],
    "practical_summary":"Hebrew practical paragraph. Neutral. No first person. No direct buy/don't-buy command."
  }},
  "checked_versions": {{
    {
        ",".join(
            f'"{slot_key}":{{"make":"string","model":"string","year":"string","trim":"string","engine_type":"string","transmission":"string","drivetrain":"string","seats":"string","data_basis":"user_input|verified_source|ai_inference|mixed","confidence":"high|medium|low|unverified","notes":"string"}}'
            for slot_key in slot_keys
        )
    }
  }},
  "sources": ["url"]
}}

HARD RULES:
1. Do not output /100, /10, winnerScore, overall_score, category_score, category weights, "ציון", or "ניקוד".
2. Do not say "המנצח", winner, or best. Use only soft decision language: "עדיפות קלה", "תלוי שימוש", "אין הכרעה חד משמעית", "דורש בדיקה נוספת".
3. Do not use first person. Do not say "אני ממליץ", "הייתי קונה", "תקנה", or "אל תקנה".
4. No direct purchase advice and no "הרכב הטוב ביותר".
5. Google-grounded factual claims must keep source URLs. If official safety/prices/trims/fees/recalls/warranty are unavailable, use null plus an explicit missing source type; do not turn it into successful-card filler.
6. Fill exactly the 9 decision_categories from MODEL_PAYLOAD: pricing_and_value, trim_and_equipment, license_fee_and_running_cost, fuel_consumption, official_safety, powertrain_and_performance, reliability_and_risk, family_daily_use, resale_and_market_confidence. Use preferred="unknown" or "depends" when evidence is insufficient.
7. buyer_profile is preference context only; it may affect fit explanation only and never overrides car facts.
8. For EVERY selected car, `choose_car_X_if` and `avoid_or_check_car_X_if` must contain 1-3 non-empty Hebrew strings whenever MODEL_PAYLOAD includes any usable evidence for that car.
9. Never return [] for per-car arrays if `overall_decision`, `category_decisions`, `key_differences`, or the evidence snapshot can support cautious partial-research wording.
10. If evidence is thin, write cautious guidance about fit, trade-offs, and what to verify before purchase instead of leaving arrays empty.
11. `checked_versions` is mandatory for every selected car. It must clearly state the representative version being discussed, including uncertainty when trim, transmission, engine, or year are not fully verified.
12. In `checked_versions.transmission`, use general labels only: אוטומטית, רובוטית, רציפה, ידנית, או null עם הערת אימות. Do not use DSG, DCT, DHT, or CVT as the visible default transmission label.
13. If the user selected a general engine/transmission value, do not present it as a fully verified exact specification. Use `notes` to explain that it still requires verification against the importer spec or vehicle license.
14. CRITICAL — transmission/engine/year consistency: If the user selected a transmission type (e.g. automatic), you MUST NOT output a contradictory value (e.g. manual) in `checked_versions`. If you lack certainty, keep the user-selected general label when available or set null and explain the missing official source in `notes`. Silently flipping automatic to manual (or vice versa) is a critical error.
15. CRITICAL — required fields must never be empty: Every `checked_versions` slot must have non-empty values for `trim`, `engine_type`, `transmission`, `drivetrain`, `seats`, `year`, and `notes`. Use null/explicit verification notes for fields you cannot verify; never invent a visible generic placeholder.
16. CRITICAL — decision text fields must never be empty: `overall_decision.text`, every `category_decisions[].why`, every `choose_car_X_if`, every `avoid_or_check_car_X_if`, and `checked_versions.notes` must always contain non-empty Hebrew text. Use cautious partial-research wording rather than inventing facts.
"""
    return prompt


def build_compare_writer_retry_prompt(
    cars_selected_slots: Dict, computed_result: Dict
) -> str:
    """Build a minimal retry prompt for summary+winner only."""
    slot_keys = ordered_compare_slot_keys(
        cars_selected_slots,
        (computed_result.get("cars") or {})
        if isinstance(computed_result, dict)
        else {},
    )
    allowed_winners = slot_keys + ["tie"]
    retry_payload = {
        "cars": {
            slot_key: {
                "label": (
                    (cars_selected_slots.get(slot_key, {}) or {}).get(
                        "display_name", slot_key
                    )
                ),
            }
            for slot_key in slot_keys
        },
        "overall_winner": computed_result.get("overall_winner"),
        "overall_scores": {
            slot_key: (
                (computed_result.get("cars", {}).get(slot_key, {}) or {}).get(
                    "overall_score"
                )
            )
            for slot_key in slot_keys
        },
    }
    prompt = f"""RETRY_MODE_SUMMARY_ONLY
Return ONLY JSON:
{{
  "summary": "max 20 words",
  "winner": "{"|".join(allowed_winners)}",
  "categories": [],
  "caveats": []
}}
Do not add extra keys. Do not add categories.
DATA:{json.dumps(retry_payload, ensure_ascii=False, separators=(",", ":"))}
"""
    return prompt[: min(COMPARE_WRITER_PROMPT_CHAR_CAP, 4000)]
