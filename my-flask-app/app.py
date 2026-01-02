# -*- coding: utf-8 -*-
# ===================================================================
# 🚗 Car Reliability Analyzer – Israel
# v7.5.1 (Security + Split Quotas + Success-only Charge + Stable Sessions
# + Full Schema Alignment: Old+New prompts + strict postprocess)
# Canonical: https://yedaarechev.com
# ===================================================================

import os, re, json, traceback
import time as pytime
from typing import Optional, Tuple, Any, Dict, List
from datetime import datetime, time, timedelta, date

from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    current_user, login_required
)
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException
from json_repair import repair_json

# CSRF (soft mode for API until JS updated)
from flask_wtf.csrf import CSRFProtect, generate_csrf, validate_csrf, CSRFError

# Rate limiting
from flask_limiter import Limiter

# Optional
try:
    from flask_cors import CORS
except Exception:
    CORS = None

# Gemini (Analyze)
import google.generativeai as genai

# Gemini 3 SDK (Advisor)
from google import genai as genai3
from google.genai import types as genai_types

# TZ for daily quota
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


# ==================================
# === GLOBAL OBJECTS ===============
# ==================================
db = SQLAlchemy()
login_manager = LoginManager()
oauth = OAuth()
csrf = CSRFProtect()
limiter = None

# Advisor client
advisor_client = None
GEMINI3_MODEL_ID = os.environ.get("GEMINI3_MODEL_ID", "gemini-3-pro-preview")

# =========================
# ========= CONFIG ========
# =========================
PRIMARY_MODEL = os.environ.get("PRIMARY_MODEL", "gemini-2.5-flash")
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "gemini-1.5-flash-latest")
RETRIES = int(os.environ.get("RETRIES", "2"))
RETRY_BACKOFF_SEC = float(os.environ.get("RETRY_BACKOFF_SEC", "1.5"))

MAX_CACHE_DAYS = int(os.environ.get("MAX_CACHE_DAYS", "45"))

# Rate limit (anti-spam)
RL_ANALYZE = os.environ.get("RL_ANALYZE", "30/minute")
RL_ADVISOR = os.environ.get("RL_ADVISOR", "15/minute")

# Split daily quotas
GLOBAL_DAILY_LIMIT_ANALYZE = int(os.environ.get("GLOBAL_DAILY_LIMIT_ANALYZE", "1000"))
GLOBAL_DAILY_LIMIT_ADVISOR = int(os.environ.get("GLOBAL_DAILY_LIMIT_ADVISOR", "300"))

USER_DAILY_LIMIT_ANALYZE = int(os.environ.get("USER_DAILY_LIMIT_ANALYZE", "5"))
USER_DAILY_LIMIT_ADVISOR = int(os.environ.get("USER_DAILY_LIMIT_ADVISOR", "5"))

# Request body safety
MAX_JSON_BODY_BYTES = int(os.environ.get("MAX_JSON_BODY_BYTES", str(64 * 1024)))

# Canonical host
CANONICAL_HOST = (os.environ.get("CANONICAL_HOST") or "yedaarechev.com").strip().lower()
PUBLIC_HOST = (os.environ.get("PUBLIC_HOST") or "yedaarechev.com").strip().lower()

# Origin allowlist (optional; if empty we don't block same-origin missing-origin cases)
ALLOWED_ORIGINS = [
    o.strip().lower().rstrip("/")
    for o in (os.environ.get("ALLOWED_ORIGINS", "")).split(",")
    if o.strip()
]

# Daily quota timezone (Israel)
QUOTA_TZ = (os.environ.get("QUOTA_TZ") or "Asia/Jerusalem").strip()

# API CSRF enforcement switch:
# 0 = soft (allow same-origin without token; log only)
# 1 = strict (require token for API POST)
ENFORCE_CSRF_API = (os.environ.get("ENFORCE_CSRF_API", "0").strip().lower() in ("1", "true", "yes"))

# Production heuristic
IS_RENDER = bool((os.environ.get("RENDER", "") or "").strip())


# ===========================
# ====== DB MODELS ==========
# ===========================
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(200), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(100))
    searches = db.relationship('SearchHistory', backref='user', lazy=True)
    advisor_searches = db.relationship('AdvisorHistory', backref='user', lazy=True)


class SearchHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # keep schema compatible; use utc for new rows
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    make = db.Column(db.String(100))
    model = db.Column(db.String(100))
    year = db.Column(db.Integer)
    mileage_range = db.Column(db.String(100))
    fuel_type = db.Column(db.String(100))
    transmission = db.Column(db.String(100))
    result_json = db.Column(db.Text, nullable=False)


