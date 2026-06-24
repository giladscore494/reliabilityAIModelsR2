# -*- coding: utf-8 -*-
"""Reliability prompt builders (catalog-first review)."""

import json

from app.services.vehicle_catalog_service import (
    build_vehicle_catalog_context,
    resolve_vehicle_selection,
)
from app.utils.prompt_defense import (
    create_data_only_instruction,
    escape_prompt_input,
    wrap_user_input_in_boundary,
)


# Identity fields the model is NOT allowed to decide; they are owned by the
# catalog resolver and locked into the prompt.
LOCKED_IDENTITY_FIELDS = (
    "make",
    "model",
    "canonical_model",
    "version_or_trim",
    "body_type",
    "fuel_type",
    "engine",
    "engine_displacement_l",
    "horsepower_hp",
    "transmission",
    "drivetrain",
    "year_start",
    "year_end",
    "support_level",
)


def _build_locked_identity_block(resolution: dict) -> str:
    """Render a compact, injection-safe locked-identity JSON block."""
    locked = {k: resolution.get(k) for k in LOCKED_IDENTITY_FIELDS}
    locked["selected_year"] = resolution.get("selected_year")
    locked["variant_id"] = resolution.get("variant_id")
    locked["resolution_status"] = resolution.get("resolution_status")
    return json.dumps(locked, ensure_ascii=False, separators=(",", ":"))


def build_reliability_report_prompt(payload: dict, missing_info: list[str]) -> str:
    """Prompt for the strict reliability report JSON schema."""
    safe_make = escape_prompt_input(payload.get("make"), max_length=120)
    safe_model = escape_prompt_input(payload.get("model"), max_length=120)
    safe_sub_model = escape_prompt_input(payload.get("sub_model"), max_length=120)
    safe_year = escape_prompt_input(payload.get("year"), max_length=10)
    safe_mileage = escape_prompt_input(payload.get("mileage_range") or payload.get("mileage_km"), max_length=50)
    safe_fuel = escape_prompt_input(payload.get("fuel_type"), max_length=50)
    safe_trans = escape_prompt_input(payload.get("transmission"), max_length=50)
    safe_budget = escape_prompt_input(payload.get("budget") or payload.get("budget_max"), max_length=30)
    safe_owner_hist = escape_prompt_input(payload.get("ownership_history"), max_length=200)
    safe_usage_city = escape_prompt_input(payload.get("usage_city_pct"), max_length=20)

    user_data = f"""יצרן: {safe_make}
דגם: {safe_model}
תת-דגם: {safe_sub_model or 'לא צוין'}
שנה: {safe_year}
קילומטראז׳: {safe_mileage or 'לא צוין'}
דלק: {safe_fuel or 'לא צוין'}
גיר: {safe_trans or 'לא צוין'}
תקציב: {safe_budget or 'לא צוין'}
היסטוריית בעלויות: {safe_owner_hist or 'לא צוין'}
שימוש עירוני באחוזים: {safe_usage_city or 'לא צוין'}"""

    bounded_user_data = wrap_user_input_in_boundary(user_data)
    data_instruction = create_data_only_instruction()
    missing_block = ", ".join(missing_info) if missing_info else "אין"

    # final_line is intentionally fixed in English because downstream UX/tests
    # require that exact sentence unchanged.
    return f"""
    {data_instruction}

    אתה עוזר ניתוח סיכונים לרכב בישראל. תפקידך אינו להמליץ אם לקנות את הרכב, לא לתת ציון סופי, ולא לנסח משפט החלטה.
    החזר JSON תקני בלבד (ללא טקסט חופשי, ללא Markdown) עם המפתחות המדויקים:
    {{
      "based_on_available_information": "1-2 משפטים ניטרליים שמדגישים שהניתוח מוגבל ומבוסס על מידע חלקי/ציבורי/כללי בלבד",
      "key_risk_areas_to_examine": [
        {{"risk_area": "", "why_to_check": ""}}
      ],
      "what_must_be_checked_before_a_decision": {{
        "mechanical_inspection_points": ["נקודות בדיקה מכניות"],
        "documents_to_verify": ["מסמכים לאימות"],
        "questions_to_ask_seller": ["שאלות למוכר"],
        "red_flags_to_look_for": ["דגלים אדומים"]
      }},
      "known_uncertainties": ["מה לא ידוע או חסר"],
      "estimated_cost_sensitivity": ["טווחי עלות בלבד, אם רלוונטי"],
      "final_line": "This information highlights areas to verify and is not a substitute for a professional inspection."
    }}

    חוקים:
    - עברית בלבד, טון ניטרלי, אנליטי ולא שיווקי.
    - אל תנחש מידע חסר; פרט אותו ב-known_uncertainties.
    - אל תיתן verdict, ציון, החלטת קנייה, "next step" החלטי, או משפט מסכם שיפוטי.
    - אל תשתמש במילים/ביטויים: "recommended", "good choice", "bad choice", "reliable", "worth it".
    - אל תציג כעובדה ודאית מצב מכני, הזנחה, היסטוריית טיפולים חסרה, או recall שלא טופל בלי ראיה מפורשת מהמשתמש.
    - כל סיכון צריך להיות מוצג כמשהו לבדיקה/אימות, לא כעובדה ודאית על הרכב הספציפי.
    - estimated_cost_sensitivity חייב להכיל רק טווחים/שונות אפשרית, לא מספר בודד ולא הבטחת עלות.
    - final_line חייב להיות בדיוק המשפט האנגלי שסופק בסכימה.

    נתוני הקלט:
    {bounded_user_data}

    Missing info שנמסר לך: {missing_block}
    """.strip()


