# -*- coding: utf-8 -*-
# ===================================================================
# 🚗 Car Reliability Analyzer – Israel
# v7.4.2 (Render DB Hard-Fail + No double create_app + /healthz + date fix)
# Phase 1 & 2: Security hardening complete
# ===================================================================

import os, re, json, traceback, logging, uuid, random, hashlib, concurrent.futures, atexit
import time as pytime
from typing import Optional, Tuple, Any, Dict, Mapping
from datetime import datetime, time, timedelta, date
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from sqlalchemy import inspect, desc
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, send_from_directory
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    current_user, login_required
)
from authlib.integrations.base_client.errors import MismatchingStateError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import RequestEntityTooLarge
from html import escape
from json_repair import repair_json
import pandas as pd

# --- Gemini 3 (Gemini API SDK) ---
from google import genai as genai3
from google.genai import types as genai_types
try:
    import google.generativeai as genai  # Legacy SDK (optional)
except Exception:
    genai = None

# --- Input Validation (Security: Tier 2 - S3 + S4) ---
from app.utils.validation import ValidationError, validate_analyze_request
# --- Output Sanitization (Security: Tier 2 - S5 + S6) ---
from app.utils.sanitization import (
    sanitize_analyze_response,
    sanitize_advisor_response,
    derive_missing_info,
    sanitize_reliability_report_response,
)
from app.utils.db_bootstrap import ensure_search_history_cache_key, ensure_duration_ms_columns
from app.utils.micro_reliability import compute_micro_reliability
from app.utils.timeline_plan import build_timeline_plan
from app.utils.sim_model import build_sim_model
# --- Prompt Injection Defense (Security: Phase 1C) ---
from app.utils.prompt_defense import (
    sanitize_user_input_for_prompt,
    wrap_user_input_in_boundary,
    create_data_only_instruction,
    escape_prompt_input,
)
# --- HTTP Helpers (moved from create_app scope) ---
from app.utils.http_helpers import (
    api_ok,
    api_error,
    get_request_id,
    is_owner_user,
    get_redirect_uri,
    log_rejection,
)
import app.extensions as extensions
from app.extensions import db, login_manager, oauth, migrate, GEMINI3_MODEL_ID
from app.models import (
    User,
    SearchHistory,
    AdvisorHistory,
    DailyQuotaUsage,
    QuotaReservation,
    IpRateLimit,
)
from app.quota import (
    resolve_app_timezone,
    compute_quota_window,
    parse_owner_emails,
    ModelOutputInvalidError,
    QuotaInternalError,
    log_access_decision,
    get_client_ip,
    get_daily_quota_usage,
    cleanup_expired_reservations,
    reserve_daily_quota,
    finalize_quota_reservation,
    release_quota_reservation,
    check_and_increment_ip_rate_limit,
    rollback_quota_increment,
)

# =========================
# ========= CONFIG ========
# =========================
AI_CALL_TIMEOUT_SEC = int(os.environ.get("AI_CALL_TIMEOUT_SEC", "170"))  # timeout for each AI call attempt
GLOBAL_DAILY_LIMIT = 1000
USER_DAILY_LIMIT = int(os.environ.get("QUOTA_LIMIT", "5"))
MAX_CACHE_DAYS = 45
PER_IP_PER_MIN_LIMIT = 20
QUOTA_RESERVATION_TTL_SECONDS = int(os.environ.get("QUOTA_RESERVATION_TTL_SECONDS", "600"))
MAX_ACTIVE_RESERVATIONS = 1
AI_EXECUTOR_WORKERS = int(os.environ.get("AI_EXECUTOR_WORKERS", "4"))
AI_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=AI_EXECUTOR_WORKERS)
atexit.register(lambda: AI_EXECUTOR.shutdown(wait=True))
import app.quota as quota_module
quota_module.USER_DAILY_LIMIT = USER_DAILY_LIMIT
quota_module.GLOBAL_DAILY_LIMIT = GLOBAL_DAILY_LIMIT
quota_module.MAX_CACHE_DAYS = MAX_CACHE_DAYS
quota_module.PER_IP_PER_MIN_LIMIT = PER_IP_PER_MIN_LIMIT
quota_module.QUOTA_RESERVATION_TTL_SECONDS = QUOTA_RESERVATION_TTL_SECONDS
quota_module.MAX_ACTIVE_RESERVATIONS = MAX_ACTIVE_RESERVATIONS


def current_user_daily_limit() -> int:
    try:
        import main as main_module
        return getattr(main_module, "USER_DAILY_LIMIT", USER_DAILY_LIMIT)
    except Exception:
        return USER_DAILY_LIMIT


def get_ai_call_fn():
    try:
        import importlib
        main_module = importlib.import_module("main")
        return getattr(main_module, "call_gemini_grounded_once", call_gemini_grounded_once)
    except Exception:
        return call_gemini_grounded_once

# ==================================
# === 3. פונקציות עזר (גלובלי) ===
# ==================================

def resolve_app_timezone() -> Tuple[ZoneInfo, str]:
    """
    Resolve application timezone from APP_TZ env with safe fallback to UTC.
    """
    tz_name = os.environ.get("APP_TZ", "UTC").strip() or "UTC"
    try:
        return ZoneInfo(tz_name), tz_name
    except Exception:
        fallback = "UTC"
        print(f"[QUOTA] ⚠️ Invalid APP_TZ='{tz_name}', falling back to UTC")
        return ZoneInfo(fallback), fallback


def compute_quota_window(tz: ZoneInfo, *, now: Optional[datetime] = None) -> Tuple[date, datetime, datetime, datetime, datetime, int]:
    """
    Compute timezone-aware quota window boundaries and retry-after seconds.
    """
    now_utc = now.astimezone(ZoneInfo("UTC")) if now else datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
    now_tz = now_utc.astimezone(tz) if tz else now_utc
    day_key = now_tz.date()
    window_start = datetime.combine(day_key, time.min, tzinfo=tz)
    window_end = datetime.combine(day_key, time.max, tzinfo=tz)
    resets_at = datetime.combine(day_key + timedelta(days=1), time.min, tzinfo=tz)
    retry_after = max(0, int((resets_at - now_tz).total_seconds()))
    return day_key, window_start, window_end, resets_at, now_tz, retry_after


def parse_owner_emails(raw: str) -> list:
    """
    Normalize OWNER_EMAILS env var into a clean, lowercase list.
    """
    return [
        item.strip().lower()
        for item in (raw or "").split(",")
        if item and item.strip()
    ]


class ModelOutputInvalidError(ValueError):
    """Raised when AI model returns an invalid JSON structure."""


class QuotaInternalError(RuntimeError):
    """Raised when the quota subsystem fails internally."""


def log_access_decision(route_name: str, user_id: Optional[int], decision: str, reason: str = ""):
    """
    Safe logging helper for access control decisions.
    Logs route access attempts without exposing sensitive data.
    """
    user_info = f"user_id={user_id}" if user_id else "anonymous"
    log_msg = f"[ACCESS] {route_name} | {user_info} | {decision}"
    if reason:
        log_msg += f" | {reason}"
    print(log_msg)


@login_manager.user_loader
def load_user(user_id):
    """
    Load user by ID from database.
    If DB connection fails, treat as unauthenticated (return None).
    This prevents 500 errors on stale pool connections.
    """
    try:
        return User.query.get(int(user_id))
    except Exception as e:
        # Log the error safely (no secrets, no user IDs)
        print(f"[AUTH] ⚠️ load_user failed: {e.__class__.__name__}")
        try:
            db.session.rollback()
        except Exception:
            pass
        finally:
            # Remove broken connection from pool
            db.session.remove()
        # Treat as unauthenticated instead of crashing
        return None


# --- טעינת המילון ---
try:
    from car_models_dict import israeli_car_market_full_compilation
    print(f"[DICT] ✅ Loaded car_models_dict. Manufacturers: {len(israeli_car_market_full_compilation)}")
    try:
        _total_models = sum(len(models) for models in israeli_car_market_full_compilation.values())
        print(f"[DICT] ✅ Total models loaded: {_total_models}")
    except Exception as inner_e:
        print(f"[DICT] ⚠️ Count models failed: {inner_e}")
