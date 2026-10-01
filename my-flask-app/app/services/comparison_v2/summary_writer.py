# -*- coding: utf-8 -*-
"""Final human-readable summary: ONE ungrounded Gemini call after JEV.

Gemini only explains the structured decision. Its output is validated in
code; if it restates a different overall choice, adds numbers that are not in
the payload, uses absolute wording or exceeds the size contract, the output is
discarded and a deterministic Hebrew template is used instead. There is no
second "repair" call.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from app.services.comparison_v2.contracts import (
    CHOICE_INSUFFICIENT,
    CHOICE_TIE,
    DECISION_UNAVAILABLE,
    NOT_APPLICABLE,
)
from app.services.comparison_v2.deterministic_engine import CATEGORY_LABELS_HE

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
        "stated_overall_choice": {"type": "string"},
        "summary_he": {"type": "string"},
    },
    "required": ["stated_overall_choice", "summary_he"],
}


def summary_model_id() -> str:
    from app.services.comparison.model_config import comparison_summary_model_id

    return comparison_summary_model_id()


def _pct(value: Optional[float]) -> Optional[int]:
    return int(round(value * 100)) if isinstance(value, (int, float)) else None


def build_summary_payload(
    cars: Dict[str, Dict[str, Any]],
    categories: Dict[str, Dict[str, Any]],
    overall: Dict[str, Any],
    coverage: Dict[str, Any],
    buyer_context: Optional[Dict[str, Any]],
    limitations: List[str],
) -> Dict[str, Any]:
    """Everything Gemini may say comes from here (no web data, no raw claims)."""
    cat_items = []
    for key, cat in categories.items():
        decision = cat.get("decision") or {}
        choice = decision.get("choice")
        if cat.get("status") == NOT_APPLICABLE:
            continue
        reasons = []
        for r in cat.get("top_reasons") or []:
            reasons.append({"metric": r.get("label_he"), "leader": r.get("leader"), "values": r.get("display")})
        cat_items.append(
            {
                "category": CATEGORY_LABELS_HE.get(key, key),
                "choice": choice,
                "choice_name": cars.get(choice, {}).get("display_name") if choice in cars else None,
                "decision_confidence_pct": _pct(decision.get("confidence")),
                "top_reasons": reasons[:3],
                "missing_information": len(cat.get("evidence", {}).get("missing_metrics") or []) > 0,
            }
        )
    return {
        "cars": {slot: {"name": c.get("display_name"), "year": c.get("year"), "trim": c.get("trim")} for slot, c in cars.items()},
        "overall": {
            "choice": overall.get("choice"),
            "choice_name": cars.get(overall.get("choice"), {}).get("display_name") if overall.get("choice") in cars else None,
            "decision_confidence_pct": _pct(overall.get("confidence")),
        },
        "categories": cat_items,
        "coverage": {
            slot: {"government": c["government"]["label_he"], "official_sources": c["official"]["label_he"]}
            for slot, c in coverage.items()
        },
        "buyer_priorities": (buyer_context or {}).get("emphasis") or [],
        "limitations": limitations,
    }


def build_summary_prompt(payload: Dict[str, Any]) -> str:
    return "\n".join(
        [
            "כתוב סיכום קצר בעברית (2 עד 5 משפטים) להשוואת רכבים, על בסיס ה-JSON בלבד.",
            "כללים:",
            "- אל תשנה את ההכרעה הכוללת (overall.choice) ואל תבחר רכב אחר.",
            "- אל תוסיף עובדות, מספרים או מידע שאינם מופיעים ב-JSON, ואל תשתמש בידע שלך על הרכבים.",
            "- אל תחשב מחדש שום נתון.",
            "- אם overall.choice הוא insufficient_evidence — כתוב שאין מספיק מידע להכרעה כוללת. אם tie — שאין כרגע יתרון משמעותי. אם decision_unavailable — שההכרעה אינה זמינה וההשוואה מציגה נתונים בלבד.",
            "- אם הכיסוי של מקורות היצרן חלקי או נמוך — ציין זאת.",
            "- אל תשתמש במילים מוחלטות (כמו 'ללא ספק', 'בוודאות'), אל תכתוב 'ציון' ואל תכתוב 'מנצח'.",
            "- נסח כך: 'לפי הנתונים הזמינים כרגע, ל־X יש יתרון כולל.'",
            "- החזר JSON עם stated_overall_choice (העתק מדויק של overall.choice) ו-summary_he.",
            "",
            json.dumps(payload, ensure_ascii=False),
        ]
    )


def _allowed_numbers(payload: Dict[str, Any]) -> set:
    text = json.dumps(payload, ensure_ascii=False)
    nums = set(re.findall(r"\d+(?:\.\d+)?", text))
    nums |= {n.replace(".0", "") for n in nums}
    nums |= {"1", "2", "3", "5", "100"}  # "רכב 1/2/3", "0-100"
    return nums


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


def validate_summary(output: Any, payload: Dict[str, Any], cars: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Return the validated summary text, or None if it must be discarded."""
    if not isinstance(output, dict):
        return None
    stated = output.get("stated_overall_choice")
    text = output.get("summary_he")
    overall_choice = payload["overall"]["choice"]
    if stated != overall_choice or not isinstance(text, str):
        return None
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) > MAX_SUMMARY_CHARS:
        return None
    sentences = _sentences(text)
    if not (MIN_SENTENCES <= len(sentences) <= MAX_SENTENCES):
        return None
    lowered = text.lower()
    if any(p.lower() in lowered for p in FORBIDDEN_PHRASES):
        return None
    allowed = _allowed_numbers(payload)
    for num in re.findall(r"\d+(?:\.\d+)?", text):
        if num not in allowed:
            return None
    names = {slot: (c.get("display_name") or "") for slot, c in cars.items()}
    for sentence in sentences:
        if "יתרון כולל" in sentence or "היתרון הכולל" in sentence:
            if overall_choice not in names:
                if not re.search(r"(אין|לא)\s", sentence):
                    return None
                continue
            others = [n for s, n in names.items() if s != overall_choice and n]
            if names[overall_choice] not in sentence or any(o in sentence for o in others):
                return None
    return text


