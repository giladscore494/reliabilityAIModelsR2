# -*- coding: utf-8 -*-
"""Reliability prompt builders."""

from app.services.vehicle_catalog_service import build_vehicle_catalog_context
from app.utils.prompt_defense import (
    create_data_only_instruction,
    escape_prompt_input,
    wrap_user_input_in_boundary,
)


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


def build_combined_prompt(payload: dict, missing_info: list[str]) -> str:
    """Single catalog-first prompt for analyze + reliability review."""
    safe_make = escape_prompt_input(payload.get("make"), max_length=120)
    safe_model = escape_prompt_input(payload.get("model"), max_length=120)
    safe_year = escape_prompt_input(payload.get("year"), max_length=10)
    safe_mileage = escape_prompt_input(payload.get("mileage_range") or payload.get("mileage_km"), max_length=50)
    safe_fuel = escape_prompt_input(payload.get("fuel_type"), max_length=50)
    safe_trans = escape_prompt_input(payload.get("transmission"), max_length=50)
    user_data = f"""יצרן: {safe_make}
דגם: {safe_model}
שנה: {safe_year}
טווח קילומטראז׳: {safe_mileage or 'לא צוין'}
סוג דלק בטופס: {safe_fuel or 'לא צוין'}
תיבת הילוכים בטופס: {safe_trans or 'לא צוין'}"""
    bounded_user_data = wrap_user_input_in_boundary(user_data)
    data_instruction = create_data_only_instruction()
    missing_block = ", ".join(missing_info) if missing_info else "אין"
    catalog_block = build_vehicle_catalog_context(payload)["prompt_block"]

    return f"""
{data_instruction}

{catalog_block}

אתה אוסף ראיות ומנתח אמינות לרכב בישראל. אינך פותר זהות טכנית כאשר המאגר המקומי סיפק התאמה מדויקת.
חובה להשתמש ב-Google Search לכל טענה אנליטית: אמינות, תקלות, ריקולים, מחירים, רמות גימור, אגרה, בטיחות, אחריות, היצע יד שנייה, ביקורות ועלויות בעלות.

כללי זהות:
- match_type=exact: השתמש בזהות הטכנית מהקטלוג בלבד. אל תחליף make/model/canonical_model/year range/version/body/fuel/engine/hp/transmission/drivetrain/support_level לפי טקסט מהאינטרנט. אם מקור אינטרנט סותר — הוסף conflict.
- match_type=ambiguous: הקטלוג הוא מועמד. אמת בזהירות ודווח אי-ודאות/סתירה.
- match_type=unmatched: מותר לזהות דרך web, אך label identity_basis כ-web_resolved או unmatched והסבר אי-ודאות.

חוקי כתיבה:
- עברית בלבד, JSON תקני בלבד, בלי Markdown.
- אין verdict, ציון, purchase recommendation, "מומלץ לקנות", "כדאי לקנות", או שפה מוחלטת.
- אל תמציא מספרים. עלויות רק כטווחים או מספרים שמופיעים במקור.
- מתחרים: החזר 3–5 חלופות קומפקטיות רק בתוך market_context.competitors; לא לשכפל במקום אחר.
- final_line חייב להיות בדיוק המשפט האנגלי שבסכימה.

החזר אובייקט JSON יחיד במבנה זה:
{{
  "ok": true,
  "search_performed": true,
  "search_queries": ["שאילתות שבוצעו"],
  "sources": [{{"title":"","url":"","domain":""}}],
  "catalog_resolution": {{
    "match_type": "exact|ambiguous|unmatched",
    "identity_basis": "catalog_exact|catalog_ambiguous|web_resolved|unmatched",
    "conflicts": ["סתירות בין הקטלוג למקורות, אם יש"],
    "confidence": "high|medium|low"
  }},
  "identity_snapshot": {{
    "make": "", "model": "", "canonical_model": null, "selected_year": null,
    "version_or_trim": null, "body_type": null, "fuel_type": null, "engine": null,
    "engine_displacement_l": null, "horsepower_hp": null, "transmission": null,
    "drivetrain": null, "year_start": null, "year_end": null, "support_level": null,
    "profile_confidence": null, "source_summary": [], "missing_grounded_fields": [], "notes": []
  }},
  "overview": {{
    "based_on_available_information": "",
    "plain_summary": "",
    "decision_readiness": "מספיק לבדיקה ראשונית|דורש בדיקה נוספת|מידע חלש",
    "data_quality_label": "high|medium|low",
    "weakly_sourced": false
  }},
  "risk_analysis": {{
    "top_risks": [{{"risk_area":"","why_to_check":"","sources":[""]}}],
    "systemic_issues": [{{"system":"","issue":"","severity":"low|medium|high","evidence":"","sources":[""]}}],
    "recalls": [{{"system":"","description":"","severity":"low|medium|high","source":""}}],
    "known_uncertainties": []
  }},
  "buyer_checklist": {{
    "mechanical_inspection_points": [],
    "documents_to_verify": [],
    "questions_to_ask_seller": [],
    "red_flags_to_look_for": []
  }},
  "ownership_cost": {{
    "maintenance_cost_pressure": "low|medium|high|unknown",
    "cost_sensitivity_notes": [],
    "issue_cost_ranges": [{{"issue":"","cost_range_ils":"","source":"","severity":"נמוך|בינוני|גבוה"}}]
  }},
  "market_context": {{
    "pricing_israel": {{"used_price_range_ils": null, "new_price_range_ils": null, "notes": [], "sources": []}},
    "trims_israel": [],
    "official_safety": {{"rating": null, "organization": null, "test_year": null, "notes": [], "sources": []}},
    "warranty_israel": {{"vehicle_warranty": null, "battery_warranty": null, "notes": [], "sources": []}},
    "competitors": [{{
      "model_name": "",
      "why_relevant": "",
      "advantage_vs_reviewed_vehicle": "",
      "disadvantage_or_risk_vs_reviewed_vehicle": "",
      "better_for": "",
      "confidence": "high|medium|low",
      "sources": []
    }}]
  }},
  "final_line": "This information highlights areas to verify and is not a substitute for a professional inspection."
}}

Missing info שסיפק המשתמש: {missing_block}
נתוני הקלט:
{bounded_user_data}
""".strip()
