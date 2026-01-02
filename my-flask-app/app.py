# -*- coding: utf-8 -*-
# ===================================================================
# 🚗 Car Reliability Analyzer – Israel
# v7.5.1 (Security + Schema Sync + Advisor Normalization + CSRF Cookie)
# Canonical: https://yedaarechev.com
# ===================================================================

import os, json, traceback
import time as pytime
from typing import Optional, Tuple, Any, Dict, List
from datetime import datetime, timedelta, date

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

# CSRF
from flask_wtf.csrf import CSRFProtect, generate_csrf, validate_csrf, CSRFError

# Rate limiting
from flask_limiter import Limiter

# Optional CORS
try:
    from flask_cors import CORS
except Exception:
    CORS = None

# Gemini (existing in your v7.4.0)
import google.generativeai as genai

# Gemini 3 SDK (Advisor)
from google import genai as genai3
from google.genai import types as genai_types

# TZ for daily quota
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

import re as _re


# ==================================
# === GLOBAL OBJECTS ===============
# ==================================
db = SQLAlchemy()
login_manager = LoginManager()
oauth = OAuth()
csrf = CSRFProtect()
limiter = None

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

RL_ANALYZE = os.environ.get("RL_ANALYZE", "30/minute")
RL_ADVISOR = os.environ.get("RL_ADVISOR", "15/minute")

GLOBAL_DAILY_LIMIT_ANALYZE = int(os.environ.get("GLOBAL_DAILY_LIMIT_ANALYZE", "1000"))
GLOBAL_DAILY_LIMIT_ADVISOR = int(os.environ.get("GLOBAL_DAILY_LIMIT_ADVISOR", "300"))

USER_DAILY_LIMIT_ANALYZE = int(os.environ.get("USER_DAILY_LIMIT_ANALYZE", "5"))
USER_DAILY_LIMIT_ADVISOR = int(os.environ.get("USER_DAILY_LIMIT_ADVISOR", "5"))

MAX_JSON_BODY_BYTES = int(os.environ.get("MAX_JSON_BODY_BYTES", str(64 * 1024)))

CANONICAL_HOST = (os.environ.get("CANONICAL_HOST") or "yedaarechev.com").strip().lower()
PUBLIC_HOST = (os.environ.get("PUBLIC_HOST") or "yedaarechev.com").strip().lower()

ALLOWED_ORIGINS = [
    o.strip().lower().rstrip("/")
    for o in (os.environ.get("ALLOWED_ORIGINS", "")).split(",")
    if o.strip()
]

QUOTA_TZ = (os.environ.get("QUOTA_TZ") or "Asia/Jerusalem").strip()

# 0 = soft, 1 = strict
ENFORCE_CSRF_API = (os.environ.get("ENFORCE_CSRF_API", "0").strip().lower() in ("1", "true", "yes"))

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
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    make = db.Column(db.String(100))
    model = db.Column(db.String(100))
    year = db.Column(db.Integer)
    mileage_range = db.Column(db.String(100))
    fuel_type = db.Column(db.String(100))
    transmission = db.Column(db.String(100))
    result_json = db.Column(db.Text, nullable=False)


