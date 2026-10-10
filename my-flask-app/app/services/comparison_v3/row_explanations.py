# -*- coding: utf-8 -*-
"""Row explanations (E): ONE ungrounded ``gemini-3.8-flash`` call per comparison, after composition.

Input per row: id, Hebrew label, unit, standard, source, the value per car slot, direction, leader / tie, plus the
normalized buyer-profile headline. No URLs, no raw records. Output ``{row_id: text_he}``: 2-4 sentences, at most
450 characters, saying what the measure is and how it is measured, and what the difference means in practice for
this buyer.

Every row text is validated (the ``summary_writer`` checks applied per row): every number must appear in that row's
payload; no car is named as better unless it is the row's leader; no percentage or score wording; no reference to
another row's data; size limits; history rows never say "אמין" / "אמינות", and the road-survival text must say the
cancellation reason is unknown. The recalls row may cite only each notice's ``affected_system`` and ``recall_year``
(the only detail keys in its payload) and never judges a recall's severity. A rejected or missing row falls back to the metric's deterministic ``explain_he``
(what it measures, its source and standard), which never states that anything is missing.

Model: ``DEFAULT_COMPARISON_V2_MODEL_ID`` (code configuration; no env var). ``temperature=0``,
``thinking_level=LOW``, no tools. The texts are stored with the comparison, so a cached comparison makes no call.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.services.comparison.model_config import DEFAULT_COMPARISON_V2_MODEL_ID
from app.services.comparison_v3.metrics import METRICS_BY_KEY

logger = logging.getLogger("comparison_v3")

MAX_ROW_CHARS = 450
MIN_SENTENCES, MAX_SENTENCES = 2, 4
TIMEOUT_SEC = 25
FORBIDDEN = ("%", "ציון", "/100", "אחוז", "http", "www.", "ללא ספק", "בוודאות")
HISTORY_FORBIDDEN = ("אמין",)
UNKNOWN_REASON_MARKERS = ("לא ידוע", "אינה ידועה", "לא ידועה", "אינו ידוע")
SEVERITY_FORBIDDEN = ("חמור", "מסוכן", "קריטי", "מסכן", "זניח", "רציני", "קל ערך")
RECALL_PAYLOAD_KEYS = ("recall_year", "affected_system")          # never fault / repair text, never a severity
SURVIVAL_PAYLOAD_KEYS = ("cohort_year", "cohort_basis", "reference_month")
WINNER_MARKERS = ("טוב יותר", "עדיף", "יתרון", "מוביל", "מנצח", "הטוב", "מתאים יותר")
DIRECTION_EN = {1: "higher_is_better", -1: "lower_is_better", 0: "display_only"}


def row_payload(row: Dict[str, Any], names: Dict[str, str]) -> Dict[str, Any]:
    """The immutable per-row input (no URLs, no raw records)."""
    leader = row.get("leader")
    out = {
        "row_id": row["row_id"],
        "label_he": row["label_he"],
        "unit_he": row.get("unit_he") or None,
        "standard": row.get("standard") if row.get("standard") and not str(row.get("standard")).startswith("stated:") else None,
        "sources": row.get("sources") or [],
        "values": {names.get(slot, slot): cell["text"] for slot, cell in row["cells"].items()},
        "direction": DIRECTION_EN[row.get("direction") or 0],
        "leader": names.get(leader) if leader in names else ("tie" if leader == "tie" else None),
        "comparability_note_he": row.get("note_he"),
    }
    if row.get("history"):
        out["note"] = "Government history data, display only; never describe it as reliability."
    if row["row_id"] == "recalls":
        out["recalls"] = {names.get(slot, slot): [{k: d[k] for k in RECALL_PAYLOAD_KEYS if d.get(k) not in (None, "")}
                                                  for d in cell.get("details") or []]
                          for slot, cell in row["cells"].items()}
        out["note"] = ("Recall notices of the model and its production year (government dataset). You may mention "
                       "only affected_system and recall_year; never judge how severe a recall is and never call it "
                       "reliability.")
    if row["row_id"] == "road_survival":
        out["cohort"] = {names.get(slot, slot): {k: cell[k] for k in SURVIVAL_PAYLOAD_KEYS if cell.get(k) not in (None, "")}
                         for slot, cell in row["cells"].items()}
        out["note"] = ("Share of the model's cars cancelled from the road by the same age for every car; the "
                       "cancellation reason is unknown and must be said so. Never call it reliability.")
    return {k: v for k, v in out.items() if v not in (None, [], "")}


def build_prompt(rows: List[Dict[str, Any]], profile_headline: List[str]) -> str:
    return "\n".join([
        "כתוב לכל שורה בטבלת השוואת רכבים הסבר קצר בעברית, על בסיס ה-JSON בלבד.",
        "לכל row_id: 2 עד 4 משפטים, עד 450 תווים. הסבר מה המדד ואיך הוא נמדד (למשל WLTP, NEDC או דירוג משרד התחבורה),",
        "ומה המשמעות המעשית של ההבדל עבור הקונה לפי השימוש שהגדיר (buyer_profile).",
        "כללים:",
        "- השתמש רק במספרים שמופיעים באותה שורה. אל תזכיר נתונים של שורות אחרות.",
        "- אל תציין רכב כעדיף אלא אם הוא ה-leader של השורה. אם leader הוא tie או חסר — אל תציין רכב עדיף.",
        "- אל תכתוב אחוזים, ציונים או מילים מוחלטות. אל תשתמש בידע שלך על הרכבים.",
        "- בשורות היסטוריה אל תכתוב 'אמינות'. בשורת הירידה מהכביש כתוב שסיבת הירידה מהכביש אינה ידועה.",
        "- בשורת הריקולים מותר לציין רק את המערכת שנפגעה (affected_system) ואת שנת הריקול (recall_year). "
        "אל תעריך את חומרת הריקול.",
        "- החזר JSON שבו כל מפתח הוא row_id והערך הוא ההסבר.",
        "",
        json.dumps({"buyer_profile": profile_headline, "rows": rows}, ensure_ascii=False),
    ])


def _sentences(text: str) -> List[str]:
    return [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]


def _allowed_numbers(payload: Dict[str, Any]) -> set:
    text = json.dumps(payload, ensure_ascii=False)
    nums = set(re.findall(r"\d+(?:[.,]\d+)*", text))
    nums |= {n.replace(",", "") for n in nums}
    return nums


def validate_row_text(text: Any, payload: Dict[str, Any], other_labels: List[str], names: Dict[str, str]) -> Optional[str]:
    """The validated text, or None when it must be replaced by the fallback."""
    if not isinstance(text, str):
        return None
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) > MAX_ROW_CHARS:
        return None
    sentences = _sentences(text)
    if not (MIN_SENTENCES <= len(sentences) <= MAX_SENTENCES):
        return None
    if any(f in text for f in FORBIDDEN):
        return None
    history = payload["row_id"] in ("recalls", "road_survival", "original_new_price_ils", "depreciation_from_new")
    if history and any(f in text for f in HISTORY_FORBIDDEN):
        return None
    if payload["row_id"] == "road_survival" and not any(m in text for m in UNKNOWN_REASON_MARKERS):
        return None
    if payload["row_id"] == "recalls" and any(f in text for f in SEVERITY_FORBIDDEN):
        return None
    allowed = _allowed_numbers(payload)
    for num in re.findall(r"\d+(?:[.,]\d+)*", text):
        if num not in allowed and num.replace(",", "") not in allowed:
            return None
    own = payload["label_he"]
    for label in other_labels:
        if len(label) >= 4 and label not in own and label in text:
            return None                                     # another row's data
    leader = payload.get("leader")
    car_names = [n for n in names.values() if n]
    for sentence in sentences:
        if not any(m in sentence for m in WINNER_MARKERS):
            continue
        named = [n for n in car_names if n in sentence]
        if named and (leader in (None, "tie") or any(n != leader for n in named)):
            return None
    return text


def fallback_text(row: Dict[str, Any]) -> str:
    metric = METRICS_BY_KEY[row["row_id"]]
    text = metric.explain_he
    if row.get("standard") and row["standard"] in ("WLTP", "NEDC") and row["standard"] not in text:
        text += f" כל הערכים בשורה נמדדו לפי תקן {row['standard']}."
    if row.get("note_he"):
        text += " " + row["note_he"]
    return text


class GeminiRowExplanationWriter:
    def __init__(self, client: Any, model_id: Optional[str] = None, timeout_sec: int = TIMEOUT_SEC):
        self.client = client
        self.model_id = model_id or DEFAULT_COMPARISON_V2_MODEL_ID
        self.timeout_sec = timeout_sec
        self.calls = 0

    def _config(self, row_ids: List[str]):
        from google.genai import types as genai_types

        return genai_types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=4096,
            response_mime_type="application/json",
            response_json_schema={"type": "object", "properties": {rid: {"type": "string"} for rid in row_ids}},
            thinking_config=genai_types.ThinkingConfig(thinking_level=genai_types.ThinkingLevel.LOW),
            http_options=genai_types.HttpOptions(timeout=int(self.timeout_sec * 1000),
                                                 retry_options=genai_types.HttpRetryOptions(attempts=1)),
        )

    def write(self, rows: List[Dict[str, Any]], profile_headline: List[str]) -> Dict[str, Any]:
        if self.client is None:
            return {"output": None, "error_code": "CLIENT_NOT_INITIALIZED", "duration_ms": 0}
        self.calls += 1
        started = time.perf_counter()
        try:
            resp = self.client.models.generate_content(model=self.model_id, contents=build_prompt(rows, profile_headline),
                                                       config=self._config([r["row_id"] for r in rows]))
            output = resp.parsed if isinstance(getattr(resp, "parsed", None), dict) else json.loads(getattr(resp, "text", None) or "")
        except Exception as exc:  # noqa: BLE001
            return {"output": None, "error_code": f"EXPLANATIONS_ERROR:{type(exc).__name__}",
                    "duration_ms": int((time.perf_counter() - started) * 1000)}
        return {"output": output, "error_code": None, "duration_ms": int((time.perf_counter() - started) * 1000)}


def produce_row_explanations(writer, rows: List[Dict[str, Any]], names: Dict[str, str],
                             profile_headline: List[str]) -> Dict[str, Any]:
    """{texts: {row_id: text}, sources: {row_id: gemini | fallback}, ...}: exactly one call (or none)."""
    payloads = [row_payload(r, names) for r in rows]
    texts = {r["row_id"]: fallback_text(r) for r in rows}
    sources = {r["row_id"]: "fallback" for r in rows}
    meta: Dict[str, Any] = {"model": getattr(writer, "model_id", None), "calls": 0, "rejected": []}
    if writer is None or not rows:
        meta["reason"] = "no_writer" if writer is None else "no_rows"
        return {"texts": texts, "sources": sources, **meta}
    result = writer.write(payloads, profile_headline)
    meta["calls"] = 1
    meta["duration_ms"] = result.get("duration_ms")
    if result.get("error_code") or not isinstance(result.get("output"), dict):
        meta["reason"] = result.get("error_code") or "INVALID_OUTPUT"
        logger.warning("comparison_v3 row_explanations_failed error=%s", meta["reason"])
        return {"texts": texts, "sources": sources, **meta}
    labels = {p["row_id"]: p["label_he"] for p in payloads}
    for payload in payloads:
        rid = payload["row_id"]
        others = [label for r, label in labels.items() if r != rid]
        text = validate_row_text(result["output"].get(rid), payload, others, names)
        if text:
            texts[rid], sources[rid] = text, "gemini"
        else:
            meta["rejected"].append(rid)
    return {"texts": texts, "sources": sources, **meta}