except Exception as e:
    print(f"[DICT] ❌ Failed to import car_models_dict: {e}")
    israeli_car_market_full_compilation = {"Toyota": ["Corolla (2008-2025)"]}
    print("[DICT] ⚠️ Fallback applied — Toyota only")

import re as _re


def normalize_text(s: Any) -> str:
    if s is None:
        return ""
    s = _re.sub(r"\(.*?\)", " ", str(s)).strip().lower()
    return _re.sub(r"\s+", " ", s)


def mileage_adjustment(mileage_range: str) -> Tuple[int, Optional[str]]:
    m = normalize_text(mileage_range or "")
    if not m:
        return 0, None
    if "200" in m and "+" in m:
        return -15, "הציון הותאם מטה עקב קילומטראז׳ גבוה מאוד (200K+)."
    if "150" in m and "200" in m:
        return -10, "הציון הותאם מטה עקב קילומטראז׳ גבוה (150–200 אלף ק״מ)."
    if "100" in m and "150" in m:
        return -5, "הציון הותאם מעט מטה עקב קילומטראז׳ בינוני-גבוה (100–150 אלף ק״מ)."
    return 0, None


def apply_mileage_logic(model_output: dict, mileage_range: str) -> Tuple[dict, Optional[str]]:
    try:
        adj, note = mileage_adjustment(mileage_range)
        for base_key in ("base_score_calculated", "overall_score"):
            if base_key in model_output:
                try:
                    base_val = float(model_output[base_key])
                except Exception:
                    m = _re.search(r"-?\d+(\.\d+)?", str(model_output[base_key]))
                    base_val = float(m.group()) if m else None
                if base_val is not None:
                    new_val = max(0.0, min(100.0, base_val + adj))
                    model_output[base_key] = round(new_val, 1)
        return model_output, note
    except Exception:
        return model_output, None


def build_prompt(make, model, sub_model, year, fuel_type, transmission, mileage_range):
    # Phase 1C: Sanitize user inputs to defend against prompt injection
    safe_make = escape_prompt_input(make, max_length=120)
    safe_model = escape_prompt_input(model, max_length=120)
    safe_sub_model = escape_prompt_input(sub_model, max_length=120)
    safe_mileage = escape_prompt_input(mileage_range, max_length=50)
    safe_fuel = escape_prompt_input(fuel_type, max_length=50)
    safe_trans = escape_prompt_input(transmission, max_length=50)
    
    # Wrap user inputs in explicit data-only boundaries
    user_data = f"""רכב: {safe_make} {safe_model}
תת-דגם/תצורה: {safe_sub_model if safe_sub_model else 'לא צוין'}
שנת ייצור: {int(year)}
טווח קילומטראז': {safe_mileage}
סוג דלק: {safe_fuel}
תיבת הילוכים: {safe_trans}"""
    
    bounded_user_data = wrap_user_input_in_boundary(user_data)
    data_instruction = create_data_only_instruction()
    
    return f"""
{data_instruction}

אתה מומחה לאמינות רכבים בישראל עם גישה לחיפוש אינטרנטי.
הניתוח חייב להתייחס ספציפית לטווח הקילומטראז' הנתון.
החזר JSON בלבד:

{{
  "search_performed": true,
  "score_breakdown": {{
    "engine_transmission_score": "מספר (1-10)",
    "electrical_score": "מספר (1-10)",
    "suspension_brakes_score": "מספר (1-10)",
    "maintenance_cost_score": "מספר (1-10)",
    "satisfaction_score": "מספר (1-10)",
    "recalls_score": "מספר (1-10)"
  }},
  "base_score_calculated": "מספר (0-100)",
  "common_issues": ["תקלות נפוצות רלוונטיות לק\\"מ"],
  "avg_repair_cost_ILS": "מספר ממוצע",
  "issues_with_costs": [
    {{"issue": "שם התקלה", "avg_cost_ILS": "מספר", "source": "מקור", "severity": "נמוך/בינוני/גבוה"}}
  ],
  "reliability_summary": "סיכום מקצועי בעברית שמסביר את הציון, יתרונות וחסרונות הרכב, ומאפייני האמינות בצורה מפורטת.",
  "reliability_summary_simple": "הסבר מאוד פשוט וקצר בעברית, ברמה של נהג צעיר שלא מבין ברכבים. בלי מושגים טכניים ובלי קיצורים. להסביר במילים פשוטות למה הציון יצא גבוה/בינוני/נמוך ומה המשמעות ליום-יום (האם זה רכב שיכול לעשות מעט בעיות, הרבה בעיות, כמה להיזהר בקנייה וכו׳).",
  "sources": ["רשימת אתרים"],
  "recommended_checks": ["בדיקות מומלצות ספציפיות"],
  "common_competitors_brief": [
      {{"model": "שם מתחרה 1", "brief_summary": "אמינות בקצרה"}},
      {{"model": "שם מתחרה 2", "brief_summary": "אמינות בקצרה"}}
  ]
}}

{bounded_user_data}

כתוב בעברית בלבד. החזר ONLY JSON, ללא טקסט נוסף.
""".strip()


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

    return f"""
{data_instruction}

אתה יועץ אמינות רכבים בישראל. החזר JSON תקני בלבד (ללא טקסט חופשי, ללא Markdown) עם המפתחות המדויקים:
{{
  "overall_score": 0-100,
  "confidence": "high"|"medium"|"low",
  "one_sentence_verdict": "משפט החלטה קצר",
  "top_risks": [
    {{"risk_title": "", "why_it_matters": "", "how_to_check": "", "severity": "low|medium|high", "cost_impact": "low|medium|high"}}
  ],
  "expected_ownership_cost": {{"maintenance_level": "low|medium|high", "typical_yearly_range_ils": "", "notes": ""}},
  "buyer_checklist": {{
    "ask_seller": ["שאלות/מסמכים"],
    "inspection_focus": ["דגשים לבדיקת מוסך"],
    "walk_away_signs": ["דגלים אדומים לביטול עסקה"]
  }},
  "what_changes_with_mileage": [
    {{"mileage_band": "", "what_to_expect": ""}}
  ],
  "recommended_next_step": {{"action": "", "reason": ""}},
  "missing_info": ["פריטים שחסרים בקלט"]
}}

חוקים:
- עברית בלבד, טון ענייני ותמציתי, ללא שיווק.
- אל תנחש מידע חסר; פרט אותו ב-missing_info.
- אם מציינים סיכון, חובה לכלול how_to_check.
- דגש על פעולות בטוחות לקונה לפני רכישה.

נתוני הקלט:
{bounded_user_data}

Missing info שנמסר לך: {missing_block}
""".strip()


def call_model_with_retry(prompt: str) -> dict:
    """Call Gemini AI model with retry logic, exponential backoff, and timeout.
    
    Phase 1F: Reliability hardening with timeouts and bounded retries.
    
    Args:
        prompt: The prompt to send to the AI model
        
    Returns:
        dict: Parsed JSON response from the model
    
    Raises:
        RuntimeError: If all retries fail
    """
    if genai is None:
        raise RuntimeError("Legacy Gemini SDK unavailable")
    last_err = None
    for model_name in [PRIMARY_MODEL, FALLBACK_MODEL]:
        try:
            llm = genai.GenerativeModel(model_name)
        except Exception as e:
            last_err = e
            print(f"[AI] ❌ init {model_name}: {e}")
            continue
        
        for attempt in range(1, RETRIES + 1):
            try:
                print(f"[AI] Calling {model_name} (attempt {attempt}/{RETRIES})")
                
                # Phase 1F: Configure timeout at SDK level if supported
                # Note: google-generativeai SDK doesn't expose direct timeout config in generate_content
                # but we can use request_options if available in newer versions
                generation_config = {
                    'temperature': 0.3,
                    'top_p': 0.9,
                    'top_k': 40,
                }
                
                # Call with timeout handling at application level
                # The SDK internally uses requests/httpx with default timeouts
                resp = llm.generate_content(
                    prompt,
                    generation_config=generation_config
                )
                
                raw = (getattr(resp, "text", "") or "").strip()
                
                # Phase 1C: Post-validate model output (JSON structure validation)
                if not raw:
                    raise ValueError("Empty response from model")
                
                try:
                    # Try to extract JSON from response
                    m = _re.search(r"\{.*\}", raw, _re.DOTALL)
                    data = json.loads(m.group()) if m else json.loads(raw)
                except Exception:
                    # Fallback: use json-repair for malformed JSON
                    data = json.loads(repair_json(raw))
                
                # Validate that response is a dict (not a list or primitive)
                if not isinstance(data, dict):
                    raise ValueError(f"Model returned non-object JSON: {type(data).__name__}")
                
                print(f"[AI] ✅ success with {model_name}")
                return data
                
            except Exception as e:
                error_type = type(e).__name__
                print(f"[AI] ⚠️ {model_name} attempt {attempt}/{RETRIES} failed: {error_type}: {e}")
                last_err = e
                
                if attempt < RETRIES:
                    # Phase 1F: Exponential backoff with jitter
                    backoff = RETRY_BACKOFF_SEC * (2 ** (attempt - 1))  # exponential
                    jitter = random.uniform(0, 0.5)  # add up to 0.5s jitter
                    sleep_time = backoff + jitter
                    print(f"[AI] Retrying in {sleep_time:.2f}s...")
                    pytime.sleep(sleep_time)
                continue
    
    # All retries exhausted
    error_msg = f"All AI model attempts failed. Last error: {type(last_err).__name__}"
    print(f"[AI] ❌ {error_msg}")
    raise RuntimeError(error_msg)


