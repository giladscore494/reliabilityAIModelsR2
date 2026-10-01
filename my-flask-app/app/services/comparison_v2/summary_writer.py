# -*- coding: utf-8 -*-
"""Final human-readable summary: ONE ungrounded Gemini call after composition.

Gemini only explains the immutable composed result (outcome, the user's
preferences that mattered, the strongest deterministic reasons and missing
evidence). Its output is validated in code; if it restates a different
outcome, adds numbers that are not in the payload, states percentages, uses
absolute wording or exceeds the size contract, the output is discarded and a
deterministic Hebrew template is used instead. There is no "repair" call.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from app.services.comparison_v2.contracts import CHOICE_INSUFFICIENT, CHOICE_TIE, DECISION_UNAVAILABLE

logger = logging.getLogger("comparison_v2")

FORBIDDEN_PHRASES = (
    "ללא ספק",
    "בוודאות",
    "באופן מוחלט",
    "ציון",
    "/100",
    "הטוב ביותר",
    "מנצח",
    "http",
    "www.",
)
MAX_SUMMARY_CHARS = 900
MIN_SENTENCES = 2
MAX_SENTENCES = 5

SUMMARY_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "stated_outcome": {"type": "string"},
        "summary_he": {"type": "string"},
    },
    "required": ["stated_outcome", "summary_he"],
}


def summary_model_id() -> str:
    from app.services.comparison.model_config import comparison_summary_model_id

    return comparison_summary_model_id()


def build_summary_payload(
    cars: Dict[str, Dict[str, Any]],
    recommendation: Dict[str, Any],
    reasons: Dict[str, List[str]],
    cards: Dict[str, Dict[str, Any]],
    constraint_notes: List[Dict[str, str]],
    profile_summary: Dict[str, Any],
    limitations: List[str],
) -> Dict[str, Any]:
    """The IMMUTABLE composed result. Everything Gemini may say comes from here."""
    outcome = recommendation["outcome"]
    return {
        "cars": {slot: {"name": c.get("display_name"), "year": c.get("year"), "trim": c.get("trim")} for slot, c in cars.items()},
        "outcome": outcome,
        "recommended_name": cars.get(outcome, {}).get("display_name") if outcome in cars else None,
        "headline": recommendation.get("title_he"),
        "basis": recommendation.get("basis"),
        "strength": recommendation.get("strength_label_he"),
        "evidence_coverage": recommendation.get("evidence_coverage_label_he"),
        "buyer_profile": {
            "headline": profile_summary.get("headline") or [],
            "priorities": [f"{p['name_he']}: {p['label_he']}" for p in profile_summary.get("priorities") or []],
        },
        "reasons_for": reasons.get("for") or [],
        "reasons_against": reasons.get("against") or [],
        "requirements": [n["text_he"] for n in constraint_notes],
        "categories": [
            {"category": c["label_he"], "influence": c["layers"]["influence"]}
            for c in cards.values()
            if c["influence_status"] not in ("not_applicable",)
        ],
        "limitations": limitations,
    }


def build_summary_prompt(payload: Dict[str, Any]) -> str:
    return "\n".join(
        [
            "כתוב סיכום קצר בעברית (2 עד 5 משפטים) להשוואת רכבים, על בסיס ה-JSON בלבד.",
            "ההכרעה כבר חושבה בקוד ואינה ניתנת לשינוי. תפקידך רק להסביר אותה.",
            "כללים:",
            "- אל תשנה את התוצאה (outcome) ואל תבחר רכב אחר. אל תחשב מחדש שום דבר.",
            "- הסבר אילו העדפות של המשתמש השפיעו, את הסיבות העובדתיות החזקות (reasons_for) ומידע חסר חשוב.",
            "- אל תוסיף עובדות, מספרים, העדפות או מידע שאינם מופיעים ב-JSON, ואל תשתמש בידע שלך על הרכבים.",
            "- נתון חסר אינו חיסרון — אל תציג אותו כחיסרון.",
            "- אם outcome הוא tie — כתוב שאין כרגע יתרון משמעותי. אם insufficient_evidence — שאין מספיק מידע מאומת להכרעה. אם no_vehicle_meets_requirements — שאף רכב אינו עומד בכל דרישות החובה.",
            "- אל תכתוב אחוזים או הסתברות שההמלצה נכונה. אל תשתמש במילים מוחלטות (כמו 'ללא ספק', 'בוודאות'), אל תכתוב 'ציון' ואל תכתוב 'מנצח'.",
            "- כאשר יש רכב מומלץ, נסח: 'לפי הנתונים הזמינים והעדיפויות שהגדרת, X מתאים יותר.'",
            "- החזר JSON עם stated_outcome (העתק מדויק של outcome) ו-summary_he.",
            "",
            json.dumps(payload, ensure_ascii=False),
        ]
    )


def _allowed_numbers(payload: Dict[str, Any]) -> set:
    text = json.dumps(payload, ensure_ascii=False)
    nums = set(re.findall(r"\d+(?:[.,]\d+)*", text))
    nums |= {n.replace(",", "") for n in nums}
    nums |= {"1", "2", "3", "100"}  # "רכב 1/2/3", "0-100"
    return nums


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


WINNER_MARKERS = ("מתאים יותר", "יתרון כולל", "היתרון הכולל", "עדיפות כוללת")


def validate_summary(output: Any, payload: Dict[str, Any], cars: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Return the validated summary text, or None if it must be discarded."""
    if not isinstance(output, dict):
        return None
    stated = output.get("stated_outcome")
    text = output.get("summary_he")
    outcome = payload["outcome"]
    if stated != outcome or not isinstance(text, str):
        return None
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) > MAX_SUMMARY_CHARS:
        return None
    sentences = _sentences(text)
    if not (MIN_SENTENCES <= len(sentences) <= MAX_SENTENCES):
        return None
    lowered = text.lower()
    if any(p.lower() in lowered for p in FORBIDDEN_PHRASES) or "%" in text:
        return None
    allowed = _allowed_numbers(payload)
    for num in re.findall(r"\d+(?:[.,]\d+)*", text):
        if num not in allowed and num.replace(",", "") not in allowed:
            return None
    names = {slot: (c.get("display_name") or "") for slot, c in cars.items()}
    for sentence in sentences:
        if any(m in sentence for m in WINNER_MARKERS):
            if outcome not in names:
                if not re.search(r"(אין|לא)\s", sentence):
                    return None
                continue
            others = [n for s, n in names.items() if s != outcome and n]
            if names[outcome] not in sentence or any(o in sentence for o in others):
                return None
    return text