class AdvisorHistory(db.Model):
    """
    היסטוריית מנוע ההמלצות:
    - profile_json: כל הפרופיל של המשתמש (שאלון מלא)
    - result_json: כל ההמלצות + כל הפרמטרים וההסברים לכל רכב
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    profile_json = db.Column(db.Text, nullable=False)
    result_json = db.Column(db.Text, nullable=False)


class DailyQuota(db.Model):
    """
    Success-only counters.
    Unique: (day, scope_type, scope_id, endpoint)
    endpoint: 'analyze' or 'advisor'
    """
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False, index=True)
    scope_type = db.Column(db.String(10), nullable=False)  # 'user' / 'global'
    scope_id = db.Column(db.Integer, nullable=False)       # user_id or 0
    endpoint = db.Column(db.String(20), nullable=False)    # analyze / advisor
    success_count = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('day', 'scope_type', 'scope_id', 'endpoint', name='uq_daily_quota'),
    )


# =========================
# ========= HELPERS =======
# =========================
@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None


# --- dictionary load ---
try:
    from car_models_dict import israeli_car_market_full_compilation
    print(f"[DICT] ✅ Loaded car_models_dict. Manufacturers: {len(israeli_car_market_full_compilation)}")
except Exception as e:
    print(f"[DICT] ❌ Failed to import car_models_dict: {e}")
    israeli_car_market_full_compilation = {"Toyota": ["Corolla (2008-2025)"]}
    print("[DICT] ⚠️ Fallback applied — Toyota only")

import re as _re


def _now_utc() -> datetime:
    return datetime.utcnow()


def quota_day_today() -> date:
    if ZoneInfo:
        try:
            return datetime.now(ZoneInfo(QUOTA_TZ)).date()
        except Exception:
            pass
    return datetime.utcnow().date()


def normalize_text(s: Any) -> str:
    if s is None:
        return ""
    s = _re.sub(r"\(.*?\)", " ", str(s)).strip().lower()
    return _re.sub(r"\s+", " ", s)


def truncate(s: Any, n: int) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else (s[:n] + f"...[truncated {len(s)-n} chars]")


def parse_json_body() -> Tuple[Optional[dict], Optional[Tuple[Any, int]]]:
    cl = request.content_length
    if cl is not None and cl > MAX_JSON_BODY_BYTES:
        return None, (jsonify({"error": "קלט גדול מדי"}), 413)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (jsonify({"error": "קלט JSON לא תקין"}), 400)
    return payload, None


def get_client_ip() -> str:
    cf = (request.headers.get("CF-Connecting-IP") or "").strip()
    if cf:
        return cf
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if xff:
        return xff
    return request.remote_addr or ""


def is_same_origin_request() -> bool:
    """
    Soft detection to avoid false 403/CSRF problems on same-site requests.
    """
    origin = (request.headers.get("Origin") or "").lower().rstrip("/")
    host_origin = (request.host_url or "").lower().rstrip("/")
    referer = (request.headers.get("Referer") or "").lower()
    sec_fetch_site = (request.headers.get("Sec-Fetch-Site") or "").lower()

    if origin and host_origin and origin == host_origin:
        return True
    if (not origin) and host_origin and (host_origin in referer):
        return True
    if sec_fetch_site in ("same-origin", "same-site"):
        return True
    return False


def enforce_origin_if_configured() -> Optional[Tuple[Any, int]]:
    """
    Blocks only clearly cross-site origins when allowlist exists.
    If allowlist empty, we do NOT block (prevents killing legit traffic).
    """
    if not ALLOWED_ORIGINS:
        return None

    origin = (request.headers.get("Origin") or "").lower().rstrip("/")
    referer = (request.headers.get("Referer") or "").lower()

    if not origin:
        return None

    allowed = set(ALLOWED_ORIGINS)
    if origin in allowed:
        return None
    if any(o in referer for o in allowed):
        return None

    return jsonify({"error": "חסימת אבטחה: מקור הבקשה לא מורשה."}), 403


def soft_or_strict_csrf_for_api() -> Optional[Tuple[Any, int]]:
    """
    Soft mode now (site works before JS updates):
      - If strict mode enabled: require CSRF token for API POST/PUT/DELETE.
      - If soft: allow same-origin requests even without token.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None

    p = request.path or ""
    if not (p == "/analyze" or p == "/advisor_api" or p.startswith("/api/")):
        return None

    token = request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token") or ""
    token = token.strip()

    if ENFORCE_CSRF_API:
        if not token:
            return jsonify({"error": "שגיאת אבטחה (CSRF): חסר טוקן. רענן את הדף ונסה שוב."}), 403
        try:
            validate_csrf(token)
            return None
        except Exception:
            return jsonify({"error": "שגיאת אבטחה (CSRF): טוקן לא תקין. רענן את הדף ונסה שוב."}), 403

    # Soft mode:
    if token:
        try:
            validate_csrf(token)
            return None
        except Exception:
            return jsonify({"error": "שגיאת אבטחה (CSRF): טוקן לא תקין. רענן את הדף ונסה שוב."}), 403

    if is_same_origin_request():
        return None

    return jsonify({"error": "חסימת אבטחה: בקשה לא מזוהה."}), 403


def quota_limits_for(endpoint: str) -> Tuple[int, int]:
    """
    returns (user_limit, global_limit)
    """
    if endpoint == "analyze":
        return USER_DAILY_LIMIT_ANALYZE, GLOBAL_DAILY_LIMIT_ANALYZE
    if endpoint == "advisor":
        return USER_DAILY_LIMIT_ADVISOR, GLOBAL_DAILY_LIMIT_ADVISOR
    return 0, 0