# ======================================================
# === Gemini 3 unified grounded call (single attempt) ===
# ======================================================

def build_combined_prompt(payload: dict, missing_info: list[str]) -> str:
    """Single prompt that returns analyze + reliability report together."""
    safe_make = escape_prompt_input(payload.get("make"), max_length=120)
    safe_model = escape_prompt_input(payload.get("model"), max_length=120)
    safe_sub_model = escape_prompt_input(payload.get("sub_model"), max_length=120)
    safe_year = escape_prompt_input(payload.get("year"), max_length=10)
    safe_mileage = escape_prompt_input(payload.get("mileage_range") or payload.get("mileage_km"), max_length=50)
    safe_fuel = escape_prompt_input(payload.get("fuel_type"), max_length=50)
    safe_trans = escape_prompt_input(payload.get("transmission"), max_length=50)

    user_data = f"""יצרן: {safe_make}
דגם: {safe_model}
תת-דגם/תצורה: {safe_sub_model or 'לא צוין'}
שנה: {safe_year}
טווח קילומטראז׳: {safe_mileage or 'לא צוין'}
סוג דלק: {safe_fuel or 'לא צוין'}
תיבת הילוכים: {safe_trans or 'לא צוין'}"""

    bounded_user_data = wrap_user_input_in_boundary(user_data)
    data_instruction = create_data_only_instruction()
    missing_block = ", ".join(missing_info) if missing_info else "אין"

    return f"""
{data_instruction}

אתה מומחה לאמינות רכבים בישראל עם גישה לכלי Google Search. חובה להשתמש בכלי החיפוש (google_search tool) ולציין search_performed=true, search_queries בעברית, ו-sources עם קישורים.

החזר אובייקט JSON יחיד, ללא Markdown או טקסט חופשי:
{{
  "ok": true,
  "search_performed": true,
  "search_queries": ["שאילתות חיפוש בעברית"],
  "sources": ["קישורים או אובייקטים {{title,url,domain}}"],
  "score_breakdown": {{
    "engine_transmission_score": "מספר (1-10)",
    "electrical_score": "מספר (1-10)",
    "suspension_brakes_score": "מספר (1-10)",
    "maintenance_cost_score": "מספר (1-10)",
    "satisfaction_score": "מספר (1-10)",
    "recalls_score": "מספר (1-10)"
  }},
  "base_score_calculated": "מספר (0-100)",
  "common_issues": ["תקלות נפוצות רלוונטיות לק\"מ"],
  "avg_repair_cost_ILS": "מספר ממוצע",
  "issues_with_costs": [
    {{"issue": "שם התקלה", "avg_cost_ILS": "מספר", "source": "מקור", "severity": "נמוך/בינוני/גבוה"}}
  ],
  "reliability_summary": "סיכום מקצועי בעברית",
  "reliability_summary_simple": "הסבר פשוט וקצר בעברית",
  "recommended_checks": ["בדיקות מומלצות ספציפיות"],
  "common_competitors_brief": [
      {{"model": "שם מתחרה 1", "brief_summary": "אמינות בקצרה"}},
      {{"model": "שם מתחרה 2", "brief_summary": "אמינות בקצרה"}}
  ],
  "reliability_report": {{
    "overall_score": 0-100,
    "confidence": "high"|"medium"|"low",
    "one_sentence_verdict": "משפט החלטה קצר",
    "top_risks": [
      {{"risk_title": "", "why_it_matters": "", "how_to_check": "", "severity": "low|medium|high", "cost_impact": "low|medium|high"}}
    ],
    "expected_ownership_cost": {{"maintenance_level": "low|medium|high", "typical_yearly_range_ils": "", "notes": ""}},
    "buyer_checklist": {{
      "ask_seller": ["שאלות/מסמכים"],
      "inspection_focus": ["דגשים לבדיקת מוסך"],
      "walk_away_signs": ["דגלים אדומים לביטול עסקה"]
    }},
    "what_changes_with_mileage": [
      {{"mileage_band": "", "what_to_expect": ""}}
    ],
    "recommended_next_step": {{"action": "", "reason": ""}},
    "missing_info": ["פריטים שחסרים בקלט"]
  }}
}}

כל הערכים בעברית בלבד. אל תוסיף הסברים מחוץ ל-JSON. Missing info שסיפק המשתמש: {missing_block}

נתוני הקלט:
{bounded_user_data}
""".strip()


def parse_model_json(raw: str) -> Tuple[Optional[dict], Optional[str]]:
    if not raw:
        return None, "EMPTY_RESPONSE"
    try:
        return json.loads(raw), None
    except Exception:
        try:
            repaired = repair_json(raw)
            return json.loads(repaired), None
        except Exception:
            return None, "MODEL_JSON_INVALID"


def _execute_with_timeout(fn, timeout_sec: int):
    try:
        work_queue = getattr(AI_EXECUTOR, "_work_queue", None)
        if work_queue is not None:
            queued = work_queue.qsize()
            if queued >= AI_EXECUTOR_WORKERS:
                return None, "EXECUTOR_SATURATED"
        future = AI_EXECUTOR.submit(fn)
    except Exception:
        return None, "EXECUTOR_SATURATED"
    try:
        return future.result(timeout=timeout_sec), None
    except concurrent.futures.TimeoutError:
        # cancel() won't stop already-running work; it prevents callbacks, and any late response may keep the thread busy briefly
        future.cancel()
        return None, "CALL_TIMEOUT"
    except Exception as e:
        return None, e


def call_gemini_grounded_once(prompt: str) -> Tuple[Optional[dict], Optional[str]]:
    if extensions.ai_client is None:
        return None, "CLIENT_NOT_INITIALIZED"
    search_tool = genai_types.Tool(google_search=genai_types.GoogleSearch())
    config = genai_types.GenerateContentConfig(
        temperature=0.3,
        top_p=0.9,
        top_k=40,
        tools=[search_tool],
        response_mime_type="application/json",
    )
    def _invoke():
        return extensions.ai_client.models.generate_content(
            model=GEMINI3_MODEL_ID,
            contents=prompt,
            config=config,
        )
    resp, err = _execute_with_timeout(_invoke, AI_CALL_TIMEOUT_SEC)
    if err == "EXECUTOR_SATURATED":
        return None, "SERVER_BUSY"
    if err == "CALL_TIMEOUT":
        return None, "CALL_TIMEOUT"
    if isinstance(err, Exception):
        return None, f"CALL_FAILED:{type(err).__name__}"
    if resp is None:
        return None, "CALL_FAILED:EMPTY"
    text = (getattr(resp, "text", "") or "").strip()
    parsed, err = parse_model_json(text)
    return parsed, err


# ======================================================
# === 3b. Car Advisor – פונקציות עזר (Gemini 3 Pro) ===
# ======================================================