class AdvisorHistory(db.Model):
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
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None

    p = request.path or ""
    if not (p == "/analyze" or p == "/advisor_api" or p.startswith("/api/")):
        return None

    token = (request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token") or "").strip()

    if ENFORCE_CSRF_API:
        if not token:
            return jsonify({"error": "שגיאת אבטחה (CSRF): חסר טוקן. רענן את הדף ונסה שוב."}), 403
        try:
            validate_csrf(token)
            return None
        except Exception:
            return jsonify({"error": "שגיאת אבטחה (CSRF): טוקן לא תקין. רענן את הדף ונסה שוב."}), 403

    # Soft mode
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
    if endpoint == "analyze":
        return USER_DAILY_LIMIT_ANALYZE, GLOBAL_DAILY_LIMIT_ANALYZE
    if endpoint == "advisor":
        return USER_DAILY_LIMIT_ADVISOR, GLOBAL_DAILY_LIMIT_ADVISOR
    return 0, 0


def quota_precheck(endpoint: str) -> Optional[Tuple[Any, int]]:
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


# =========================
# Analyze output helpers
# =========================
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


def sanitize_analyze_output(d: dict) -> dict:
    """Make sure the response matches what script.js renders."""
    if not isinstance(d, dict):
        return {}

    out = dict(d)

    # lists
    if not isinstance(out.get("common_issues"), list):
        out["common_issues"] = []
    if not isinstance(out.get("recommended_checks"), list):
        out["recommended_checks"] = []
    if not isinstance(out.get("common_competitors_brief"), list):
        out["common_competitors_brief"] = []
    if not isinstance(out.get("issues_with_costs"), list):
        out["issues_with_costs"] = []

    # normalize issues_with_costs rows
    fixed_rows = []
    for row in out["issues_with_costs"]:
        if not isinstance(row, dict):
            continue
        fixed_rows.append({
            "issue": row.get("issue") or row.get("name") or "",
            "avg_cost_ILS": row.get("avg_cost_ILS") or row.get("cost") or row.get("avg_cost") or "",
            "source": row.get("source") or row.get("src") or "",
            "severity": row.get("severity") or row.get("level") or "",
        })
    out["issues_with_costs"] = fixed_rows

    # competitors normalization
    fixed_comp = []
    for c in out["common_competitors_brief"]:
        if not isinstance(c, dict):
            continue
        fixed_comp.append({
            "model": c.get("model") or c.get("name") or "",
            "brief_summary": c.get("brief_summary") or c.get("summary") or "",
        })
    out["common_competitors_brief"] = fixed_comp

    # ensure summaries exist
    out["reliability_summary_simple"] = (out.get("reliability_summary_simple") or "").strip()
    out["reliability_summary"] = (out.get("reliability_summary") or "").strip()

    return out


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
  "reliability_summary": "סיכום מקצועי בעברית.",
  "reliability_summary_simple": "הסבר קצר ופשוט בעברית.",
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
# ===========================
fuel_map = {"בנזין": "gasoline", "היברידי": "hybrid", "דיזל היברידי": "hybrid-diesel", "דיזל": "diesel", "חשמלי": "electric"}
gear_map = {"אוטומטית": "automatic", "ידנית": "manual"}
turbo_map = {"לא משנה": "any", "כן": "yes", "לא": "no"}

fuel_map_he = {v: k for k, v in fuel_map.items()}
gear_map_he = {v: k for k, v in gear_map.items()}
turbo_map_he = {"yes": "כן", "no": "לא", "any": "לא משנה", True: "כן", False: "לא"}


def make_user_profile(
    budget_min, budget_max, years_range, fuels, gears, turbo_required,
    main_use, annual_km, driver_age, family_size, cargo_need,
    safety_required, trim_level, weights, body_style, driving_style,
    excluded_colors,
):
    # ensure excluded_colors list
    if isinstance(excluded_colors, str):
        excluded_colors = [x.strip() for x in excluded_colors.split(",") if x.strip()]
    if not isinstance(excluded_colors, list):
        excluded_colors = []

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


# ---- Advisor schema normalization (NEW/OLD field names) ----
_ADVISOR_CANON_KEYS = [
    "brand", "model", "year",
    "engine_cc", "price_range_nis",
    "fuel", "gear", "turbo",
    "avg_fuel_consumption", "annual_fee",
    "reliability_score", "maintenance_cost", "safety_rating",
    "insurance_cost", "resale_value",
    "performance_score", "comfort_features", "suitability",
    "market_supply", "fit_score",
    "comparison_comment", "not_recommended_reason",
    # methods
    "fuel_method", "fee_method", "reliability_method", "maintenance_method",
    "safety_method", "insurance_method", "resale_method", "performance_method",
    "comfort_method", "suitability_method", "supply_method",
]

_ADVISOR_SYNONYMS = {
    "brand": ["brand", "make", "manufacturer"],
    "model": ["model", "trim", "name"],
    "year": ["year", "year_range", "best_year"],
    "engine_cc": ["engine_cc", "engine", "engine_size_cc", "engine_displacement_cc"],
    "price_range_nis": ["price_range_nis", "price_range", "price", "price_nis"],
    "fuel": ["fuel", "fuel_type"],
    "gear": ["gear", "transmission"],
    "turbo": ["turbo", "turbo_required"],
    "avg_fuel_consumption": ["avg_fuel_consumption", "fuel_consumption", "avg_consumption", "consumption"],
    "annual_fee": ["annual_fee", "license_fee", "fee"],
    "reliability_score": ["reliability_score", "reliability", "reliability_index"],
    "maintenance_cost": ["maintenance_cost", "maintenance", "annual_maintenance_cost"],
    "safety_rating": ["safety_rating", "safety", "safety_score"],
    "insurance_cost": ["insurance_cost", "insurance", "annual_insurance_cost"],
    "resale_value": ["resale_value", "resale", "resale_score", "value_retention"],
    "performance_score": ["performance_score", "performance"],
    "comfort_features": ["comfort_features", "comfort", "comfort_score"],
    "suitability": ["suitability", "suitability_score", "match_score"],
    "market_supply": ["market_supply", "supply", "availability"],
    "fit_score": ["fit_score", "fit", "fit_percent"],
    "comparison_comment": ["comparison_comment", "comment", "summary", "why"],
    "not_recommended_reason": ["not_recommended_reason", "warning", "cons", "risks"],
    # methods
    "fuel_method": ["fuel_method", "fuel_calc_method"],
    "fee_method": ["fee_method", "fee_calc_method"],
    "reliability_method": ["reliability_method", "reliability_calc_method"],
    "maintenance_method": ["maintenance_method", "maintenance_calc_method"],
    "safety_method": ["safety_method", "safety_calc_method"],
    "insurance_method": ["insurance_method", "insurance_calc_method"],
    "resale_method": ["resale_method", "resale_calc_method"],
    "performance_method": ["performance_method", "performance_calc_method"],
    "comfort_method": ["comfort_method", "comfort_calc_method"],
    "suitability_method": ["suitability_method", "suitability_calc_method"],
    "supply_method": ["supply_method", "market_supply_method"],
}


def _first_present(d: dict, keys: List[str]):
    for k in keys:
        if k in d and d.get(k) is not None:
            return d.get(k)
    return None


def normalize_advisor_car_item(car: dict) -> dict:
    if not isinstance(car, dict):
        return {}

    raw = dict(car)
    out: Dict[str, Any] = {}

    for canon in _ADVISOR_CANON_KEYS:
        out[canon] = _first_present(raw, _ADVISOR_SYNONYMS.get(canon, [canon]))

    # type fixes
    def to_float(x):
        try:
            if x is None or x == "":
                return None
            return float(x)
        except Exception:
            m = _re.search(r"-?\d+(\.\d+)?", str(x))
            return float(m.group()) if m else None

    def to_int(x):
        try:
            if x is None or x == "":
                return None
            return int(float(x))
        except Exception:
            m = _re.search(r"\d{4}", str(x))
            return int(m.group()) if m else None

    out["year"] = to_int(out.get("year"))
    out["engine_cc"] = to_int(out.get("engine_cc"))
    out["avg_fuel_consumption"] = to_float(out.get("avg_fuel_consumption"))
    out["annual_fee"] = to_float(out.get("annual_fee"))
    out["reliability_score"] = to_float(out.get("reliability_score"))
    out["maintenance_cost"] = to_float(out.get("maintenance_cost"))
    out["safety_rating"] = to_float(out.get("safety_rating"))
    out["insurance_cost"] = to_float(out.get("insurance_cost"))
    out["resale_value"] = to_float(out.get("resale_value"))
    out["performance_score"] = to_float(out.get("performance_score"))
    out["comfort_features"] = to_float(out.get("comfort_features"))
    out["suitability"] = to_float(out.get("suitability"))
    out["fit_score"] = to_float(out.get("fit_score"))

    # price_range can be array [min,max] or string
    pr = out.get("price_range_nis")
    if isinstance(pr, str):
        nums = _re.findall(r"\d+", pr.replace(",", ""))
        if len(nums) >= 2:
            out["price_range_nis"] = [int(nums[0]), int(nums[1])]
        elif len(nums) == 1:
            n = int(nums[0])
            out["price_range_nis"] = [n, n]
    elif isinstance(pr, (list, tuple)) and len(pr) == 2:
        try:
            out["price_range_nis"] = [int(float(pr[0])), int(float(pr[1]))]
        except Exception:
            pass

    # strings cleanup
    for k in ["brand", "model", "fuel", "gear", "market_supply", "comparison_comment", "not_recommended_reason"]:
        if out.get(k) is not None:
            out[k] = str(out[k]).strip()

    return out


def car_advisor_call_gemini_with_search(profile: dict) -> dict:
    global advisor_client
    if advisor_client is None:
        return {"_error": "Gemini Car Advisor client unavailable."}

    # Force schema that matches recommendations.js rendering
    schema_hint = {
        "search_performed": True,
        "search_queries": ["(hebrew queries)"],
        "recommended_cars": [
            {
                "brand": "string",
                "model": "string",
                "year": 2018,
                "engine_cc": 1600,
                "price_range_nis": [60000, 85000],
                "fuel": "בנזין/היברידי/דיזל/חשמלי",
                "gear": "אוטומטית/ידנית",
                "turbo": "כן/לא/לא משנה",
                "avg_fuel_consumption": 15.2,
                "annual_fee": 1400,
                "reliability_score": 8.7,
                "maintenance_cost": 2500,
                "safety_rating": 8.0,
                "insurance_cost": 5200,
                "resale_value": 7.8,
                "performance_score": 6.5,
                "comfort_features": 7.0,
                "suitability": 8.2,
                "market_supply": "גבוה/בינוני/נמוך",
                "fit_score": 87,
                "comparison_comment": "string",
                "not_recommended_reason": "string or empty",
                "fuel_method": "string",
                "fee_method": "string",
                "reliability_method": "string",
                "maintenance_method": "string",
                "safety_method": "string",
                "insurance_method": "string",
                "resale_method": "string",
                "performance_method": "string",
                "comfort_method": "string",
                "suitability_method": "string",
                "supply_method": "string",
            }
        ],
    }

    prompt = f"""
Please recommend cars for an Israeli customer. Here is the user profile (JSON):
{json.dumps(profile, ensure_ascii=False, indent=2)}

You are an independent automotive data analyst for the **Israeli used car market**.

CRITICAL:
- Use Google Search tool.
- Return ONLY ONE top-level JSON object.
- It MUST follow this schema exactly (keys + types), and MUST include "recommended_cars" array:
{json.dumps(schema_hint, ensure_ascii=False, indent=2)}

Return ONLY raw JSON. No backticks.
"""

    search_tool = genai_types.Tool(google_search=genai_types.GoogleSearch())
    config = genai_types.GenerateContentConfig(
        temperature=0.3,
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
                return {"_error": "Invalid JSON object from advisor", "_raw": text}
            return parsed
        except Exception:
            return {"_error": "JSON decode error from Gemini Car Advisor", "_raw": text}
    except Exception as e:
        return {"_error": f"Gemini Car Advisor call failed: {e}"}


def car_advisor_postprocess(profile: dict, parsed: dict) -> dict:
    recommended = parsed.get("recommended_cars") or []
    if not isinstance(recommended, list) or not recommended:
        return {
            "search_performed": bool(parsed.get("search_performed", False)),
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

        # Normalize schema (old/new keys) -> canonical keys used by JS
        car_norm = normalize_advisor_car_item(car)

        fuel_val = str(car_norm.get("fuel", "")).strip()
        gear_val = str(car_norm.get("gear", "")).strip()
        turbo_val = car_norm.get("turbo")

        fuel_norm = fuel_map.get(fuel_val, fuel_val.lower())
        gear_norm = gear_map.get(gear_val, gear_val.lower())
        turbo_norm = turbo_map.get(turbo_val, turbo_val) if isinstance(turbo_val, str) else turbo_val

        avg_fc_num = car_norm.get("avg_fuel_consumption")
        annual_energy_cost = None
        if isinstance(avg_fc_num, (int, float)) and avg_fc_num and avg_fc_num > 0:
            if fuel_norm == "electric":
                annual_energy_cost = (annual_km / 100.0) * float(avg_fc_num) * float(elec_price)
            else:
                # avg_fc_num is "km per liter" per your JS labels
                annual_energy_cost = (annual_km / float(avg_fc_num)) * float(fuel_price)

        maintenance_cost = car_norm.get("maintenance_cost") or 0.0
        insurance_cost = car_norm.get("insurance_cost") or 0.0
        annual_fee = car_norm.get("annual_fee") or 0.0

        total_annual_cost = None
        if annual_energy_cost is not None:
            total_annual_cost = float(annual_energy_cost) + float(maintenance_cost) + float(insurance_cost) + float(annual_fee)

        # Add fields used by your postprocess display (safe for JS)
        car_norm["annual_energy_cost"] = round(annual_energy_cost, 0) if annual_energy_cost is not None else None
        car_norm["annual_fuel_cost"] = car_norm["annual_energy_cost"]
        car_norm["maintenance_cost"] = round(float(maintenance_cost), 0) if maintenance_cost is not None else None
        car_norm["insurance_cost"] = round(float(insurance_cost), 0) if insurance_cost is not None else None
        car_norm["annual_fee"] = round(float(annual_fee), 0) if annual_fee is not None else None
        car_norm["total_annual_cost"] = round(total_annual_cost, 0) if total_annual_cost is not None else None

        # Convert back to Hebrew labels for UI consistency
        car_norm["fuel"] = fuel_map_he.get(fuel_norm, fuel_val or fuel_norm)
        car_norm["gear"] = gear_map_he.get(gear_norm, gear_val or gear_norm)
        car_norm["turbo"] = turbo_map_he.get(turbo_norm, turbo_val)

        processed.append(car_norm)

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

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=2, x_proto=1, x_host=1, x_prefix=1)

    app.config["MAX_CONTENT_LENGTH"] = MAX_JSON_BODY_BYTES
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["WTF_CSRF_HEADERS"] = ["X-CSRFToken", "X-CSRF-Token"]

    # Cookies/session stability
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = True if IS_RENDER else False

    if IS_RENDER and PUBLIC_HOST:
        app.config["SESSION_COOKIE_DOMAIN"] = f".{PUBLIC_HOST}"
    else:
        app.config["SESSION_COOKIE_DOMAIN"] = None

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

    with app.app_context():
        db.create_all()

    # Gemini key init
    GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not GEMINI_API_KEY and IS_RENDER:
        raise RuntimeError("GEMINI_API_KEY missing on Render.")
    genai.configure(api_key=GEMINI_API_KEY)

    if GEMINI_API_KEY:
        try:
            advisor_client = genai3.Client(api_key=GEMINI_API_KEY)
        except Exception:
            advisor_client = None

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
            # optional convenience for templates: {{ csrf_token() }}
        }

    def get_redirect_uri():
        host = (request.host or "").lower()
        if CANONICAL_HOST and CANONICAL_HOST in host:
            return f"https://{CANONICAL_HOST}/auth"
        return request.url_root.rstrip("/") + "/auth"

    # Canonical redirect + security gates
    @app.before_request
    def canonical_and_security_gate():
        host = (request.host or "").lower()
        # strip port
        host_no_port = host.split(":")[0]
        if host_no_port.startswith("www.") and CANONICAL_HOST and host_no_port.endswith(CANONICAL_HOST):
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

    def _should_set_csrf_cookie() -> bool:
        # We set csrf cookie on HTML GET responses so JS can always read it
        if request.method != "GET":
            return False
        if request.path.startswith("/static/"):
            return False
        return True

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        # HSTS only when you're sure HTTPS is enforced (Render)
        if IS_RENDER:
            resp.headers["Strict-Transport-Security"] = "max-age=15552000; includeSubDomains"

        if request.path in ("/analyze", "/advisor_api") or request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"

        # CSRF token cookie for JS (readable by JS, SameSite=Lax)
        if _should_set_csrf_cookie():
            try:
                token = generate_csrf()
                resp.set_cookie(
                    "csrf_token",
                    token,
                    max_age=60 * 60 * 6,
                    secure=True if IS_RENDER else False,
                    httponly=False,       # JS must read it
                    samesite="Lax",
                    path="/",
                )
            except Exception:
                pass

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
        resp = jsonify({"csrf_token": token})
        # also set cookie
        resp.set_cookie(
            "csrf_token",
            token,
            max_age=60 * 60 * 6,
            secure=True if IS_RENDER else False,
            httponly=False,
            samesite="Lax",
            path="/",
        )
        return resp

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

            weights = payload.get("weights") or {"reliability": 5, "resale": 3, "fuel": 4, "performance": 2, "comfort": 3}

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

        if not (isinstance(result, dict) and result.get("search_performed") is True and isinstance(result.get("recommended_cars"), list)):
            return jsonify({"error": "פלט AI לא תקין (Advisor)."}), 500

        try:
            quota_charge_success("advisor")
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[QUOTA] advisor charge failed: {e}")

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
            # matches script.js payload keys exactly
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

        # Cache
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
                result = sanitize_analyze_output(result)
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

        if not (isinstance(model_output, dict) and model_output.get("search_performed") is True and ("base_score_calculated" in model_output)):
            return jsonify({"error": "פלט AI לא תקין (Analyze)."}), 500

        model_output, note = apply_mileage_logic(model_output, final_mileage)
        model_output = sanitize_analyze_output(model_output)

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
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes') and (not IS_RENDER)
    app.run(host='0.0.0.0', port=port, debug=debug)