def quota_precheck(endpoint: str) -> Optional[Tuple[Any, int]]:
    """
    Pre-check based on SUCCESS counts only (no charging here).
    """
    if not current_user.is_authenticated:
        return jsonify({"error": "נדרש להתחבר כדי להשתמש בשירות."}), 401

    user_limit, global_limit = quota_limits_for(endpoint)
    today = quota_day_today()

    try:
        g = DailyQuota.query.filter_by(day=today, scope_type="global", scope_id=0, endpoint=endpoint).first()
        if g and g.success_count >= global_limit:
            return jsonify({"error": "המערכת עמוסה: הגעת למכסה יומית כללית. נסה שוב מחר."}), 429

        u = DailyQuota.query.filter_by(day=today, scope_type="user", scope_id=current_user.id, endpoint=endpoint).first()
        if u and u.success_count >= user_limit:
            return jsonify({"error": f"ניצלת את {user_limit} הבקשות היומיות שלך בכלי זה. נסה שוב מחר."}), 429

        return None
    except Exception:
        return None


def quota_charge_success(endpoint: str) -> None:
    """
    Charge SUCCESS only after we actually have a valid result.
    """
    if not current_user.is_authenticated:
        return

    user_limit, global_limit = quota_limits_for(endpoint)
    today = quota_day_today()

    g = DailyQuota.query.filter_by(day=today, scope_type="global", scope_id=0, endpoint=endpoint).first()
    if not g:
        g = DailyQuota(day=today, scope_type="global", scope_id=0, endpoint=endpoint, success_count=0)
        db.session.add(g)
        db.session.flush()

    u = DailyQuota.query.filter_by(day=today, scope_type="user", scope_id=current_user.id, endpoint=endpoint).first()
    if not u:
        u = DailyQuota(day=today, scope_type="user", scope_id=current_user.id, endpoint=endpoint, success_count=0)
        db.session.add(u)
        db.session.flush()

    if g.success_count >= global_limit:
        raise RuntimeError("Global daily limit reached at charge time")
    if u.success_count >= user_limit:
        raise RuntimeError("User daily limit reached at charge time")

    g.success_count += 1
    g.updated_at = _now_utc()
    u.success_count += 1
    u.updated_at = _now_utc()


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
        base_key = "base_score_calculated"
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


# ===========================
# ===== ANALYZE PROMPT ======
# (Aligned with older "perfect schema")
# ===========================
def build_prompt(make, model, sub_model, year, fuel_type, transmission, mileage_range):
    extra = f" תת-דגם/תצורה: {sub_model}" if sub_model else ""
    return f"""
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

רכב: {make} {model}{extra} {int(year)}
טווח קילומטראז': {mileage_range}
סוג דלק: {fuel_type}
תיבת הילוכים: {transmission}
כתוב בעברית בלבד.
""".strip()


def call_model_with_retry(prompt: str) -> dict:
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
                resp = llm.generate_content(prompt)
                raw = (getattr(resp, "text", "") or "").strip()
                try:
                    m = _re.search(r"\{.*\}", raw, _re.DOTALL)
                    data = json.loads(m.group()) if m else json.loads(raw)
                except Exception:
                    data = json.loads(repair_json(raw))
                if not isinstance(data, dict):
                    raise ValueError("Model output is not a JSON object")
                return data
            except Exception as e:
                last_err = e
                if attempt < RETRIES:
                    pytime.sleep(RETRY_BACKOFF_SEC)
                continue
    raise RuntimeError(f"Model failed: {repr(last_err)}")


# ===========================
# ===== Car Advisor helpers ==
# (Aligned schema: old+new)
# ===========================
fuel_map = {
    "בנזין": "gasoline",
    "היברידי": "hybrid",
    "דיזל היברידי": "hybrid-diesel",
    "דיזל": "diesel",
    "חשמלי": "electric",
}
gear_map = {"אוטומטית": "automatic", "ידנית": "manual"}
turbo_map = {"לא משנה": "any", "כן": "yes", "לא": "no"}

fuel_map_he = {v: k for k, v in fuel_map.items()}
gear_map_he = {v: k for k, v in gear_map.items()}
turbo_map_he = {"yes": "כן", "no": "לא", "any": "לא משנה", True: "כן", False: "לא"}

REQUIRED_CAR_FIELDS = [
    "brand", "model", "year",
    "fuel", "gear", "turbo",
    "engine_cc",
    "price_range_nis",
    "avg_fuel_consumption", "fuel_method",
    "annual_fee", "fee_method",
    "reliability_score", "reliability_method",
    "maintenance_cost", "maintenance_method",
    "safety_rating", "safety_method",
    "insurance_cost", "insurance_method",
    "resale_value", "resale_method",
    "performance_score", "performance_method",
    "comfort_features", "comfort_method",
    "suitability", "suitability_method",
    "market_supply", "supply_method",
    "fit_score",
    "comparison_comment",
    "not_recommended_reason",
]