fuel_map = {
    "בנזין": "gasoline",
    "היברידי": "hybrid",
    "דיזל היברידי": "hybrid-diesel",
    "דיזל": "diesel",
    "חשמלי": "electric",
}
gear_map = {
    "אוטומטית": "automatic",
    "ידנית": "manual",
}
turbo_map = {
    "לא משנה": "any",
    "כן": "yes",
    "לא": "no",
}

fuel_map_he = {v: k for k, v in fuel_map.items()}
gear_map_he = {v: k for k, v in gear_map.items()}
turbo_map_he = {
    "yes": "כן",
    "no": "לא",
    "any": "לא משנה",
    True: "כן",
    False: "לא",
}


def make_user_profile(
    budget_min,
    budget_max,
    years_range,
    fuels,
    gears,
    turbo_required,
    main_use,
    annual_km,
    driver_age,
    family_size,
    cargo_need,
    safety_required,
    trim_level,
    weights,
    body_style,
    driving_style,
    excluded_colors,
):
    return {
        "budget_nis": [float(budget_min), float(budget_max)],
        "years": [int(years_range[0]), int(years_range[1])],
        "fuel": [f.lower() for f in fuels],
        "gear": [g.lower() for g in gears],
        "turbo_required": None if turbo_required == "any" else (turbo_required == "yes"),
        "main_use": main_use.strip(),
        "annual_km": int(annual_km),
        "driver_age": int(driver_age),
        "family_size": family_size,
        "cargo_need": cargo_need,
        "safety_required": safety_required,
        "trim_level": trim_level,
        "weights": weights,
        "body_style": body_style,
        "driving_style": driving_style,
        "excluded_colors": excluded_colors,
    }


def sanitize_profile_for_prompt(profile: dict) -> dict:
    """Recursively escape user profile fields before prompt construction."""
    if isinstance(profile, dict):
        return {k: sanitize_profile_for_prompt(v) for k, v in profile.items()}
    if isinstance(profile, list):
        return [sanitize_profile_for_prompt(v) for v in profile]
    if isinstance(profile, str):
        return escape_prompt_input(profile, max_length=300)
    return profile


def car_advisor_call_gemini_with_search(profile: dict) -> dict:
    """
    קריאה ל-Gemini 3 Pro (SDK החדש) עם Google Search ו-output כ-JSON בלבד.
    """
    if extensions.advisor_client is None:
        return {"_error": "Gemini Car Advisor client unavailable."}

    sanitized_profile = sanitize_profile_for_prompt(profile)
    bounded_profile = wrap_user_input_in_boundary(
        json.dumps(sanitized_profile, ensure_ascii=False, indent=2),
        boundary_tag="user_input"
    )
    data_instruction = create_data_only_instruction()

    prompt = f"""
{data_instruction}

Please recommend cars for an Israeli customer. Here is the user profile (JSON wrapped in <user_input>):
{bounded_profile}

You are an independent automotive data analyst for the **Israeli used car market**.

🔴 CRITICAL INSTRUCTION: USE GOOGLE SEARCH TOOL
You MUST use the Google Search tool to verify:
- that the specific model and trim are actually sold in Israel
- realistic used prices in Israel (NIS)
- realistic fuel/energy consumption values
- common issues (DSG, reliability, recalls)

Hard constraints:
- Return only ONE top-level JSON object.
- JSON fields: "search_performed", "search_queries", "recommended_cars".
- search_performed: ALWAYS true (boolean).
- search_queries: array of the real Hebrew queries you would run in Google (max 6).
- All numeric fields must be pure numbers (no units, no text).

recommended_cars: array of 5–10 cars. EACH car MUST include:
  - brand
  - model
  - year
  - fuel
  - gear
  - turbo
  - engine_cc
  - price_range_nis
  - avg_fuel_consumption (+ fuel_method):
      * non-EV: km per liter (number only)
      * EV: kWh per 100 km (number only)
  - annual_fee (₪/year, number only) + fee_method
  - reliability_score (1–10, number only) + reliability_method
  - maintenance_cost (₪/year, number only) + maintenance_method
  - safety_rating (1–10, number only) + safety_method
  - insurance_cost (₪/year, number only) + insurance_method
  - resale_value (1–10, number only) + resale_method
  - performance_score (1–10, number only) + performance_method
  - comfort_features (1–10, number only) + comfort_method
  - suitability (1–10, number only) + suitability_method
  - market_supply ("גבוה" / "בינוני" / "נמוך") + supply_method
  - fit_score (0–100, number only)
  - comparison_comment (Hebrew)
  - not_recommended_reason (Hebrew or null)

**All explanation fields (all *_method, comparison_comment, not_recommended_reason) MUST be in clean, easy Hebrew.**

IMPORTANT MARKET REALITY:
- לפני שאתה בוחר רכבים, תבדוק בזהירות בעזרת החיפוש שדגם כזה אכן נמכר בישראל, בתצורת מנוע וגיר שאתה מציג.
- אל תמציא דגמים או גרסאות שלא קיימים ביד 2 בישראל.
- מודלים שלא נמכרו כמעט / אין להם היצע – סמן "market_supply": "נמוך" והסבר בעברית.

Return ONLY raw JSON. Do not add any backticks or explanation text.
"""

    search_tool = genai_types.Tool(
        google_search=genai_types.GoogleSearch()
    )

    config = genai_types.GenerateContentConfig(
        temperature=0.3,
        top_p=0.9,
        top_k=40,
        tools=[search_tool],
        response_mime_type="application/json",
    )

    def _invoke():
        return extensions.advisor_client.models.generate_content(
            model=GEMINI3_MODEL_ID,
            contents=prompt,
            config=config,
        )
    resp, err = _execute_with_timeout(_invoke, AI_CALL_TIMEOUT_SEC)
    if err == "EXECUTOR_SATURATED":
        return {"_error": "SERVER_BUSY"}
    if err == "CALL_TIMEOUT":
        return {"_error": "CALL_TIMEOUT"}
    if isinstance(err, Exception):
        return {"_error": f"Gemini Car Advisor call failed: {err}"}
    if resp is None:
        return {"_error": "Gemini Car Advisor call failed: EMPTY"}
    text = getattr(resp, "text", "") or ""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_error": "JSON decode error from Gemini Car Advisor", "_raw": text}


def car_advisor_postprocess(profile: dict, parsed: dict) -> dict:
    """
    מקבל profile + פלט גולמי מג'מיני, מחשב עלויות שנתיות,
    ממפה ערכים לעברית ומחזיר אובייקט JSON מוכן ל-frontend.
    """
    recommended = parsed.get("recommended_cars") or []
    if not isinstance(recommended, list) or not recommended:
        return {
            "search_performed": parsed.get("search_performed", False),
            "search_queries": parsed.get("search_queries", []),
            "recommended_cars": [],
        }

    annual_km = profile.get("annual_km", 15000)
    fuel_price = profile.get("fuel_price_nis_per_liter", 7.0)
    elec_price = profile.get("electricity_price_nis_per_kwh", 0.65)

    processed = []
    for car in recommended:
        if not isinstance(car, dict):
            continue
        car = dict(car)  # copy

        fuel_val = str(car.get("fuel", "")).strip()
        gear_val = str(car.get("gear", "")).strip()
        turbo_val = car.get("turbo")

        if fuel_val in fuel_map:
            fuel_norm = fuel_map[fuel_val]
        else:
            fuel_norm = fuel_val.lower()

        if gear_val in gear_map:
            gear_norm = gear_map[gear_val]
        else:
            gear_norm = gear_val.lower()

        if isinstance(turbo_val, str):
            turbo_norm = turbo_map.get(turbo_val, turbo_val)
        else:
            turbo_norm = turbo_val

        avg_fc = car.get("avg_fuel_consumption")
        try:
            avg_fc_num = float(avg_fc)
            if avg_fc_num <= 0:
                avg_fc_num = None
        except Exception:
            avg_fc_num = None

        annual_energy_cost = None
        if avg_fc_num is not None:
            if fuel_norm == "electric":
                annual_energy_cost = (annual_km / 100.0) * avg_fc_num * elec_price
            else:
                annual_energy_cost = (annual_km / avg_fc_num) * fuel_price

        def as_float(x):
            try:
                return float(x)
            except Exception:
                return 0.0

        maintenance_cost = as_float(car.get("maintenance_cost"))
        insurance_cost = as_float(car.get("insurance_cost"))
        annual_fee = as_float(car.get("annual_fee"))

        if annual_energy_cost is not None:
            total_annual_cost = annual_energy_cost + maintenance_cost + insurance_cost + annual_fee
        else:
            total_annual_cost = None

        car["annual_energy_cost"] = round(annual_energy_cost, 0) if annual_energy_cost is not None else None
        car["annual_fuel_cost"] = car["annual_energy_cost"]
        car["maintenance_cost"] = round(maintenance_cost, 0)
        car["insurance_cost"] = round(insurance_cost, 0)
        car["annual_fee"] = round(annual_fee, 0)
        car["total_annual_cost"] = round(total_annual_cost, 0) if total_annual_cost is not None else None

        car["fuel"] = fuel_map_he.get(fuel_norm, fuel_val or fuel_norm)
        car["gear"] = gear_map_he.get(gear_norm, gear_val or gear_norm)
        car["turbo"] = turbo_map_he.get(turbo_norm, turbo_val)

        processed.append(car)

    return {
        "search_performed": parsed.get("search_performed", False),
        "search_queries": parsed.get("search_queries", []),
        "recommended_cars": processed,
    }


