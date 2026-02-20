# -*- coding: utf-8 -*-
"""Leasing Advisor service – BIK calculation, candidate selection, Gemini prompt."""

import csv
import io
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

from app.extensions import db
from app.models import LeasingAdvisorHistory
from app.utils.http_helpers import get_request_id

# ── BIK 2026 constants (verify annually) ──────────────────────────────
BIK_RATE_2026 = 0.0248
BIK_CAP_PRICE_2026 = 596860
BIK_GREEN_DISCOUNTS_2026: Dict[str, int] = {
    "ev": 1380,
    "phev": 1150,
    "hybrid": 580,
    "ice": 0,
}
ALLOWED_POWERTRAINS = set(BIK_GREEN_DISCOUNTS_2026.keys()) | {"unknown"}
LEASING_GEMINI_MODEL_ID = "gemini-3-flash-preview"
UPLOAD_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

# ── Catalog cache (module-level) ──────────────────────────────────────
_catalog_cache: Optional[List[Dict[str, Any]]] = None


def _parse_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes")


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def load_catalog() -> List[Dict[str, Any]]:
    """Load internal car catalog CSV with in-memory caching."""
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache

    # Leasing candidates are loaded from a local CSV and cached in-memory (no external API call).
    catalog_path = os.path.join(os.path.dirname(__file__), "..", "data", "leasing_catalog_il_2026.csv")
    rows: List[Dict[str, Any]] = []
    with open(catalog_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "make": (row.get("make") or "").strip(),
                "model": (row.get("model") or "").strip(),
                "trim": (row.get("trim") or "").strip(),
                "list_price_ils": _safe_int(row.get("list_price_ils")),
                "powertrain": (row.get("powertrain") or "ice").strip().lower(),
                "body_type": (row.get("body_type") or "").strip().lower(),
                "seats": _safe_int(row.get("seats"), 5),
                "trunk_liters": _safe_int(row.get("trunk_liters")),
                "safety_stars": _safe_int(row.get("safety_stars")),
                "has_adas": _parse_bool(row.get("has_adas")),
                "has_carplay": _parse_bool(row.get("has_carplay")),
            })
    _catalog_cache = rows
    return rows


# ── BIK calculation ───────────────────────────────────────────────────

def calc_bik_2026(list_price_ils: int, powertrain: str) -> Dict[str, Any]:
    """
    Compute monthly BIK benefit (₪) for Israel 2026 tax rules.
    Returns breakdown dict.
    """
    pt = powertrain.lower() if powertrain else "ice"
    if pt not in BIK_GREEN_DISCOUNTS_2026:
        pt = "ice"

    cap = BIK_CAP_PRICE_2026
    price_for_calc = min(list_price_ils, cap)
    discount = BIK_GREEN_DISCOUNTS_2026[pt]
    bik = price_for_calc * BIK_RATE_2026 - discount
    bik = max(bik, 0)

    return {
        "list_price_ils": list_price_ils,
        "price_for_calc": price_for_calc,
        "capped": list_price_ils > cap,
        "rate": BIK_RATE_2026,
        "discount": discount,
        "powertrain": pt,
        "monthly_bik": round(bik, 2),
    }


def invert_list_price_from_bik(bik_monthly: float, powertrain: str = "unknown") -> Dict[str, Any]:
    """
    Given a monthly BIK, invert to approximate list price.
    If powertrain is unknown, return range across all powertrains.
    """
    if powertrain != "unknown" and powertrain in BIK_GREEN_DISCOUNTS_2026:
        discount = BIK_GREEN_DISCOUNTS_2026[powertrain]
        price = (bik_monthly + discount) / BIK_RATE_2026
        capped = price >= BIK_CAP_PRICE_2026
        if capped:
            price = BIK_CAP_PRICE_2026
        return {
            "powertrain": powertrain,
            "estimated_list_price": round(price),
            "capped": capped,
        }

    results = {}
    for pt, disc in BIK_GREEN_DISCOUNTS_2026.items():
        price = (bik_monthly + disc) / BIK_RATE_2026
        capped = price >= BIK_CAP_PRICE_2026
        if capped:
            price = BIK_CAP_PRICE_2026
        results[pt] = {"estimated_list_price": round(price), "capped": capped}
    return {"powertrain": "unknown", "ranges": results}