def has_required_fields(car: dict) -> bool:
    if not isinstance(car, dict):
        return False
    for k in REQUIRED_CAR_FIELDS:
        if k not in car:
            return False
    return True


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
        "fuel": [str(f).lower() for f in (fuels or [])],
        "gear": [str(g).lower() for g in (gears or [])],
        "turbo_required": None if turbo_required == "any" else (turbo_required == "yes"),
        "main_use": (main_use or "").strip(),
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


def car_advisor_call_gemini_with_search(profile: dict) -> dict:
    """
    Gemini 3 Pro (SDK החדש) עם Google Search ו-output כ-JSON בלבד.
    Full schema enforced in prompt.
    """
    global advisor_client
    if advisor_client is None:
        return {"_error": "Gemini Car Advisor client unavailable."}

    prompt = f"""
Please recommend cars for an Israeli customer. Here is the user profile (JSON):
{json.dumps(profile, ensure_ascii=False, indent=2)}

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
""".strip()

    search_tool = genai_types.Tool(google_search=genai_types.GoogleSearch())
    config = genai_types.GenerateContentConfig(
        temperature=0.3,
        top_p=0.9,
        top_k=40,
        tools=[search_tool],
        response_mime_type="application/json",
    )

    try:
        resp = advisor_client.models.generate_content(
            model=GEMINI3_MODEL_ID,
            contents=prompt,
            config=config,
        )
        text = (getattr(resp, "text", "") or "").strip()
        try:
            parsed = json.loads(repair_json(text))
            if not isinstance(parsed, dict):
                return {"_error": "Invalid JSON object from advisor"}
            return parsed
        except Exception:
            return {"_error": "JSON decode error from Gemini Car Advisor"}
    except Exception as e:
        return {"_error": f"Gemini Car Advisor call failed: {e}"}


def car_advisor_postprocess(profile: dict, parsed: dict) -> dict:
    """
    - Strict schema: drops cars missing required fields
    - No fake zeros: missing numeric values stay null (None)
    - Clamp scores to expected ranges
    - Compute annual_energy_cost + total_annual_cost only when enough data exists
    - Map fuel/gear/turbo to Hebrew for frontend
    """
    recommended = parsed.get("recommended_cars") or []
    if not isinstance(recommended, list) or not recommended:
        return {
            "search_performed": bool(parsed.get("search_performed", False)),
            "search_queries": parsed.get("search_queries", []),
            "recommended_cars": [],
        }

    annual_km = int(profile.get("annual_km", 15000) or 15000)
    fuel_price = float(profile.get("fuel_price_nis_per_liter", 7.0) or 7.0)
    elec_price = float(profile.get("electricity_price_nis_per_kwh", 0.65) or 0.65)

    def num_or_none(x) -> Optional[float]:
        try:
            v = float(x)
            if v <= 0:
                return None
            return v
        except Exception:
            return None

    def clamp(v: Optional[float], lo: float, hi: float) -> Optional[float]:
        if v is None:
            return None
        return max(lo, min(hi, v))

    def to_int_or_none(x) -> Optional[int]:
        try:
            v = int(float(x))
            return v if v > 0 else None
        except Exception:
            return None

    processed: List[dict] = []
    for car in recommended[:10]:
        if not isinstance(car, dict):
            continue
        if not has_required_fields(car):
            continue

        car = dict(car)

        # strings
        fuel_val = str(car.get("fuel", "")).strip()
        gear_val = str(car.get("gear", "")).strip()
        turbo_val = car.get("turbo")

        fuel_norm = fuel_map.get(fuel_val, (fuel_val or "").lower())
        gear_norm = gear_map.get(gear_val, (gear_val or "").lower())
        turbo_norm = turbo_map.get(turbo_val, turbo_val) if isinstance(turbo_val, str) else turbo_val

        # numeric coercion
        car["year"] = to_int_or_none(car.get("year"))
        car["engine_cc"] = to_int_or_none(car.get("engine_cc"))

        # core scores
        car["fit_score"] = clamp(num_or_none(car.get("fit_score")), 0, 100)
        car["reliability_score"] = clamp(num_or_none(car.get("reliability_score")), 1, 10)
        car["safety_rating"] = clamp(num_or_none(car.get("safety_rating")), 1, 10)
        car["resale_value"] = clamp(num_or_none(car.get("resale_value")), 1, 10)
        car["performance_score"] = clamp(num_or_none(car.get("performance_score")), 1, 10)
        car["comfort_features"] = clamp(num_or_none(car.get("comfort_features")), 1, 10)
        car["suitability"] = clamp(num_or_none(car.get("suitability")), 1, 10)

        avg_fc_num = num_or_none(car.get("avg_fuel_consumption"))

        annual_energy_cost = None
        if avg_fc_num is not None:
            if fuel_norm == "electric":
                annual_energy_cost = (annual_km / 100.0) * avg_fc_num * elec_price
            else:
                annual_energy_cost = (annual_km / avg_fc_num) * fuel_price

        maintenance_cost = num_or_none(car.get("maintenance_cost"))
        insurance_cost = num_or_none(car.get("insurance_cost"))
        annual_fee = num_or_none(car.get("annual_fee"))

        total_annual_cost = None
        if (annual_energy_cost is not None and maintenance_cost is not None and insurance_cost is not None and annual_fee is not None):
            total_annual_cost = annual_energy_cost + maintenance_cost + insurance_cost + annual_fee

        car["annual_energy_cost"] = round(annual_energy_cost, 0) if annual_energy_cost is not None else None
        car["annual_fuel_cost"] = car["annual_energy_cost"]
        car["maintenance_cost"] = round(maintenance_cost, 0) if maintenance_cost is not None else None
        car["insurance_cost"] = round(insurance_cost, 0) if insurance_cost is not None else None
        car["annual_fee"] = round(annual_fee, 0) if annual_fee is not None else None
        car["total_annual_cost"] = round(total_annual_cost, 0) if total_annual_cost is not None else None

        # Hebrew mapping
        car["fuel"] = fuel_map_he.get(fuel_norm, fuel_val or fuel_norm)
        car["gear"] = gear_map_he.get(gear_norm, gear_val or gear_norm)
        car["turbo"] = turbo_map_he.get(turbo_norm, turbo_val)

        # final sanity
        if not car.get("brand") or not car.get("model") or not car.get("year") or car.get("fit_score") is None:
            continue

        processed.append(car)

    return {
        "search_performed": bool(parsed.get("search_performed", False)),
        "search_queries": parsed.get("search_queries", []),
        "recommended_cars": processed,
    }