def deterministic_summary(payload: Dict[str, Any]) -> str:
    """Hebrew template used when Gemini fails or is rejected."""
    lines: List[str] = []
    outcome = payload["outcome"]
    name = payload.get("recommended_name")
    if name:
        if payload.get("basis") == "hard_constraints":
            lines.append(f"{name} הוא הרכב היחיד שעומד בדרישות החובה שהגדרת.")
        else:
            lines.append(f"לפי הנתונים הזמינים והעדיפויות שהגדרת, {name} מתאים יותר.")
        lines.extend(payload.get("reasons_for", [])[:2])
    elif outcome == CHOICE_TIE:
        lines.append("לפי הנתונים הזמינים והעדיפויות שהגדרת, אין כרגע יתרון משמעותי לאחד הרכבים.")
    elif outcome == CHOICE_INSUFFICIENT:
        lines.append("אין כרגע מספיק מידע מאומת להכרעה מותאמת.")
    elif outcome == "no_vehicle_meets_requirements":
        lines.append("אף אחד מהרכבים אינו עומד בכל דרישות החובה שהגדרת.")
    else:
        lines.append("ההכרעה המותאמת אינה זמינה כרגע, ולכן מוצגת השוואת נתונים בלבד.")
    fails = [r for r in payload.get("requirements", []) if "אינו עומד" in r]
    if fails and not name:
        lines.append(fails[0])
    lines.append("ההשוואה מבוססת על הנתונים המאומתים הזמינים בלבד; נתון חסר אינו נחשב לחיסרון.")
    return " ".join(lines[:5])


class GeminiSummaryWriter:
    def __init__(self, client: Any, model_id: Optional[str] = None, timeout_sec: Optional[int] = None):
        self.client = client
        self.model_id = model_id or summary_model_id()
        self.timeout_sec = timeout_sec or int(os.environ.get("COMPARISON_SUMMARY_TIMEOUT_SEC", "25"))
        self.calls = 0

    def _config(self):
        from google.genai import types as genai_types

        return genai_types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=700,
            response_mime_type="application/json",
            response_json_schema=SUMMARY_RESPONSE_SCHEMA,
            thinking_config=genai_types.ThinkingConfig(thinking_level=genai_types.ThinkingLevel.LOW),
            http_options=genai_types.HttpOptions(
                timeout=int(self.timeout_sec * 1000),
                retry_options=genai_types.HttpRetryOptions(attempts=1),
            ),
        )

    def write(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.client is None:
            return {"output": None, "error_code": "CLIENT_NOT_INITIALIZED", "duration_ms": 0}
        self.calls += 1
        started = time.perf_counter()
        try:
            resp = self.client.models.generate_content(model=self.model_id, contents=build_summary_prompt(payload), config=self._config())
            output = resp.parsed if isinstance(getattr(resp, "parsed", None), dict) else json.loads(getattr(resp, "text", None) or "")
        except Exception as exc:
            return {"output": None, "error_code": f"SUMMARY_ERROR:{type(exc).__name__}", "duration_ms": int((time.perf_counter() - started) * 1000)}
        return {"output": output, "error_code": None, "duration_ms": int((time.perf_counter() - started) * 1000)}


def produce_summary(writer: Optional[GeminiSummaryWriter], cars: Dict[str, Dict[str, Any]], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Exactly one Gemini call on the immutable composed result (or none)."""
    fallback = deterministic_summary(payload)
    if writer is None or payload["outcome"] == DECISION_UNAVAILABLE:
        # Without a composed decision there is nothing for Gemini to explain.
        return {"text": fallback, "source": "deterministic_fallback", "reason": "no_writer" if writer is None else "decision_unavailable"}
    result = writer.write(payload)
    if result.get("error_code"):
        logger.warning("comparison_v2 summary_failed error=%s", result["error_code"])
        return {"text": fallback, "source": "deterministic_fallback", "reason": result["error_code"], "duration_ms": result.get("duration_ms")}
    text = validate_summary(result.get("output"), payload, cars)
    if not text:
        logger.warning("comparison_v2 summary_failed error=SUMMARY_REJECTED")
        return {"text": fallback, "source": "deterministic_fallback", "reason": "SUMMARY_REJECTED", "duration_ms": result.get("duration_ms")}
    logger.info("comparison_v2 summary_completed model=%s duration_ms=%s", writer.model_id, result.get("duration_ms"))
    return {"text": text, "source": "gemini", "reason": None, "model": writer.model_id, "duration_ms": result.get("duration_ms")}