# ── File parsing (CSV / XLSX) ─────────────────────────────────────────

_COL_ALIASES: Dict[str, List[str]] = {
    "make": ["make", "יצרן", "manufacturer"],
    "model": ["model", "דגם", "model_name"],
    "list_price_ils": ["list_price_ils", "list_price", "מחיר מחירון", "מחיר", "price"],
    "powertrain": ["powertrain", "הנעה", "fuel", "fuel_type", "סוג דלק"],
    "body_type": ["body_type", "סוג מרכב", "body"],
    "trim": ["trim", "גימור", "level"],
}


def _normalize_columns(headers: List[str]) -> Dict[str, str]:
    """Map raw column headers to canonical names."""
    mapping: Dict[str, str] = {}
    lower_headers = {h.strip().lower(): h for h in headers}
    for canonical, aliases in _COL_ALIASES.items():
        for alias in aliases:
            if alias.lower() in lower_headers:
                mapping[canonical] = lower_headers[alias.lower()]
                break
    return mapping


def parse_company_file(file_storage) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Parse uploaded company car list (CSV or XLSX).
    Returns (rows, error_message).
    """
    filename = (file_storage.filename or "").lower()
    data = file_storage.read()
    if len(data) > UPLOAD_MAX_BYTES:
        return [], "File exceeds 5 MB limit"

    if filename.endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        reader = csv.DictReader(io.StringIO(text))
        raw_rows = list(reader)
        if not raw_rows:
            return [], "CSV file is empty"
        col_map = _normalize_columns(list(raw_rows[0].keys()))
    elif filename.endswith(".xlsx"):
        try:
            import openpyxl
        except ImportError:
            return [], "XLSX support requires openpyxl library"
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        headers = [str(c or "") for c in next(rows_iter, [])]
        if not headers:
            return [], "XLSX file has no headers"
        col_map = _normalize_columns(headers)
        raw_rows = []
        for row_vals in rows_iter:
            raw_rows.append({headers[i]: (row_vals[i] if i < len(row_vals) else None) for i in range(len(headers))})
        wb.close()
    else:
        return [], "Unsupported file type. Use CSV or XLSX."

    # Build normalized rows
    parsed: List[Dict[str, Any]] = []
    for raw in raw_rows:
        entry: Dict[str, Any] = {}
        for canon, orig in col_map.items():
            entry[canon] = raw.get(orig, "")
        if entry.get("list_price_ils"):
            entry["list_price_ils"] = _safe_int(entry["list_price_ils"])
        if entry.get("powertrain"):
            entry["powertrain"] = str(entry["powertrain"]).strip().lower()
        parsed.append(entry)

    return parsed, None


# ── Candidate selection ───────────────────────────────────────────────

def select_candidates(
    data_source: List[Dict[str, Any]],
    max_bik: Optional[float] = None,
    powertrain: Optional[str] = None,
    body_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Filter candidates from data_source based on frame constraints.
    Adds computed BIK to each row if list_price_ils is available.
    """
    results: List[Dict[str, Any]] = []
    for row in data_source:
        price = _safe_int(row.get("list_price_ils"))
        pt = (row.get("powertrain") or "ice").lower()
        if pt not in ALLOWED_POWERTRAINS:
            pt = "ice"

        # Compute BIK if price available
        if price > 0:
            bik_info = calc_bik_2026(price, pt)
            row_bik = bik_info["monthly_bik"]
        else:
            bik_info = None
            row_bik = None

        # Filter by max BIK
        if max_bik is not None and row_bik is not None and row_bik > max_bik:
            continue
        # Filter by powertrain
        if powertrain and powertrain != "unknown" and pt != powertrain:
            continue
        # Filter by body type
        if body_type and (row.get("body_type") or "").lower() != body_type.lower():
            continue

        candidate = dict(row)
        if bik_info:
            candidate["bik"] = bik_info
        results.append(candidate)

    return results