def _get_today_bounds(tz: ZoneInfo) -> Tuple[datetime, datetime]:
    """Backward-compatible helper, now timezone-aware."""
    _, start, end, _, _, _ = compute_quota_window(tz)
    return start, end


def get_daily_quota_usage(user_id: int, day_key: date) -> int:
    """Return today's usage count without mutating state."""
    quota = DailyQuotaUsage.query.filter_by(user_id=user_id, day=day_key).first()
    return quota.count if quota else 0


def cleanup_expired_reservations(user_id: int, day_key: date, now_utc: Optional[datetime] = None) -> int:
    """
    Remove stale reservations that were never finalized to avoid blocking quota.
    """
    now = now_utc or datetime.utcnow()
    expire_before = now - timedelta(seconds=QUOTA_RESERVATION_TTL_SECONDS)
    deleted = (
        db.session.query(QuotaReservation)
        .filter(
            QuotaReservation.user_id == user_id,
            QuotaReservation.day == day_key,
            QuotaReservation.status == "reserved",
            QuotaReservation.created_at < expire_before,
        )
        .delete(synchronize_session=False)
    )
    # Optional cleanup of already released/consumed rows older than TTL to control growth
    db.session.query(QuotaReservation).filter(
        QuotaReservation.user_id == user_id,
        QuotaReservation.day < (day_key - timedelta(days=7))
    ).delete(synchronize_session=False)
    return deleted


def _get_or_create_quota_row(user_id: int, day_key: date, now_utc: datetime) -> DailyQuotaUsage:
    bind = db.session.get_bind()
    dialect_name = bind.dialect.name if bind else ""
    base_values = {"user_id": user_id, "day": day_key, "count": 0, "updated_at": now_utc}

    try:
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            stmt = pg_insert(DailyQuotaUsage).values(**base_values).on_conflict_do_nothing(
                constraint="uq_user_day_quota_usage"
            )
            db.session.execute(stmt)
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            stmt = sqlite_insert(DailyQuotaUsage).values(**base_values).on_conflict_do_nothing(
                index_elements=["user_id", "day"]
            )
            db.session.execute(stmt)
    except IntegrityError:
        db.session.rollback()
    except SQLAlchemyError:
        db.session.rollback()

    try:
        quota = (
            db.session.query(DailyQuotaUsage)
            .filter_by(user_id=user_id, day=day_key)
            .with_for_update()
            .first()
        )
    except SQLAlchemyError:
        db.session.rollback()
        quota = (
            db.session.query(DailyQuotaUsage)
            .filter_by(user_id=user_id, day=day_key)
            .first()
        )
    if quota is None:
        quota = DailyQuotaUsage(user_id=user_id, day=day_key, count=0, updated_at=now_utc)
        db.session.add(quota)
        db.session.flush()
    return quota


def reserve_daily_quota(user_id: int, day_key: date, limit: int, request_id: str, now_utc: Optional[datetime] = None) -> Tuple[bool, int, int, Optional[int]]:
    """
    Reserve a quota slot (reserved -> finalize or release).

    Returns:
        allowed (bool): whether reservation succeeded
        consumed_count (int): already consumed count
        reserved_count (int): active reserved count AFTER this call (if allowed)
        reservation_id (int|None): id of created reservation if allowed
    """
    now = now_utc or datetime.utcnow()
    try:
        try:
            cleanup_expired_reservations(user_id, day_key, now)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logging.getLogger(__name__).warning(
                "[QUOTA] cleanup_expired_reservations failed for user=%s day=%s",
                user_id,
                day_key,
            )
        with db.session.begin_nested():
            quota = _get_or_create_quota_row(user_id, day_key, now)
            consumed_count = quota.count

            active_reserved = (
                db.session.query(QuotaReservation)
                .filter_by(user_id=user_id, day=day_key, status="reserved")
                .count()
            )

            if active_reserved >= MAX_ACTIVE_RESERVATIONS:
                db.session.rollback()
                return False, consumed_count, active_reserved, None

            if (consumed_count + active_reserved) >= limit:
                db.session.rollback()
                return False, consumed_count, active_reserved, None

            reservation = QuotaReservation(
                user_id=user_id,
                day=day_key,
                status="reserved",
                request_id=request_id,
                created_at=now,
                updated_at=now,
            )
            db.session.add(reservation)
            db.session.flush()
            reservation_id = reservation.id

        db.session.commit()
        return True, consumed_count, active_reserved + 1, reservation_id
    except SQLAlchemyError as e:
        db.session.rollback()
        logging.getLogger(__name__).exception("[QUOTA] Reservation failed for user %s", user_id)
        raise QuotaInternalError() from e


def finalize_quota_reservation(reservation_id: Optional[int], user_id: int, day_key: date, now_utc: Optional[datetime] = None) -> Tuple[bool, int]:
    """
    Mark reservation as consumed and increment quota counter.
    """
    if not reservation_id:
        return False, get_daily_quota_usage(user_id, day_key)
    now = now_utc or datetime.utcnow()
    try:
        with db.session.begin_nested():
            reservation = (
                db.session.query(QuotaReservation)
                .filter_by(id=reservation_id, user_id=user_id, day=day_key)
                .with_for_update()
                .first()
            )
            if not reservation or reservation.status != "reserved":
                db.session.rollback()
                return False, get_daily_quota_usage(user_id, day_key)

            quota = _get_or_create_quota_row(user_id, day_key, now)
            quota.count += 1
            quota.updated_at = now

            reservation.status = "consumed"
            reservation.updated_at = now

        db.session.commit()
        return True, quota.count
    except SQLAlchemyError as e:
        print(f"[QUOTA] ❌ Finalize failed for user {user_id}: {type(e).__name__}")
        db.session.rollback()
        return False, get_daily_quota_usage(user_id, day_key)


def release_quota_reservation(reservation_id: Optional[int], user_id: int, day_key: date, now_utc: Optional[datetime] = None) -> bool:
    """
    Release a reservation (refund quota) if it was still reserved.
    """
    if not reservation_id:
        return False
    now = now_utc or datetime.utcnow()
    try:
        with db.session.begin_nested():
            reservation = (
                db.session.query(QuotaReservation)
                .filter_by(id=reservation_id, user_id=user_id, day=day_key)
                .with_for_update()
                .first()
            )
            if reservation and reservation.status == "reserved":
                reservation.status = "released"
                reservation.updated_at = now
        db.session.commit()
        return True
    except SQLAlchemyError as e:
        print(f"[QUOTA] ⚠️ Release failed for user {user_id}: {type(e).__name__}")
        db.session.rollback()
        return False


def get_client_ip() -> str:
    """Resolve client IP from X-Forwarded-For or remote_addr."""
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    ip = xff or (request.remote_addr or "")
    return ip[:64] if ip else "unknown"


# ======================================================
# === Phase 1E: Atomic Quota Enforcement ===
# ======================================================