def deterministic_summary(
    cars: Dict[str, Dict[str, Any]],
    categories: Dict[str, Dict[str, Any]],
    overall: Dict[str, Any],
    coverage: Dict[str, Any],
) -> str:
    """Hebrew template used when Gemini fails or is rejected."""
    def name(slot):
        return cars.get(slot, {}).get("display_name") or slot

    leads: Dict[str, List[str]] = {slot: [] for slot in cars}
    insufficient: List[str] = []
    for key, cat in categories.items():
        choice = (cat.get("decision") or {}).get("choice")
        if cat.get("status") == NOT_APPLICABLE:
            continue
        if choice in leads:
            leads[choice].append(CATEGORY_LABELS_HE.get(key, key))
        elif choice in (CHOICE_INSUFFICIENT,):
            insufficient.append(CATEGORY_LABELS_HE.get(key, key))

    lines: List[str] = []
    choice = overall.get("choice")
    if choice in cars:
        lines.append(f"לפי הנתונים הזמינים כרגע, ל־{name(choice)} יש יתרון כולל.")
    elif choice == CHOICE_TIE:
        lines.append("לפי הנתונים הזמינים כרגע, אין יתרון כולל משמעותי לאחד הרכבים.")
    elif choice == CHOICE_INSUFFICIENT:
        lines.append("אין כרגע מספיק מידע מאומת להכרעה כוללת.")
    else:
        lines.append("ההכרעה הכוללת אינה זמינה כרגע, ולכן מוצגת השוואת נתונים בלבד.")
    for slot, cats in leads.items():
        if cats:
            lines.append(f"{name(slot)} מוביל בתחומים: {', '.join(cats[:4])}.")
    if insufficient:
        lines.append(f"בתחומים {', '.join(insufficient[:3])} אין מספיק נתונים להשוואה מלאה.")
    low_official = [name(s) for s, c in coverage.items() if c["official"]["label_he"] in ("נמוך", "אין")]
    if low_official:
        lines.append("ההכרעה מבוססת על הנתונים הזמינים בלבד; מידע מאתרי היצרן חלקי.")
    else:
        lines.append("ההכרעה מבוססת על הנתונים הזמינים בלבד.")
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


def produce_summary(
    writer: Optional[GeminiSummaryWriter],
    cars: Dict[str, Dict[str, Any]],
    categories: Dict[str, Dict[str, Any]],
    overall: Dict[str, Any],
    coverage: Dict[str, Any],
    buyer_context: Optional[Dict[str, Any]],
    limitations: List[str],
) -> Dict[str, Any]:
    payload = build_summary_payload(cars, categories, overall, coverage, buyer_context, limitations)
    fallback = deterministic_summary(cars, categories, overall, coverage)
    if writer is None or overall.get("choice") == DECISION_UNAVAILABLE:
        # Without a structured decision there is nothing for Gemini to explain.
        return {"text": fallback, "source": "deterministic_fallback", "reason": "no_writer" if writer is None else "decision_unavailable", "payload": payload}
    result = writer.write(payload)
    if result.get("error_code"):
        logger.warning("comparison_v2 summary_failed error=%s", result["error_code"])
        return {"text": fallback, "source": "deterministic_fallback", "reason": result["error_code"], "payload": payload, "duration_ms": result.get("duration_ms")}
    text = validate_summary(result.get("output"), payload, cars)
    if not text:
        logger.warning("comparison_v2 summary_failed error=SUMMARY_REJECTED")
        return {"text": fallback, "source": "deterministic_fallback", "reason": "SUMMARY_REJECTED", "payload": payload, "duration_ms": result.get("duration_ms")}
    logger.info("comparison_v2 summary_completed model=%s duration_ms=%s", writer.model_id, result.get("duration_ms"))
    return {"text": text, "source": "gemini", "reason": None, "payload": payload, "model": writer.model_id, "duration_ms": result.get("duration_ms")}
