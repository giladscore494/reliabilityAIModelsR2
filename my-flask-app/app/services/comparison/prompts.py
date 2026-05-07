# -*- coding: utf-8 -*-
"""Prompt builders for comparison Stage A and Stage B calls."""

import json
import os
from typing import Any, Dict, List, Optional

from app.services.comparison.normalization import (
    _infer_compare_segment_details,
    _normalize_compare_writer_winner,
    _ordered_compare_slot_keys,
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


CATEGORY_LABELS_HE = {
    "reliability_risk": "אמינות וסיכונים",
    "ownership_cost": "עלות אחזקה",
    "practicality_comfort": "נוחות ופרקטיות",
    "driving_performance": "ביצועים ונהיגה",
}


_MAX_STAGE_A_SOURCES = 5


COMPARE_CATEGORY_NAMES = tuple(CATEGORY_LABELS_HE.keys())


DECISION_CATEGORY_DEFINITIONS = [
    ("pricing_and_value", "מחיר ותמורה"),
    ("trim_and_equipment", "רמות גימור ואבזור"),
    ("license_fee_and_running_cost", "אגרה ועלויות שוטפות"),
    ("fuel_consumption", "צריכת דלק/חשמל"),
    ("official_safety", "בטיחות רשמית"),
    ("powertrain_and_performance", "מכלולים וביצועים"),
    ("reliability_and_risk", "אמינות וסיכונים"),
    ("family_daily_use", "שימוש יומי ומשפחתי"),
    ("resale_and_market_confidence", "סחירות וירידת ערך"),
]


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
    """Build Stage A prompt for a SINGLE car using a compact evidence schema."""
    sanitized = {
        "make": escape_prompt_input(car.get("make", ""), max_length=50),
        "model": escape_prompt_input(car.get("model", ""), max_length=100),
    }
    if car.get("year"):
        sanitized["year"] = int(car["year"])
    if car.get("engine_type"):
        sanitized["engine_type"] = escape_prompt_input(
            car["engine_type"], max_length=50
        )
    if car.get("gearbox"):
        sanitized["gearbox"] = escape_prompt_input(car["gearbox"], max_length=50)

    car_json = json.dumps(sanitized, ensure_ascii=False)
    bounded_car = wrap_user_input_in_boundary(car_json, boundary_tag="car_input")
    data_instruction = create_data_only_instruction()
    segment_key, segment_signals = _infer_compare_segment_details(sanitized, {})
    segment_rule = COMPARE_SEGMENT_PROMPT_RULES.get(
        segment_key,
        COMPARE_SEGMENT_PROMPT_RULES["general_private_car"],
    )
    segment_context = {
        "segment_key": segment_key,
        "inference_signals": segment_signals,
        "focus_more": segment_rule.get("focus_more", []),
        "focus_less": segment_rule.get("focus_less", []),
        "special_note": segment_rule.get("special_note"),
    }
    category_behavior = {
        key: COMPARE_CATEGORY_BEHAVIOR_RULES[key] for key in COMPARE_CATEGORY_NAMES
    }

    return f"""{data_instruction}

You are acting as an experienced automotive analyst giving a first-impression assessment. Estimates based on your general knowledge, market reputation, and segment norms are valid and encouraged. You do NOT need official verified sources for every metric — provide your best reasoned assessment. Include source URLs when available, but omit them if you don't have one.

You are a car research extractor for ONE car.
Return ONLY compact evidence in JSON.
Return ONLY valid JSON. No markdown. No code fences. No prose outside JSON.

{bounded_car}

Region: {region}

SEGMENT_CONTEXT (deterministic; use this mission when assigning labels):
{json.dumps(segment_context, ensure_ascii=False)}

CATEGORY_BEHAVIOR_RULES:
{json.dumps(category_behavior, ensure_ascii=False)}

Return this exact JSON structure:
{{
  "car_name": "string",
  "reliability": {{
    "overall": "high"|"medium"|"low"|null,
    "issue_frequency": "low"|"medium"|"high"|null,
    "issue_severity": "low"|"medium"|"high"|null,
    "repair_cost_risk": "low"|"medium"|"high"|null,
    "recall_risk": "low"|"medium"|"high"|null,
    "parts_complexity": "low"|"medium"|"high"|null
  }},
  "ownership_cost": {{
    "fuel_cost": "low"|"medium"|"high"|null,
    "routine_maintenance": "low"|"medium"|"high"|null,
    "repair_burden": "low"|"medium"|"high"|null,
    "insurance_burden": "low"|"medium"|"high"|null,
    "depreciation_risk": "low"|"medium"|"high"|null
  }},
  "comfort_practicality": {{
    "space": "low"|"medium"|"high"|null,
    "ride_comfort": "low"|"medium"|"high"|null,
    "trunk_usefulness": "low"|"medium"|"high"|null,
    "daily_usability": "low"|"medium"|"high"|null
  }},
  "performance_driving": {{
    "power_feel": "low"|"medium"|"high"|null,
    "power_to_weight": "low"|"medium"|"high"|null,
    "braking_confidence": "low"|"medium"|"high"|null,
    "handling_agility": "low"|"medium"|"high"|null,
    "fun_to_drive": "low"|"medium"|"high"|null
  }},
  "facts": {{
    "horsepower": <number or null>,
    "weight_kg": <number or null>,
    "body_type": "string or null",
    "fuel_type": "string or null"
  }},
  "car_profile": {{
    "vehicle_identity": {{"make":"string","model":"string","year":"string|null","generation":"string|null","body_type":"string|null","segment":"string|null","israel_market_status":"sold_new|sold_used_only|parallel_import|discontinued_in_israel|unclear|null"}},
    "pricing_israel": {{"new_price_range_ils":"string|null","used_price_range_ils":"string|null","notes":["string"],"sources":["url"]}},
    "license_fee_israel": {{"annual_fee_ils": <number or null>, "method": "official|unknown", "notes": ["string"], "sources": ["url"]}},
    "trim_levels_israel": [{{"trim_name":"string","price_ils": <number or null>, "main_equipment":["string"],"powertrain":"string|null","safety_equipment":["string"],"source":"url|null"}}],
    "powertrain_specs": {{"engine":"string|null","gearbox":"string|null","drivetrain":"string|null","horsepower": <number or null>,"torque_nm": <number or null>,"battery_kwh": <number or null>,"ev_range_km": <number or null>,"zero_to_100_sec": <number or null>,"trunk_liters": <number or null>,"seats": <number or null>,"sources":["url"]}},
    "fuel_consumption": {{"official_value":"string|null","real_world_value":"string|null","method":"official|review_based|owner_reported|unknown","notes":["string"],"sources":["url"]}},
    "official_safety": {{"rating":"string|null","organization":"Euro NCAP|IIHS|NHTSA|ANCAP|Israeli Ministry/Importer|unknown|null","test_year": <number or null>,"notes":["string"],"sources":["url"]}},
    "warranty_israel": {{"vehicle_warranty":"string|null","battery_warranty":"string|null","sources":["url"]}},
    "recalls": {{"known_recalls":[{{"year": <number or null>, "issue":"string", "source":"url|null"}}],"checked_against_official_source": <boolean>,"sources":["url"]}},
    "reliability_risks": ["string"],
    "ownership_cost_notes": {{"maintenance_cost_pressure":"low|medium|high|unknown","insurance_cost_pressure":"low|medium|high|unknown","depreciation_risk":"low|medium|high|unknown","parts_availability":"low|medium|high|unknown","notes":["string"]}},
    "best_for": ["string"],
    "not_ideal_for": ["string"],
    "sources": ["url"]
  }},
  "short_notes": ["up to 4 short bullets"],
  "sources": ["up to 5 source URLs"]
}}

RULES:
1. Google Search grounding is mandatory. Search Hebrew and English when useful.
2. Prefer official importer pages, Israeli Ministry of Transport, official safety organizations, official recall data, and reputable Israeli car sites.
3. Keep the legacy labels compact and stable, but also fill car_profile with sourced factual facts where available.
4. Do NOT compare against another car. Do NOT score numerically. Do NOT write long explanations.
5. Do not invent official safety ratings, Israeli trim levels, prices, license fees, recalls, specs, consumption, or warranty terms. Use null/unknown plus notes when unavailable.
6. license_fee_israel.method can only be official or unknown.
7. If exact official safety rating is not found, set rating=null, organization="unknown", and add an explanatory note.
8. Keep source URLs for factual claims about price, trims, license fee, safety, recalls, specs, fuel/energy consumption, and warranty.
9. Do not return visible numeric quality scores or score-like quality fields. Percentages are allowed only for factual specs or official safety sub-scores from official sources.
10. short_notes must contain at most 4 short items; sources must contain direct http/https URLs.
11. Segment-aware labels are required: judge the car against realistic expectations of its inferred mission, not one universal standard.
12. Return ONLY valid JSON.
""".strip()


def build_comparison_prompt(cars: List[Dict[str, str]]) -> str:
    """Build the comparison prompt for Gemini with strict JSON output."""

    # Sanitize car inputs including year, engine_type, and gearbox
    sanitized_cars = []
    for car in cars:
        sanitized_car = {
            "make": escape_prompt_input(car.get("make", ""), max_length=50),
            "model": escape_prompt_input(car.get("model", ""), max_length=100),
        }
        # Include explicit year if provided (single year, not a range)
        if car.get("year"):
            sanitized_car["year"] = int(car.get("year"))
        elif car.get("year_start"):
            sanitized_car["year_start"] = car.get("year_start")
            sanitized_car["year_end"] = car.get("year_end")
        # Include engine type and gearbox as explicit assumptions
        if car.get("engine_type"):
            sanitized_car["engine_type"] = escape_prompt_input(
                car.get("engine_type", ""), max_length=50
            )
        if car.get("gearbox"):
            sanitized_car["gearbox"] = escape_prompt_input(
                car.get("gearbox", ""), max_length=50
            )
        sanitized_cars.append(sanitized_car)

    cars_json = json.dumps(sanitized_cars, ensure_ascii=False, indent=2)
    bounded_cars = wrap_user_input_in_boundary(cars_json, boundary_tag="cars_input")
    data_instruction = create_data_only_instruction()

    # Build slot mapping for stable keys
    slot_mapping = {}
    segment_context = {}
    for i, car in enumerate(sanitized_cars):
        slot_key = f"car_{i + 1}"
        slot_mapping[slot_key] = build_display_name(car)
        segment_key, segment_signals = _infer_compare_segment_details(car, {})
        segment_rule = COMPARE_SEGMENT_PROMPT_RULES.get(
            segment_key,
            COMPARE_SEGMENT_PROMPT_RULES["general_private_car"],
        )
        segment_context[slot_key] = {
            "segment_key": segment_key,
            "inference_signals": segment_signals,
            "focus_more": segment_rule.get("focus_more", []),
            "focus_less": segment_rule.get("focus_less", []),
            "special_note": segment_rule.get("special_note"),
        }

    slot_mapping_text = "\n".join(f"  {k}: {v}" for k, v in slot_mapping.items())

    return f"""
{data_instruction}

You are a car comparison data analyst.

🔴 CRITICAL: You are a data retrieval agent ONLY. You MUST NOT:
- Decide winners or compare scores between cars
- Compute any scores or rankings
- Make recommendations or judgments
- State which car is "better" in any way

Your ONLY job is to retrieve factual data for each metric for each car.
Return ONLY JSON. No markdown. No code fences. No commentary.
Use double quotes only. Do not include trailing commas.
Do not output tables, repetition, or long paragraphs.

{bounded_cars}

IMPORTANT: Use these EXACT keys in the "cars" object:
{slot_mapping_text}

Return data for each car using the slot key (car_1, car_2, etc.) NOT the car name.

SEGMENT_CONTEXT_BY_SLOT (deterministic; use this mission when assigning labels):
{json.dumps(segment_context, ensure_ascii=False, indent=2)}

CATEGORY_BEHAVIOR_RULES:
{json.dumps(COMPARE_CATEGORY_BEHAVIOR_RULES, ensure_ascii=False, indent=2)}

Return a SINGLE JSON object with this EXACT structure:

{{
  "grounding_successful": true,
  "search_queries_used": ["list of actual search queries you ran"],
  "assumptions": {{
    "year_assumption": "If year range wasn't clear, state what years you assumed",
    "engine_assumption": "If specific engine wasn't given, state what you assumed",
    "trim_assumption": "If specific trim wasn't given, state what you assumed"
  }},
  "cars": {{
    "car_1": {{
      "reliability_risk": {{
        "reliability_rating": {{
          "value": 0-100 or null,
          "missing_reason": "reason if null",
          "sources": [
            {{"url": "https://...", "title": "Source title", "snippet": "Brief quote (max 25 words)"}}
          ]
        }},
        "major_failure_risk": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "common_failure_patterns": {{
          "value": [
            {{"issue": "Issue name", "frequency": "common/rare/occasional"}}
          ] or null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "mileage_sensitivity": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "maintenance_complexity": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "expected_maintenance_cost_level": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }}
      }},
      "ownership_cost": {{
        "fuel_economy_real_world": {{
          "value": <number in L/100km> or null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "insurance_cost_level": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "depreciation_value_retention": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "parts_availability": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "service_network_ease": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }}
      }},
      "practicality_comfort": {{
        "cabin_space": {{
          "value": "small" | "medium" | "large" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "trunk_space_liters": {{
          "value": <number in liters> or null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "ride_comfort": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "noise_insulation": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "city_driveability": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "features_value": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }}
      }},
      "driving_performance": {{
        "acceleration_0_100": {{
          "value": <number in seconds> or null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "engine_power_hp": {{
          "value": <number in hp> or null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "handling_stability": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "braking_performance": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "highway_stability": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }}
      }}
    }}
    "car_2": {{ ... }}
  }}
}}

RULES:
1. Use your best available knowledge to fill every metric. Official sources are preferred but NOT required — well-reasoned estimates based on general knowledge, segment norms, and known characteristics are acceptable and encouraged.
2. Only set value=null if you genuinely have no basis whatsoever to estimate. Prefer a low-confidence estimate over null.
3. Sources are optional. If you have a URL, include it. If not, you may omit the sources array or leave it empty — do NOT set value=null just because you lack a URL.
4. Segment-aware labels are required: judge each car against realistic expectations of its inferred mission, not one universal standard.
5. Keep the 4 main categories unchanged; only the sub-priority logic is segment-aware.
6. Do NOT compare cars or state winners - only provide raw data.
7. Return ONLY valid JSON. No markdown, no explanations.
8. Do not wrap the response in an array; return one object that starts with {{ and ends with }}.
9. If a required field is truly unknown, keep the key and use null instead of omitting it — but always try to estimate first.
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

    slot_keys = _ordered_compare_slot_keys(
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
        "overall": _normalize_compare_writer_winner(
            computed_result.get("overall_winner"), slot_keys
        )
        or "depends",
        "legacy_category_winners": {
            category_key: _normalize_compare_writer_winner(winner, slot_keys)
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
    "competitors_to_consider": [{{"model":"string","why_consider":"string"}}],
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
2. Do not say "המנצח". Prefer "הבחירה הסבירה יותר", "תלוי שימוש", "אין הכרעה חד משמעית", "עדיפות קלה", "דורש בדיקה נוספת".
3. Do not use first person. Do not say "אני ממליץ", "הייתי קונה", "תקנה", or "אל תקנה".
4. No direct purchase advice and no "הרכב הטוב ביותר".
5. Google-grounded factual claims must keep source URLs. If official safety/prices/trims/fees/recalls/warranty are unavailable, use null/unknown or an explicit caveat.
6. Fill all decision_categories from MODEL_PAYLOAD. Use preferred="unknown" or "depends" when evidence is insufficient.
7. buyer_profile is preference context only; it may affect fit explanation only and never overrides car facts.
8. For EVERY selected car, `choose_car_X_if` and `avoid_or_check_car_X_if` must contain 1-3 non-empty Hebrew strings whenever MODEL_PAYLOAD includes any usable evidence for that car.
9. Never return [] for per-car arrays if `overall_decision`, `category_decisions`, `key_differences`, or the evidence snapshot can support safe fallback wording.
10. If evidence is thin, write cautious guidance about fit, trade-offs, and what to verify before purchase instead of leaving arrays empty.
11. `checked_versions` is mandatory for every selected car. It must clearly state the representative version being discussed, including uncertainty when trim, transmission, engine, or year are not fully verified.
12. In `checked_versions.transmission`, use general labels only: אוטומטית, רובוטית, רציפה, ידנית, לא ידוע / לבדיקה. Do not use DSG, DCT, DHT, or CVT as the visible default transmission label.
13. If the user selected a general engine/transmission value, do not present it as a fully verified exact specification. Use `notes` to explain that it still requires verification against the importer spec or vehicle license.
14. CRITICAL — transmission/engine/year consistency: If the user selected a transmission type (e.g. automatic), you MUST NOT output a contradictory value (e.g. manual) in `checked_versions`. If you lack certainty, output `לא ידוע / לבדיקה` and explain in `notes`. Silently flipping automatic to manual (or vice versa) is a critical error.
15. CRITICAL — required fields must never be empty: Every `checked_versions` slot must have non-empty values for `trim`, `engine_type`, `transmission`, `drivetrain`, `seats`, `year`, and `notes`. Use `לא ידוע / לבדיקה` as a safe fallback for any field you cannot verify — never leave them blank.
16. CRITICAL — decision text fields must never be empty: `overall_decision.text`, every `category_decisions[].why`, every `choose_car_X_if`, every `avoid_or_check_car_X_if`, and `checked_versions.notes` must always contain non-empty Hebrew text. Use cautious fallback wording rather than returning empty strings or empty arrays.
"""
    return prompt


def build_compare_writer_retry_prompt(
    cars_selected_slots: Dict, computed_result: Dict
) -> str:
    """Build a minimal retry prompt for summary+winner only."""
    slot_keys = _ordered_compare_slot_keys(
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