def check_and_increment_daily_quota(user_id: int, limit: int, day_key: date, now_utc: Optional[datetime] = None) -> Tuple[bool, int]:
    """
    Atomically check and increment the daily quota for a user.
    
    Phase 1E: Race-safe quota enforcement using DailyQuotaUsage table with unique constraint.
    
    Args:
        user_id: The user's ID
        limit: The daily limit (e.g., USER_DAILY_LIMIT)
        
    Returns:
        Tuple of (allowed: bool, current_count: int)
        - allowed: True if within quota (incremented), False if quota exceeded
        - current_count: The count AFTER increment (if allowed) or current count (if rejected)
    """

    now = now_utc or datetime.utcnow()

    try:
        with db.session.begin_nested():
            try:
                quota = (
                    db.session.query(DailyQuotaUsage)
                    .filter_by(user_id=user_id, day=day_key)
                    .with_for_update()
                    .first()
                )
            except SQLAlchemyError:
                quota = (
                    db.session.query(DailyQuotaUsage)
                    .filter_by(user_id=user_id, day=day_key)
                    .first()
                )

            if quota is None:
                quota = DailyQuotaUsage(user_id=user_id, day=day_key, count=0, updated_at=now)
                db.session.add(quota)
                db.session.flush()

            if quota.count >= limit:
                db.session.rollback()
                return False, quota.count

            quota.count += 1
            quota.updated_at = now

        db.session.commit()
        return True, quota.count

    except IntegrityError:
        db.session.rollback()
        # Retry once after handling potential race on insert
        try:
            with db.session.begin_nested():
                quota = (
                    db.session.query(DailyQuotaUsage)
                    .filter_by(user_id=user_id, day=day_key)
                    .with_for_update()
                    .first()
                )

                if quota is None:
                    quota = DailyQuotaUsage(user_id=user_id, day=day_key, count=0, updated_at=now)
                    db.session.add(quota)
                    db.session.flush()

                if quota.count >= limit:
                    db.session.rollback()
                    return False, quota.count

                quota.count += 1
                quota.updated_at = now

            db.session.commit()
            return True, quota.count
        except SQLAlchemyError as e:
            print(f"[QUOTA] ❌ Error after retry for user {user_id}: {type(e).__name__}")
            db.session.rollback()
            return False, 0

    except SQLAlchemyError as e:
        # Unexpected error, log and deny to be safe
        print(f"[QUOTA] ❌ Error checking quota for user {user_id}: {type(e).__name__}")
        db.session.rollback()
        return False, 0


def check_and_increment_ip_rate_limit(ip: str, limit: int = 20, now_utc: Optional[datetime] = None) -> Tuple[bool, int, datetime]:
    """
    Atomically enforce per-IP minute window limit.
    """
    now = now_utc or datetime.utcnow()
    window_start = now.replace(second=0, microsecond=0)
    resets_at = window_start + timedelta(minutes=1)
    cleanup_before = window_start - timedelta(days=1)

    def _increment_record() -> Tuple[bool, int]:
        # Cleanup old buckets to avoid unbounded growth (best-effort, same transaction).
        db.session.query(IpRateLimit).filter(IpRateLimit.window_start < cleanup_before).delete(synchronize_session=False)

        # Try dialect upsert to avoid duplicate inserts under concurrency
        bind = db.session.get_bind()
        dialect_name = bind.dialect.name if bind else ""
        base_values = {"ip": ip, "window_start": window_start, "count": 1, "updated_at": now}
        try:
            if dialect_name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                stmt = (
                    pg_insert(IpRateLimit)
                    .values(**base_values)
                    .on_conflict_do_update(
                        index_elements=["ip", "window_start"],
                        set_={"count": IpRateLimit.__table__.c.count + 1, "updated_at": now},
                    )
                    .returning(IpRateLimit.count)
                )
                result = db.session.execute(stmt)
                new_count = result.scalar_one()
            elif dialect_name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                stmt = (
                    sqlite_insert(IpRateLimit)
                    .values(**base_values)
                    .on_conflict_do_update(
                        index_elements=["ip", "window_start"],
                        set_={"count": IpRateLimit.__table__.c.count + 1, "updated_at": now},
                    )
                )
                db.session.execute(stmt)
                record = (
                    db.session.query(IpRateLimit)
                    .filter_by(ip=ip, window_start=window_start)
                    .first()
                )
                new_count = record.count if record else 0
            else:
                raise SQLAlchemyError("dialect_upsert_not_supported")

            if new_count > limit:
                db.session.rollback()
                record = (
                    db.session.query(IpRateLimit)
                    .filter_by(ip=ip, window_start=window_start)
                    .first()
                )
                current_count = record.count if record else limit
                return False, current_count
            return True, new_count
        except IntegrityError:
            db.session.rollback()
        except SQLAlchemyError:
            # Fallback to legacy lock-based approach if dialect upsert unavailable
            db.session.rollback()

        try:
            record = (
                db.session.query(IpRateLimit)
                .filter_by(ip=ip, window_start=window_start)
                .with_for_update()
                .first()
            )
        except SQLAlchemyError:
            db.session.rollback()
            record = (
                db.session.query(IpRateLimit)
                .filter_by(ip=ip, window_start=window_start)
                .first()
            )

        if record is None:
            record = IpRateLimit(ip=ip, window_start=window_start, count=0, updated_at=now)
            db.session.add(record)
            db.session.flush()

        if record.count >= limit:
            db.session.rollback()
            return False, record.count

        record.count += 1
        record.updated_at = now
        return True, record.count

    try:
        with db.session.begin_nested():
            ok, count = _increment_record()
            if not ok:
                return False, count, resets_at

        db.session.commit()
        return True, count, resets_at
    except IntegrityError:
        db.session.rollback()
        try:
            with db.session.begin_nested():
                ok, count = _increment_record()
                if not ok:
                    return False, count, resets_at

            db.session.commit()
            return True, count, resets_at
        except Exception:
            db.session.rollback()
            return False, 0, resets_at


def rollback_quota_increment(user_id: int, day_key: date) -> int:
    """
    Roll back a previously recorded quota increment (best-effort).
    """
    try:
        with db.session.begin_nested():
            quota = (
                db.session.query(DailyQuotaUsage)
                .filter_by(user_id=user_id, day=day_key)
                .with_for_update()
                .first()
            )
            if quota and quota.count > 0:
                quota.count -= 1
                quota.updated_at = datetime.utcnow()
                current = quota.count
            else:
                current = quota.count if quota else 0
        db.session.commit()
        return current
    except SQLAlchemyError as e:
        print(f"[QUOTA] ❌ rollback failed for user {user_id}: {type(e).__name__}")
        db.session.rollback()
        return 0