def build_combined_prompt(payload: dict, missing_info: list[str], resolution: dict | None = None) -> str:
    """Single catalog-first prompt for the reliability review.

    The catalog resolver owns vehicle identity; the model only researches
    dynamic/external fields with mandatory Google Search. ``resolution`` is the
    output of :func:`resolve_vehicle_selection`; it is resolved internally when
    not supplied (keeps backwards-compatible call sites working).
    """
    if resolution is None:
        resolution = resolve_vehicle_selection(payload)

    safe_make = escape_prompt_input(payload.get("make"), max_length=120)
    safe_model = escape_prompt_input(payload.get("model"), max_length=120)
    safe_year = escape_prompt_input(payload.get("year"), max_length=10)
    safe_mileage = escape_prompt_input(payload.get("mileage_range") or payload.get("mileage_km"), max_length=50)
    user_context = {
        "form_make": safe_make,
        "form_model": safe_model,
        "form_year": safe_year,
        "mileage_range": safe_mileage or "לא צוין",
        "missing_info": missing_info or [],
    }
    bounded_user_data = wrap_user_input_in_boundary(
        json.dumps(user_context, ensure_ascii=False), boundary_tag="user_context"
    )
    data_instruction = create_data_only_instruction()
    locked_identity = _build_locked_identity_block(resolution)
    status = resolution.get("resolution_status") or "unmatched"
    if status in ("exact", "inferred"):
        identity_rule = (
            "הזהות הטכנית נעולה מהקטלוג. אסור לשנות אף שדה זהות. אם מקור web סותר — "
            "דווח זאת ב-research_status בלבד, אל תכתוב זהות חלופית."
        )
    else:
        identity_rule = (
            "אין התאמת קטלוג חד-משמעית (unmatched). מותר לתאר אי-ודאות זהות, אך אל "
            "תמציא מנוע/גיר/הנעה/שנה כעובדה; סמן כל הנחה כלא-מאומתת."
        )

    return f"""
{data_instruction}

SYSTEM/ROLE:
אתה עוזר מחקר אמינות לרכבי יד-שנייה בישראל. אינך מחליט על זהות הרכב — היא נקבעת מראש מהקטלוג.

LOCKED_CATALOG_IDENTITY:
{locked_identity}

USER_CONTEXT:
{bounded_user_data}

TASK:
השתמש ב-Google Search כדי לחקור אך ורק שדות דינמיים/חיצוניים/משתנים. בצע כמה חיפושים ממוקדים (לא שאילתה כללית אחת):
- תקלות נפוצות וסיכוני אמינות
- recalls / קריאות שירות / ליקויי בטיחות
- רגישות ועומס עלויות אחזקה
- צריכת דלק/אנרגיה אמיתית
- מחירון והיצע יד-שנייה בישראל
- בטיחות רשמית
- אחריות/יבואן רשמי
- צ'ק-ליסט בדיקה לקונה ומתחרים רלוונטיים בישראל

STRICT RULES:
- {identity_rule}
- אל תשנה את שדות הזהות הנעולים: make/model/canonical_model/version_or_trim/body_type/fuel_type/engine/engine_displacement_l/horsepower_hp/transmission/drivetrain/year_start/year_end/support_level.
- החזר JSON תקני בלבד, ללא Markdown וללא טקסט חופשי מסביב.
- "unknown" עדיף על ניחוש. אם אין מקור — ציין סוג מקור חסר ב-research_status.open_fields.
- אסור ציון אמינות מספרי כלשהו (אין /100, /10, אחוזים, "ציון", "ניקוד").
- אסור verdict קנייה ("מומלץ לקנות", "כדאי", "אל תקנה", "הרכב הטוב ביותר").
- עברית מעשית וברורה. הימנע מוודאות משפטית/פיננסית. המלץ על בדיקה מקצועית במוסך כשרלוונטי.
- מתחרים: 3–5 חלופות קומפקטיות רק בתוך market_context.competitors.
- final_line חייב להיות בדיוק המשפט האנגלי שבסכימה.

החזר אובייקט JSON יחיד במבנה זה (ערכי הזהות יוחלפו בשרת מהקטלוג — אל תסתמך עליהם להחלטה):
{{
  "ok": true,
  "search_queries": ["שאילתות שבוצעו בפועל"],
  "sources": [{{"title":"","url":"","publisher":"","used_for":"recall|fault|market|safety|warranty|fuel|other"}}],
  "research_status": {{"limitations": [], "open_fields": [{{"field":"","missing_source_type":"","why_open":""}}]}},
  "overview": {{
    "based_on_available_information": "",
    "plain_summary": "",
    "best_for": [],
    "less_suitable_for": [],
    "confidence": "high|medium|low"
  }},
  "risk_analysis": {{
    "overall_risk_level": "low|medium|high|unknown",
    "top_risks": [{{"risk_area":"","why_to_check":"","sources":[""]}}],
    "systemic_issues": [{{"issue":"","severity":"low|medium|high|unknown","frequency_signal":"low|medium|high|unknown","what_to_check":"","source_refs":[""]}}],
    "recalls_or_service_campaigns": [{{"description":"","severity":"low|medium|high|unknown","source":""}}],
    "known_uncertainties": []
  }},
  "ownership_cost": {{
    "maintenance_cost_pressure": "low|medium|high|unknown",
    "expensive_items_to_check": [],
    "fuel_or_energy_notes": [],
    "insurance_or_tax_notes_if_found": [],
    "cost_confidence": "high|medium|low"
  }},
  "market_context": {{
    "israel_used_market_notes": [],
    "price_supply_signal": "strong|average|weak|unknown",
    "resale_notes": [],
    "warranty_israel": {{"vehicle_warranty": null, "battery_warranty": null, "notes": [], "sources": []}},
    "official_safety": {{"rating": null, "organization": null, "test_year": null, "notes": [], "sources": []}},
    "pricing_israel": {{"used_price_range_ils": null, "new_price_range_ils": null, "notes": [], "sources": []}},
    "trims_israel": [],
    "competitors": [{{"model_name":"","why_relevant":"","better_for":"","confidence":"high|medium|low","sources":[]}}]
  }},
  "buyer_checklist": {{
    "mechanical_inspection_points": [],
    "paperwork_checks": [],
    "test_drive_checks": [],
    "questions_to_ask_seller": []
  }},
  "final_line": "This information highlights areas to verify and is not a substitute for a professional inspection."
}}
""".strip()