# ========================================
# ===== APP FACTORY ======================
# ========================================
def create_app():
    global advisor_client, limiter

    app = Flask(__name__)

    # Trust proxy headers (Cloudflare + Render can be 2 hops)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=2, x_proto=1, x_host=1, x_prefix=1)

    # Safety
    app.config["MAX_CONTENT_LENGTH"] = MAX_JSON_BODY_BYTES
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["WTF_CSRF_HEADERS"] = ["X-CSRFToken", "X-CSRF-Token"]

    # Cookies/session stability
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = True if IS_RENDER else False

    # Canonical cookie domain in production
    if IS_RENDER and PUBLIC_HOST:
        app.config["SESSION_COOKIE_DOMAIN"] = f".{PUBLIC_HOST}"
    else:
        app.config["SESSION_COOKIE_DOMAIN"] = None

    # Secrets
    db_url = (os.environ.get('DATABASE_URL') or "").strip()
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    secret_key = (os.environ.get('SECRET_KEY') or "").strip()

    if IS_RENDER and not db_url:
        raise RuntimeError("DATABASE_URL missing on Render.")
    if IS_RENDER and not secret_key:
        raise RuntimeError("SECRET_KEY missing on Render.")

    if not db_url:
        db_url = "sqlite:///:memory:"
    if not secret_key:
        secret_key = "dev-secret-key-that-is-not-secret"

    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SECRET_KEY'] = secret_key

    # Init extensions
    db.init_app(app)
    login_manager.init_app(app)
    oauth.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = 'login'

    @login_manager.unauthorized_handler
    def unauthorized():
        p = request.path or ""
        if p in ("/analyze", "/advisor_api") or p.startswith("/api/"):
            return jsonify({"error": "נדרש להתחבר כדי להשתמש בשירות."}), 401
        return redirect(url_for("login"))

    # CORS (only if you explicitly need it)
    if CORS is not None and ALLOWED_ORIGINS:
        CORS(app, supports_credentials=True, resources={r"/*": {"origins": ALLOWED_ORIGINS}})

    # Rate limiter
    redis_url = (os.environ.get("REDIS_URL") or os.environ.get("VALKEY_URL") or "").strip()
    storage_uri = redis_url if redis_url else "memory://"

    def limiter_key():
        if current_user.is_authenticated:
            return f"user:{current_user.id}"
        return f"ip:{get_client_ip() or 'unknown'}"

    limiter = Limiter(
        key_func=limiter_key,
        storage_uri=storage_uri,
        strategy="fixed-window",
        default_limits=[],
        headers_enabled=True,
    )
    limiter.init_app(app)

    # Create DB tables (safe-ish once-per-container lock)
    with app.app_context():
        try:
            lock_path = "/tmp/.db_inited.lock"
            if os.environ.get("SKIP_CREATE_ALL", "").lower() in ("1", "true", "yes"):
                print("[DB] ⏭️ SKIP_CREATE_ALL enabled - skipping db.create_all()")
            elif os.path.exists(lock_path):
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

    # Gemini key init
    GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not GEMINI_API_KEY and IS_RENDER:
        raise RuntimeError("GEMINI_API_KEY missing on Render.")

    genai.configure(api_key=GEMINI_API_KEY)

    if GEMINI_API_KEY:
        try:
            advisor_client = genai3.Client(api_key=GEMINI_API_KEY)
            print("[CAR-ADVISOR] ✅ Gemini 3 client initialized")
        except Exception as e:
            advisor_client = None
            print(f"[CAR-ADVISOR] ❌ Failed to init Gemini 3 client: {e}")
    else:
        advisor_client = None

    # OAuth register
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

    # Owner list
    OWNER_EMAILS = [
        e.strip().lower()
        for e in os.environ.get("OWNER_EMAILS", "").split(",")
        if e.strip()
    ]

    def is_owner_user() -> bool:
        if not current_user.is_authenticated:
            return False
        email = (getattr(current_user, "email", "") or "").lower()
        return email in OWNER_EMAILS

    @app.context_processor
    def inject_template_globals():
        return {
            "is_logged_in": current_user.is_authenticated,
            "current_user": current_user,
            "is_owner": is_owner_user(),
        }

    # Canonical redirect + security gates
    @app.before_request
    def canonical_and_security_gate():
        host = (request.host or "").lower()
        if host.startswith("www.") and CANONICAL_HOST and host.endswith(CANONICAL_HOST):
            target = f"https://{CANONICAL_HOST}{request.full_path}"
            if target.endswith("?"):
                target = target[:-1]
            return redirect(target, code=301)

        if request.path in ("/analyze", "/advisor_api") or request.path.startswith("/api/"):
            block = enforce_origin_if_configured()
            if block:
                return block

            csrf_block = soft_or_strict_csrf_for_api()
            if csrf_block:
                return csrf_block

        return None

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if request.path in ("/analyze", "/advisor_api") or request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ===========================
    # Health + CSRF
    # ===========================
    @app.route("/healthz")
    def healthz():
        return "ok", 200

    @app.route("/api/csrf", methods=["GET"])
    def api_csrf():
        token = generate_csrf()
        return jsonify({"csrf_token": token})

    # ===========================
    # Pages
    # ===========================
    @app.route('/')
    def index():
        return render_template(
            'index.html',
            car_models_data=israeli_car_market_full_compilation,
            user=current_user,
            is_owner=is_owner_user(),
        )

    def get_redirect_uri():
        host = (request.host or "").lower()
        if CANONICAL_HOST and CANONICAL_HOST in host:
            return f"https://{CANONICAL_HOST}/auth"
        return request.url_root.rstrip("/") + "/auth"

    @app.route('/login')
    def login():
        redirect_uri = get_redirect_uri()
        return oauth.google.authorize_redirect(redirect_uri)

    @app.route('/auth')
    def auth():
        try:
            oauth.google.authorize_access_token()
            userinfo = oauth.google.get('userinfo').json()

            google_id = userinfo.get("id")
            email = userinfo.get("email", "")
            name = userinfo.get("name", "")

            if not google_id or not email:
                return redirect(url_for('index'))

            user = User.query.filter_by(google_id=google_id).first()
            if not user:
                user = User(google_id=google_id, email=email, name=name)
                db.session.add(user)
                db.session.commit()

            login_user(user)
            return redirect(url_for('index'))
        except Exception as e:
            print(f"[AUTH] ❌ {e}")
            traceback.print_exc()
            try:
                logout_user()
                session.clear()
            except Exception:
                pass
            return redirect(url_for('index'))

    @app.route('/logout')
    @login_required
    def logout():
        try:
            logout_user()
            session.clear()
        except Exception:
            pass
        return redirect(url_for('index'))

    @app.route('/privacy')
    def privacy():
        return render_template('privacy.html', user=current_user, is_owner=is_owner_user())

    @app.route('/terms')
    def terms():
        return render_template('terms.html', user=current_user, is_owner=is_owner_user())

    @app.route('/dashboard')
    @login_required
    def dashboard():
        try:
            user_searches = SearchHistory.query.filter_by(
                user_id=current_user.id
            ).order_by(SearchHistory.timestamp.desc()).all()

            searches_data = []
            for s in user_searches:
                searches_data.append({
                    "id": s.id,
                    "timestamp": s.timestamp.strftime('%d/%m/%Y %H:%M'),
                    "make": s.make,
                    "model": s.model,
                    "year": s.year,
                    "mileage_range": s.mileage_range or '',
                    "fuel_type": s.fuel_type or '',
                    "transmission": s.transmission or '',
                    "data": json.loads(s.result_json)
                })

            advisor_entries = AdvisorHistory.query.filter_by(
                user_id=current_user.id
            ).order_by(AdvisorHistory.timestamp.desc()).all()
            advisor_count = len(advisor_entries)

            return render_template(
                'dashboard.html',
                searches=searches_data,
                advisor_count=advisor_count,
                user=current_user,
                is_owner=is_owner_user(),
            )
        except Exception as e:
            print(f"[DASH] ❌ {e}")
            return redirect(url_for('index'))

    @app.route('/search-details/<int:search_id>')
    @login_required
    def search_details(search_id):
        try:
            s = SearchHistory.query.filter_by(id=search_id, user_id=current_user.id).first()
            if not s:
                return jsonify({"error": "לא נמצא רישום מתאים"}), 404

            meta = {
                "id": s.id,
                "timestamp": s.timestamp.strftime("%d/%m/%Y %H:%M"),
                "make": s.make.title() if s.make else "",
                "model": s.model.title() if s.model else "",
                "year": s.year,
                "mileage_range": s.mileage_range,
                "fuel_type": s.fuel_type,
                "transmission": s.transmission,
            }
            return jsonify({"meta": meta, "data": json.loads(s.result_json)})
        except Exception as e:
            print(f"[DETAILS] ❌ {e}")
            return jsonify({"error": "שגיאת שרת בשליפת נתוני חיפוש"}), 500

    @app.route('/recommendations')
    @login_required
    def recommendations():
        user_email = getattr(current_user, "email", "") if current_user.is_authenticated else ""
        return render_template(
            'recommendations.html',
            user=current_user,
            user_email=user_email,
            is_owner=is_owner_user(),
        )

    # ===========================
    # 🔹 Car Advisor – API JSON
    # ===========================
    @app.route('/advisor_api', methods=['POST'])
    @login_required
    @limiter.limit(RL_ADVISOR)
    def advisor_api():
        qerr = quota_precheck("advisor")
        if qerr:
            return qerr

        payload, err = parse_json_body()
        if err:
            return err

        try:
            # ---- base ----
            budget_min = float(payload.get("budget_min", 0))
            budget_max = float(payload.get("budget_max", 0))
            year_min = int(payload.get("year_min", 2000))
            year_max = int(payload.get("year_max", 2025))

            if budget_max <= 0 or budget_min > budget_max:
                return jsonify({"error": "תקציב לא תקין (min/max)."}), 400
            if year_min > year_max:
                return jsonify({"error": "טווח שנים לא תקין."}), 400

            fuels_he = payload.get("fuels_he") or []
            gears_he = payload.get("gears_he") or []
            turbo_choice_he = payload.get("turbo_choice_he", "לא משנה")

            # ---- usage/style ----
            main_use = (payload.get("main_use") or "").strip()
            annual_km = int(payload.get("annual_km", 15000))
            driver_age = int(payload.get("driver_age", 21))

            license_years = int(payload.get("license_years", 0))
            driver_gender = payload.get("driver_gender", "זכר") or "זכר"

            body_style = payload.get("body_style", "כללי") or "כללי"
            driving_style = payload.get("driving_style", "רגוע ונינוח") or "רגוע ונינוח"
            seats_choice = payload.get("seats_choice", "5") or "5"

            excluded_colors = payload.get("excluded_colors") or []
            if isinstance(excluded_colors, str):
                excluded_colors = [s.strip() for s in excluded_colors.split(",") if s.strip()]

            # ---- priorities ----
            weights = payload.get("weights") or {
                "reliability": 5, "resale": 3, "fuel": 4, "performance": 2, "comfort": 3
            }

            # ---- extras ----
            insurance_history = payload.get("insurance_history", "") or ""
            violations = payload.get("violations", "אין") or "אין"

            family_size = payload.get("family_size", "1-2") or "1-2"
            cargo_need = payload.get("cargo_need", "בינוני") or "בינוני"

            safety_required = payload.get("safety_required") or payload.get("safety_required_radio") or "כן"
            trim_level = payload.get("trim_level", "סטנדרטי") or "סטנדרטי"

            consider_supply = payload.get("consider_supply", "כן") or "כן"
            consider_market_supply = (consider_supply == "כן")

            fuel_price = float(payload.get("fuel_price", 7.0))
            electricity_price = float(payload.get("electricity_price", 0.65))

        except Exception as e:
            return jsonify({"error": f"שגיאת קלט: {e}"}), 400

        fuels = [fuel_map.get(f, "gasoline") for f in fuels_he] if fuels_he else ["gasoline"]

        if "חשמלי" in fuels_he:
            gears = ["automatic"]
        else:
            gears = [gear_map.get(g, "automatic") for g in gears_he] if gears_he else ["automatic"]

        turbo_choice = turbo_map.get(turbo_choice_he, "any")

        user_profile = make_user_profile(
            budget_min, budget_max, [year_min, year_max],
            fuels, gears, turbo_choice,
            main_use, annual_km, driver_age,
            family_size, cargo_need, safety_required,
            trim_level, weights, body_style, driving_style, excluded_colors
        )

        # additional fields
        user_profile["license_years"] = license_years
        user_profile["driver_gender"] = driver_gender
        user_profile["insurance_history"] = insurance_history
        user_profile["violations"] = violations
        user_profile["consider_market_supply"] = consider_market_supply
        user_profile["fuel_price_nis_per_liter"] = fuel_price
        user_profile["electricity_price_nis_per_kwh"] = electricity_price
        user_profile["seats"] = seats_choice

        parsed = car_advisor_call_gemini_with_search(user_profile)
        if parsed.get("_error"):
            return jsonify({"error": "שגיאת AI במנוע ההמלצות. נסה שוב מאוחר יותר."}), 500

        result = car_advisor_postprocess(user_profile, parsed)

        cars = result.get("recommended_cars")
        if not (
            isinstance(result, dict)
            and result.get("search_performed") is True
            and isinstance(cars, list)
            and len(cars) >= 3
        ):
            return jsonify({"error": "פלט AI לא תקין (Advisor)."}), 500

        # charge success-only quota
        try:
            quota_charge_success("advisor")
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[QUOTA] advisor charge failed: {e}")

        # save history
        try:
            rec_log = AdvisorHistory(
                user_id=current_user.id,
                profile_json=json.dumps(user_profile, ensure_ascii=False),
                result_json=json.dumps(result, ensure_ascii=False),
            )
            db.session.add(rec_log)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[DB] ⚠️ failed to save advisor history: {e}")

        return jsonify(result)

    # ===========================
    # 🔹 Reliability analyze – API
    # ===========================
    @app.route('/analyze', methods=['POST'])
    @login_required
    @limiter.limit(RL_ANALYZE)
    def analyze_car():
        qerr = quota_precheck("analyze")
        if qerr:
            return qerr

        payload, err = parse_json_body()
        if err:
            return err

        try:
            final_make = normalize_text(payload.get('make'))
            final_model = normalize_text(payload.get('model'))
            final_sub_model = normalize_text(payload.get('sub_model') or "")
            final_year = int(payload.get('year')) if payload.get('year') else None
            final_mileage = str(payload.get('mileage_range') or "")
            final_fuel = str(payload.get('fuel_type') or "")
            final_trans = str(payload.get('transmission') or "")

            if not (final_make and final_model and final_year):
                return jsonify({"error": "נא למלא יצרן, דגם ושנה"}), 400
        except Exception as e:
            return jsonify({"error": f"שגיאת קלט: {e}"}), 400

        # Cache (still counts as success because user got an answer)
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=MAX_CACHE_DAYS)
            cached = SearchHistory.query.filter(
                SearchHistory.make == final_make,
                SearchHistory.model == final_model,
                SearchHistory.year == final_year,
                SearchHistory.mileage_range == final_mileage,
                SearchHistory.fuel_type == final_fuel,
                SearchHistory.transmission == final_trans,
                SearchHistory.timestamp >= cutoff_date
            ).order_by(SearchHistory.timestamp.desc()).first()

            if cached:
                result = json.loads(cached.result_json)
                result['source_tag'] = f"מקור: מטמון DB (נשמר ב-{cached.timestamp.strftime('%Y-%m-%d')})"

                try:
                    quota_charge_success("analyze")
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    print(f"[QUOTA] analyze charge failed (cache): {e}")

                return jsonify(result)
        except Exception as e:
            print(f"[CACHE] ⚠️ {e}")

        # AI call
        try:
            prompt = build_prompt(
                final_make, final_model, final_sub_model, final_year,
                final_fuel, final_trans, final_mileage
            )
            model_output = call_model_with_retry(prompt)
        except Exception:
            traceback.print_exc()
            return jsonify({"error": "שגיאת AI בעת ניתוח. נסה שוב מאוחר יותר."}), 500

        # Validate "success"
        if not (
            isinstance(model_output, dict)
            and model_output.get("search_performed") is True
            and ("base_score_calculated" in model_output)
        ):
            return jsonify({"error": "פלט AI לא תקין (Analyze)."}), 500

        model_output, note = apply_mileage_logic(model_output, final_mileage)

        # Charge quota first (success-only), then save history
        try:
            quota_charge_success("analyze")
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[QUOTA] analyze charge failed: {e}")

        try:
            new_log = SearchHistory(
                user_id=current_user.id,
                make=final_make,
                model=final_model,
                year=final_year,
                mileage_range=final_mileage,
                fuel_type=final_fuel,
                transmission=final_trans,
                result_json=json.dumps(model_output, ensure_ascii=False)
            )
            db.session.add(new_log)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[DB] ⚠️ save failed: {e}")

        model_output['source_tag'] = "מקור: ניתוח AI חדש"
        model_output['mileage_note'] = note
        model_output['km_warn'] = False
        return jsonify(model_output)

    # ===========================
    # Error handlers
    # ===========================
    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        return jsonify({"error": "שגיאת אבטחה (CSRF). רענן את הדף ונסה שוב."}), 403

    @app.errorhandler(429)
    def handle_429(e):
        return jsonify({"error": "הגעת למגבלת בקשות (Rate Limit)."}), 429

    @app.errorhandler(HTTPException)
    def handle_http_exception(e):
        code = int(getattr(e, "code", 500) or 500)
        msg = getattr(e, "description", None) or "שגיאת בקשה"
        if request.path in ("/analyze", "/advisor_api") or request.path.startswith("/api/"):
            return jsonify({"error": msg}), code
        return e

    @app.errorhandler(Exception)
    def handle_exception(e):
        traceback.print_exc()
        if request.path in ("/analyze", "/advisor_api") or request.path.startswith("/api/"):
            return jsonify({"error": "שגיאת שרת פנימית"}), 500
        return "Internal Server Error", 500

    return app


# ===================================================================
# Entry
# ===================================================================
# אם אתה מריץ gunicorn ככה:
#   gunicorn app:app --bind 0.0.0.0:$PORT
# אז צריך app גלובלי.
# אם אתה מריץ:
#   gunicorn "app:create_app()"
# אז תמחק את השורה app=create_app() ותשאיר רק create_app.
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes') and (not IS_RENDER)
    app.run(host='0.0.0.0', port=port, debug=debug)