# ========================================
# ===== ★★★ 4. פונקציית ה-Factory ★★★ =====
# ========================================
def create_app():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    templates_dir = os.path.join(base_dir, "templates")
    static_dir = os.path.join(base_dir, "static")
    app = Flask(__name__, template_folder=templates_dir, static_folder=static_dir)
    
    # Phase 2K: Configure Python logging (structured logging to stdout)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logger = logging.getLogger(__name__)
    canonical_base = os.environ.get("CANONICAL_BASE_URL", "https://yedaarechev.com").strip().rstrip("/")
    if not canonical_base:
        canonical_base = "https://yedaarechev.com"
    parsed_canonical = urlparse(canonical_base)
    canonical_host = (parsed_canonical.hostname or canonical_base.replace("https://", "").replace("http://", "")).lower()
    app.config["CANONICAL_BASE_URL"] = canonical_base
    app.config["CANONICAL_HOST"] = canonical_host
    app_tz, app_tz_name = resolve_app_timezone()
    app.config["APP_TZ"] = app_tz_name
    app.config["APP_TZ_OBJ"] = app_tz
    logger.info(f"APP_TZ configured as {app_tz_name}")
    
    # Phase 2I: ProxyFix parameterization (Render + Cloudflare chain)
    # Cloudflare -> Render proxy chain typically needs x_for=1, x_proto=1, x_host=1
    # Can be overridden via TRUSTED_PROXY_COUNT env var if proxy chain changes
    trusted_proxy_count = int(os.environ.get("TRUSTED_PROXY_COUNT", "1"))
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=trusted_proxy_count,
        x_proto=trusted_proxy_count,
        x_host=trusted_proxy_count,
        x_prefix=0  # not using path prefix
    )
    logger.info(f"ProxyFix configured with trusted_proxy_count={trusted_proxy_count}")

    # ---- בעל מערכת (למנוע ההמלצות) ----
    OWNER_EMAILS = parse_owner_emails(os.environ.get("OWNER_EMAILS", ""))
    OWNER_BYPASS_QUOTA = os.environ.get("OWNER_BYPASS_QUOTA", "1").lower() in ("1", "true", "yes")
    ADVISOR_OWNER_ONLY = os.environ.get("ADVISOR_OWNER_ONLY", "1").lower() in ("1", "true", "yes")
    
    # Store config values for helper functions to access
    app.config['OWNER_EMAILS'] = OWNER_EMAILS
    app.config['CANONICAL_BASE'] = canonical_base
    app.config['OWNER_BYPASS_QUOTA'] = OWNER_BYPASS_QUOTA
    app.config['ADVISOR_OWNER_ONLY'] = ADVISOR_OWNER_ONLY
    app.config['PER_IP_PER_MIN_LIMIT'] = PER_IP_PER_MIN_LIMIT
    app.config['QUOTA_RESERVATION_TTL_SECONDS'] = QUOTA_RESERVATION_TTL_SECONDS

    # Helper functions (is_owner_user, api_ok, api_error, get_request_id, get_redirect_uri)
    # are now imported from app.utils.http_helpers and used throughout the code

    @app.before_request
    def assign_request_id_and_redirect():
        """
        Assign a request_id + start_time early and enforce canonical host redirect.
        """
        from flask import g
        if not getattr(g, "request_id", None):
            g.request_id = str(uuid.uuid4())
        g.start_time = pytime.perf_counter()

        host = (request.host or "").lower()
        # Preserve port if present (e.g., local dev)
        host_parts = host.split(":")
        hostname_only = host_parts[0]
        port_part = f":{host_parts[1]}" if len(host_parts) > 1 else ""
        if canonical_host and hostname_only == f"www.{canonical_host}":
            target_host = canonical_host + port_part
            parsed = urlparse(request.url)
            redirect_url = parsed._replace(netloc=target_host).geturl()
            return redirect(redirect_url, code=301)

    @app.context_processor
    def inject_template_globals():
        return {
            "is_logged_in": current_user.is_authenticated,
            "current_user": current_user,
            "is_owner": is_owner_user(),
        }

    # Phase 2G: Allowed hosts validation
    ALLOWED_HOSTS = set()
    allowed_hosts_env = os.environ.get("ALLOWED_HOSTS", "").strip()
    if allowed_hosts_env:
        ALLOWED_HOSTS = {h.strip().lower() for h in allowed_hosts_env.split(",") if h.strip()}
    else:
        # Default allowed hosts for production
        ALLOWED_HOSTS = {
            "yedaarechev.com",
            "yedaarechev.onrender.com",
            "localhost",
            "127.0.0.1",
        }
    if canonical_host:
        ALLOWED_HOSTS.add(canonical_host.lower())
    print(f"[BOOT] Allowed hosts: {ALLOWED_HOSTS}")
    
    def is_host_allowed(host: str) -> bool:
        """Check if the given host is in the allowed hosts list."""
        if not host:
            return False
        # Strip port if present
        host_no_port = host.split(":")[0].lower()
        return host_no_port in ALLOWED_HOSTS

    # ======================
    # ✅ Render DB hard-fail
    # ======================
    db_url = os.environ.get("DATABASE_URL", "").strip()
    secret_key = os.environ.get("SECRET_KEY", "").strip()

    # Normalize deprecated prefix for SQLAlchemy
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    # If running on Render, refuse to boot without DATABASE_URL
    is_render = os.environ.get("RENDER", "").strip() != ""
    if is_render and not db_url:
        raise RuntimeError(
            "DATABASE_URL is missing on Render. "
            "Set DATABASE_URL (Internal Postgres URL) in Render Environment Variables."
        )
    if is_render and not secret_key:
        raise RuntimeError(
            "SECRET_KEY is missing on Render. "
            "Set SECRET_KEY in Render Environment Variables."
        )

    # Config
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url if db_url else "sqlite:///:memory:"
    app.config["SECRET_KEY"] = secret_key if secret_key else "dev-secret-key-that-is-not-secret"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    
    # ===== SECURITY: MAX_CONTENT_LENGTH (Phase 1D: DoS prevention) =====
    # Limit request payload size (64 KB) to cap JSON bodies and prevent memory exhaustion attacks
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # 64 KB hard cap

    # ===== SECURITY:  Session Cookie Configuration (Tier 1) =====
    app.config["SESSION_COOKIE_SECURE"] = bool(is_render)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # ===== FIX A: SQLAlchemy Connection Pool (prevents stale connections) =====
    # pool_pre_ping:  test connection before reusing from pool
    # pool_recycle:  recycle connections after 240 seconds (Postgres timeout ~300s)
    # pool_size: base number of connections per worker
    # max_overflow: additional connections when base is exhausted
    if db_url and "postgresql" in db_url:
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_pre_ping": True,
            "pool_recycle": 240,
            "pool_size": 5,
            "max_overflow": 10,
            "connect_args": {"connect_timeout": 10, "sslmode": "prefer"}
        }
        print("[BOOT] SQLAlchemy configured with pool_pre_ping=True, pool_recycle=240")

    if not db_url:
        print("[BOOT] ⚠️ DATABASE_URL not set. Using in-memory sqlite (LOCAL DEV ONLY).")
    if not secret_key:
        print("[BOOT] ⚠️ SECRET_KEY not set. Using dev fallback (LOCAL DEV ONLY).")

    if db_url:
        parsed_db_url = urlparse(db_url)
        safe_host = parsed_db_url.hostname or ""
        safe_port = f":{parsed_db_url.port}" if parsed_db_url.port else ""
        safe_db = (parsed_db_url.path or "").lstrip("/")
        logger.info("[DB] DATABASE host=%s%s db=%s", safe_host, safe_port, safe_db or "(default)")
    else:
        logger.info("[DB] DATABASE_URL not provided; using sqlite fallback")

    # Init
    db.init_app(app)
    login_manager.init_app(app)
    oauth.init_app(app)
    migrate.init_app(app, db)

    with app.app_context():
        ensure_search_history_cache_key(app, db, logger)
        ensure_duration_ms_columns(db.engine, logger)
        try:
            with db.engine.connect() as conn:
                context = MigrationContext.configure(conn)
                current_rev = context.get_current_revision()
            has_duration_ms = False
            with db.engine.connect() as conn:
                inspector = inspect(conn)
                if inspector.has_table("search_history"):
                    cols = [col["name"] for col in inspector.get_columns("search_history")]
                    has_duration_ms = "duration_ms" in cols
            logger.info(
                "[DB] Alembic revision: %s (duration_ms column: %s)",
                current_rev or "(none)",
                "present" if has_duration_ms else "missing",
            )
        except Exception:
            logger.exception("[DB] Alembic revision check failed")

    login_manager.login_view = 'public.login'
    
    # Phase 2G: Host header validation middleware
    @app.before_request
    def validate_host_header():
        """Validate the Host header to prevent host header injection attacks."""
        host = request.host
        if not is_host_allowed(host):
            logger.warning(f"[SECURITY] Invalid host header: {host}")
            # For API routes, return JSON error
            if request.is_json or request.accept_mimetypes.accept_json or request.path.startswith(('/analyze', '/advisor_api', '/search-details')):
                return api_error("invalid_host", "Invalid host header", status=400)
            return "Invalid host header", 400
    
    # Phase 2H: Origin/Referer protection for session-auth POST endpoints (CSRF-safe without tokens)
    @app.before_request
    def check_origin_referer_for_posts():
        """
        Validate Origin or Referer header for session-based POST endpoints.
        This provides CSRF protection without requiring CSRF tokens in fetch() calls.
        """
        # Only check POST requests to session-authenticated endpoints
        if request.method != 'POST':
            return None
        
        # Only check specific endpoints (not login/auth which may come from external OAuth flow)
        protected_paths = ['/analyze', '/advisor_api', '/api/account/delete']
        if not any(request.path.startswith(p) for p in protected_paths):
            return None

        def _forbidden_response():
            if request.path.startswith("/api/account/delete"):
                rid = get_request_id()
                resp = jsonify({"error": "forbidden", "request_id": rid})
                resp.status_code = 403
                resp.headers["X-Request-ID"] = rid
                return resp
            return api_error("forbidden_origin", "Request from unauthorized origin", status=403)

        # Get Origin or Referer header
        origin = request.headers.get('Origin')
        referer = request.headers.get('Referer')
        
        # Extract host from origin or referer
        if origin:
            # Origin format: https://example.com or https://example.com:port
            try:
                parsed = urlparse(origin)
                origin_host = parsed.netloc or parsed.hostname
            except Exception:
                origin_host = None
        else:
            origin_host = None
        
        if referer and not origin_host:
            # Referer format: https://example.com/path
            try:
                parsed = urlparse(referer)
                origin_host = parsed.netloc or parsed.hostname
            except Exception:
                origin_host = None
        
        if not origin_host:
            logger.warning(f"[CSRF] POST to {request.path} with no Origin/Referer header")
            return _forbidden_response()

        host_no_port = origin_host.split(':')[0].lower() if ':' in origin_host else origin_host.lower()
        if host_no_port not in ALLOWED_HOSTS:
            logger.warning(f"[CSRF] Blocked POST to {request.path} from disallowed origin: {origin_host}")
            return _forbidden_response()
        
        return None

    # Handle unauthorized access for AJAX/JSON requests
    @login_manager.unauthorized_handler
    def unauthorized():
        """Return 401 for AJAX/JSON requests, otherwise redirect to login."""
        if request.is_json or request.accept_mimetypes.accept_json:
            if request.path.startswith("/api/account/delete"):
                rid = get_request_id()
                resp = jsonify({"error": "unauthorized", "message": "Login required", "request_id": rid})
                resp.status_code = 401
                resp.headers["X-Request-ID"] = rid
                return resp
            log_rejection("unauthenticated", "User not logged in, no valid session")
            return api_error("unauthenticated", "אנא התחבר כדי להשתמש בשירות זה", status=401)
        return redirect(url_for('public.login'))

    @app.before_request
    def log_request_metadata():
        """
        Phase 2K: Log request metadata (request_id assigned earlier).
        """
        from flask import g
        request_id = getattr(g, "request_id", str(uuid.uuid4()))
        g.request_id = request_id

        xfp = request.headers.get("X-Forwarded-Proto", "")
        xff = request.headers.get("X-Forwarded-For", "")
        auth_state = current_user.is_authenticated
        path = request.path or ""
        
        if not (path.startswith("/static/") or path == "/favicon.ico"):
            # Phase 2K: Use logger instead of print
            logger.info(
                f"[REQ] request_id={request_id} {request.method} {path} "
                f"host={request.host} scheme={request.scheme} xfp={xfp} xff={xff} auth={auth_state}"
            )

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_too_large(e):
        return api_error("payload_too_large", "Payload exceeds limit", status=413, details={"field": "payload"})

    @app.after_request
    def apply_security_headers(response):
        rid = get_request_id()
        response.headers.setdefault("X-Request-ID", rid)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=()"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        response.headers.setdefault(
            "Content-Security-Policy-Report-Only",
            # Report-Only until inline scripts are moved or nonces are added.
            "default-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://fonts.googleapis.com https://fonts.gstatic.com data:; "
            "img-src 'self' data: https://*.googleusercontent.com; "
            "connect-src 'self' https://accounts.google.com https://www.googleapis.com https://openidconnect.googleapis.com https://generativelanguage.googleapis.com"
        )
        # CSP enforcement plan:
        # 1) Move inline scripts/styles to static files or add nonces.
        # 2) Serve Tailwind locally (remove CDN dependency).
        # 3) Flip to enforced Content-Security-Policy header and drop 'unsafe-inline'.
        if is_render or request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")

        # Structured-ish response log with duration
        try:
            from flask import g
            duration_ms = None
            if hasattr(g, "start_time"):
                duration_ms = (pytime.perf_counter() - g.start_time) * 1000
            user_id = current_user.id if current_user.is_authenticated else "anonymous"
            if not (request.path.startswith("/static/") or request.path == "/favicon.ico"):
                logger.info(
                    f"[RESP] request_id={rid} method={request.method} path={request.path} "
                    f"status={response.status_code} duration_ms={(duration_ms or 0):.2f} user={user_id}"
                )
        except Exception:
            pass
        return response

    @app.after_request
    def apply_cache_control(response):
        path = request.path or ""
        if path.startswith("/static/") or path == "/favicon.ico":
            return response
        if path == "/dashboard" or path.startswith("/api/history/") or path.startswith("/api/account/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.teardown_request
    def teardown_request_handler(exc):
        try:
            db.session.rollback()
        except Exception:
            logger.exception("[DB] teardown rollback failed")
        finally:
            db.session.remove()

    # ==========================
    # ✅ Run create_all ONLY ONCE
    # ==========================
    with app.app_context():
        try:
            lock_path = "/tmp/.db_inited.lock"
            inspector = inspect(db.engine)
            quota_usage_exists = inspector.has_table('daily_quota_usage')
            ip_rate_limit_exists = inspector.has_table('ip_rate_limit')
            quota_reservation_exists = inspector.has_table('quota_reservation')
            if is_render:
                print("[DB] ⏭️ Render detected - skipping db.create_all(); run `flask db upgrade` via release/preDeploy")
            elif os.environ.get("SKIP_CREATE_ALL", "").lower() in ("1", "true", "yes"):
                print("[DB] ⏭️ SKIP_CREATE_ALL enabled - skipping db.create_all()")
            elif os.path.exists(lock_path) and quota_usage_exists and ip_rate_limit_exists and quota_reservation_exists:
                print("[DB] ⏭️ create_all skipped (lock exists)")
            else:
                db.create_all()
                try:
                    with open(lock_path, "w", encoding="utf-8") as f:
                        f.write(str(datetime.utcnow()))
                except Exception:
                    pass
                print("[DB] ✅ create_all executed")
        except Exception as e:
            print(f"[DB] ⚠️ create_all failed: {e}")

    # Gemini key
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    if not GEMINI_API_KEY:
        print("[AI] ⚠️ GEMINI_API_KEY missing")

    if GEMINI_API_KEY:
        try:
            extensions.ai_client = genai3.Client(api_key=GEMINI_API_KEY)
            extensions.advisor_client = extensions.ai_client
            print("[AI] ✅ Gemini 3 client initialized")
        except Exception as e:
            extensions.ai_client = None
            extensions.advisor_client = None
            print(f"[AI] ❌ Failed to init Gemini 3 client: {e}")
    else:
        extensions.ai_client = None
        extensions.advisor_client = None

    # OAuth
    oauth.register(
        name='google',
        client_id=os.environ.get('GOOGLE_CLIENT_ID'),
        client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
        api_base_url='https://www.googleapis.com/oauth2/v1/',
        userinfo_endpoint='https://openidconnect.googleapis.com/v1/userinfo',
        claims_options={'iss': {'values': ['https://accounts.google.com', 'accounts.google.com']}}
    )

    # ------------------
    # ===== ROUTES =====
    # ------------------
    
    # Register blueprints
    from app.routes.public_routes import bp as public_bp
    from app.routes.analyze_routes import bp as analyze_bp
    from app.routes.advisor_routes import bp as advisor_bp
    from app.routes.dashboard_routes import bp as dashboard_bp
    app.register_blueprint(public_bp)
    app.register_blueprint(analyze_bp)
    app.register_blueprint(advisor_bp)
    app.register_blueprint(dashboard_bp)


    @app.cli.command("init-db")
    def init_db_command():
        with app.app_context():
            db.create_all()
        print("Initialized the database tables.")

    return app


# ===================================================================
# ===== 5. נקודת כניסה (Gunicorn/Flask) =====
# ===================================================================
# Render מריץ עם:
# gunicorn "app:create_app()" --bind 0.0.0.0:$PORT ...
# לכן אסור ליצור app בזמן import (אחרת זה יאתחל פעמיים).

if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    app.run(host='0.0.0.0', port=port, debug=debug)