# ── Gemini prompt builder ─────────────────────────────────────────────

def build_gemini_prompt(
    prefs: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    frame_context: Dict[str, Any],
) -> str:
    """
    Build a Gemini prompt for leasing car ranking.
    Requests strict JSON output and forbids inventing data.
    """
    cand_summary = json.dumps(candidates[:30], ensure_ascii=False, default=str)
    prefs_summary = json.dumps(prefs, ensure_ascii=False)
    frame_summary = json.dumps(frame_context, ensure_ascii=False)

    prompt = f"""You are an expert Israeli company-car leasing advisor.
The user has a BIK (Benefit-in-Kind / שווי שימוש) budget frame and preferences.
Your task: rank the provided candidate cars and return the top 3 recommendations.

STRICT RULES:
1. Output ONLY valid JSON — no markdown, no explanation text outside JSON.
2. Do NOT invent prices, specs, or BIK values. Use ONLY the data provided below.
3. Rank ONLY among the provided candidates.
4. Respond in Hebrew.

BIK Frame Context:
{frame_summary}

User Preferences:
{prefs_summary}

Candidate Cars (up to 30):
{cand_summary}

Return JSON with this exact schema:
{{
  "top3": [
    {{
      "rank": 1,
      "make": "...",
      "model": "...",
      "trim": "...",
      "monthly_bik": ...,
      "list_price_ils": ...,
      "reason_he": "...",
      "strengths": ["..."],
      "weaknesses": ["..."]
    }}
  ],
  "full_ranking": [
    {{
      "rank": 1,
      "make": "...",
      "model": "...",
      "score": 0-100
    }}
  ],
  "warnings": ["..."]
}}
"""
    return prompt


# ── Gemini call ───────────────────────────────────────────────────────

def call_gemini_leasing(
    prefs: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    frame_context: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Call Gemini 3 Flash for leasing recommendation.
    Returns (parsed_result, error_string).
    """
    import app.extensions as ext
    from json_repair import repair_json

    client = ext.ai_client
    if not client:
        return None, "AI client not initialized"

    prompt = build_gemini_prompt(prefs, candidates, frame_context)
    request_id = get_request_id()

    try:
        from google.genai import types as genai_types
        response = client.models.generate_content(
            model=LEASING_GEMINI_MODEL_ID,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=4096,
            ),
        )
        raw_text = response.text or ""
    except Exception as e:
        current_app.logger.error("[LEASING] Gemini call failed request_id=%s: %s", request_id, e)
        return None, "AI call failed"

    # Parse JSON response
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            repaired = repair_json(raw_text)
            parsed = json.loads(repaired)
        except Exception:
            current_app.logger.error("[LEASING] JSON parse failed request_id=%s", request_id)
            return None, "AI returned invalid JSON"

    # Validate schema minimally
    if not isinstance(parsed, dict) or "top3" not in parsed:
        return None, "AI response missing required fields"

    return parsed, None


# ── Save to history ───────────────────────────────────────────────────

def save_leasing_history(
    user_id: int,
    frame_input: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    prefs: Dict[str, Any],
    gemini_response: Dict[str, Any],
    duration_ms: int,
    request_id: str,
) -> Optional[int]:
    """Persist leasing advisor result to DB. Returns history ID or None."""
    try:
        record = LeasingAdvisorHistory(
            user_id=user_id,
            frame_input_json=json.dumps(frame_input, ensure_ascii=False),
            candidates_json=json.dumps(candidates[:30], ensure_ascii=False),
            prefs_json=json.dumps(prefs, ensure_ascii=False),
            gemini_response_json=json.dumps(gemini_response, ensure_ascii=False),
            request_id=request_id,
            duration_ms=duration_ms,
        )
        db.session.add(record)
        db.session.commit()
        return record.id
    except Exception as e:
        current_app.logger.warning("[LEASING] Failed to save history: %s", e)
        db.session.rollback()
        return None
