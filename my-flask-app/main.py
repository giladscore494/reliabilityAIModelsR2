# -*- coding: utf-8 -*-
# ===================================================================
# 🚗 Car Reliability Analyzer – Israel
# v7.4.2 (Render DB Hard-Fail + No double create_app + /healthz + date fix)
# ===================================================================

import os, re, json, traceback
import time as pytime
from typing import Optional, Tuple, Any, Dict
from datetime import datetime, time, timedelta

from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    current_user, login_required
)
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix
from json_repair import repair_json
import google.generativeai as genai
import pandas as pd

# --- Gemini 3 (Car Advisor, SDK החדש) ---
from google import genai as genai3
from google.genai import types as genai_types

# --- Input Validation (Security: Tier 2 - S3 + S4) ---
from app.utils.validation import ValidationError, validate_analyze_request
# --- Output Sanitization (Security: Tier 2 - S5 + S6) ---
from app.utils.sanitization import sanitize_analyze_response, sanitize_advisor_response

# ==================================
# === 1. יצירת אובייקטים גלובליים ===
# ==================================
db = SQLAlchemy()
login_manager = LoginManager()
oauth = OAuth()

# Car Advisor – Gemini 3 client (SDK החדש)
advisor_client = None
GEMINI3_MODEL_ID = "gemini-3-pro-preview"

# =========================
# ========= CONFIG ========
# =========================
PRIMARY_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-1.5-flash-latest"
RETRIES = 2
RETRY_BACKOFF_SEC = 1.5
GLOBAL_DAILY_LIMIT = 1000
USER_DAILY_LIMIT = 5
MAX_CACHE_DAYS = 45

# ==================================
# === 2. מודלים של DB (גלובלי) ===
# ==================================
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
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.now)
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
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.now)
    profile_json = db.Column(db.Text, nullable=False)
    result_json = db.Column(db.Text, nullable=False)


# ==================================
# === 3. פונקציות עזר (גלובלי) ===
# ==================================

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
                print(f"[AI] Calling {model_name} (attempt {attempt})")
                resp = llm.generate_content(prompt)
                raw = (getattr(resp, "text", "") or "").strip()
                try:
                    m = _re.search(r"\{.*\}", raw, _re.DOTALL)
                    data = json.loads(m.group()) if m else json.loads(raw)
                except Exception:
                    data = json.loads(repair_json(raw))
                print("[AI] ✅ success")
                return data
            except Exception as e:
                print(f"[AI] ⚠️ {model_name} attempt {attempt} failed: {e}")
                last_err = e
                if attempt < RETRIES:
                    pytime.sleep(RETRY_BACKOFF_SEC)
                continue
    raise RuntimeError(f"Model failed: {repr(last_err)}")


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


def car_advisor_call_gemini_with_search(profile: dict) -> dict:
    """
    קריאה ל-Gemini 3 Pro (SDK החדש) עם Google Search ו-output כ-JSON בלבד.
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

    try:
        resp = advisor_client.models.generate_content(
            model=GEMINI3_MODEL_ID,
            contents=prompt,
            config=config,
        )
        text = getattr(resp, "text", "") or ""
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_error": "JSON decode error from Gemini Car Advisor", "_raw": text}
    except Exception as e:
        return {"_error": f"Gemini Car Advisor call failed: {e}"}


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


# ========================================
# ===== ★★★ 4. פונקציית ה-Factory ★★★ =====
# ========================================
def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # ---- בעל מערכת (למנוע ההמלצות) ----
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

    def log_rejection(reason: str, details: str = "") -> None:
        """
        Safely log rejection reasons without exposing sensitive data.
        This function is defined inside create_app to access Flask context (current_user, request).
        
        Args:
            reason: Short category (unauthenticated, quota, validation, server_error)
            details: Safe description of the issue (no secrets, API keys, or DB details)
        """
        user_id = current_user.id if current_user.is_authenticated else "anonymous"
        endpoint = request.endpoint or "unknown"
        print(f"[REJECT] endpoint={endpoint} user={user_id} reason={reason} details={details}")

    @app.context_processor
    def inject_template_globals():
        return {
            "is_logged_in": current_user.is_authenticated,
            "current_user": current_user,
            "is_owner": is_owner_user(),
        }

    # פונקציה חכמה לבחירת redirect_uri
    def get_redirect_uri():
        """
        Build OAuth redirect URI based on the current request host.
        - Custom domain stays fixed.
        - Otherwise, use the current host (Render/local/etc).
        """
        host = (request.host or "").lower()
        if "yedaarechev.com" in host:
            uri = "https://yedaarechev.com/auth"
        else:
            # request.url_root already includes scheme + host (ProxyFix handles X-Forwarded-Proto/Host)
            uri = request.url_root.rstrip("/") + "/auth"
        print(f"[AUTH] Using redirect_uri={uri} (host={host})")
        return uri

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

    # Config
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url if db_url else "sqlite:///:memory:"
    app.config["SECRET_KEY"] = secret_key if secret_key else "dev-secret-key-that-is-not-secret"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # ===== SECURITY:  Session Cookie Configuration (Tier 1) =====
    app.config["SESSION_COOKIE_SECURE"] = True
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

    # Init
    db.init_app(app)
    login_manager.init_app(app)
    oauth.init_app(app)

    login_manager.login_view = 'login'

    # Handle unauthorized access for AJAX/JSON requests
    @login_manager.unauthorized_handler
    def unauthorized():
        """Return 401 for AJAX/JSON requests, otherwise redirect to login."""
        if request.is_json or request.accept_mimetypes.accept_json:
            log_rejection("unauthenticated", "User not logged in, no valid session")
            return jsonify({"error": "אנא התחבר כדי להשתמש בשירות זה"}), 401
        return redirect(url_for('login'))

    # ==========================
    # ✅ Run create_all ONLY ONCE
    # ==========================
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

    # Gemini key
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    if not GEMINI_API_KEY:
        print("[AI] ⚠️ GEMINI_API_KEY missing")
    genai.configure(api_key=GEMINI_API_KEY)

    # Gemini 3 client עבור Car Advisor (SDK החדש)
    global advisor_client
    if GEMINI_API_KEY:
        try:
            advisor_client = genai3.Client(api_key=GEMINI_API_KEY)
            print("[CAR-ADVISOR] ✅ Gemini 3 client initialized")
        except Exception as e:
            advisor_client = None
            print(f"[CAR-ADVISOR] ❌ Failed to init Gemini 3 client: {e}")
    else:
        advisor_client = None

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

    # Health check endpoint (Render Health Checks can hit /healthz)
    @app.route('/healthz')
    def healthz():
        return "ok", 200

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
        return oauth.google.authorize_redirect(redirect_uri, state=None)

    @app.route('/auth')
    def auth():
        try:
            token = oauth.google.authorize_access_token()
            userinfo = oauth.google.get('userinfo').json()
            user = User.query.filter_by(google_id=userinfo['id']).first()
            if not user:
                user = User(
                    google_id=userinfo['id'],
                    email=userinfo.get('email', ''),
                    name=userinfo.get('name', '')
                )
                db.session.add(user)
                db.session.commit()
            login_user(user)
            return redirect(url_for('index'))
        except Exception as e:
            print(f"[AUTH] ❌ {e}")
            traceback.print_exc()
            try:
                logout_user()
            except Exception:
                pass
            return redirect(url_for('index'))

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('index'))

    # Legal pages
    @app.route('/privacy')
    def privacy():
        return render_template(
            'privacy.html',
            user=current_user,
            is_owner=is_owner_user(),
        )

    @app.route('/terms')
    def terms():
        return render_template(
            'terms.html',
            user=current_user,
            is_owner=is_owner_user(),
        )

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

    # ✅ NEW ROUTE: שליפת פרטים לדשבורד (AJAX)
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

    # ===========================
    # 🔹 Car Advisor – עמוד HTML
    # ===========================
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
    def advisor_api():
        """
        מקבל profile מה-JS (recommendations.js),
        בונה user_profile מלא כמו ב-Car Advisor (Streamlit),
        קורא ל-Gemini 3 Pro, שומר היסטוריה ומחזיר JSON מוכן להצגה.
        """
        # Log access decision
        user_id = current_user.id if current_user.is_authenticated else None
        log_access_decision('/advisor_api', user_id, 'allowed', 'authenticated user')
        
        try:
            payload = request.get_json(force=True) or {}
        except Exception:
            log_access_decision('/advisor_api', user_id, 'rejected', 'validation error: invalid JSON')
            return jsonify({"error": "קלט JSON לא תקין"}), 400

        # Validate request before processing
        try:
            validated = validate_analyze_request(payload)
            payload = validated
        except ValidationError as e:
            log_access_decision('/advisor_api', user_id, 'rejected', f'validation error: {e.field}')
            return jsonify({'error': f'{e.field}: {e.message}'}), 400

        try:
            # ---- שלב 1: בסיסי ----
            budget_min = float(payload.get("budget_min", 0))
            budget_max = float(payload.get("budget_max", 0))
            year_min = int(payload.get("year_min", 2000))
            year_max = int(payload.get("year_max", 2025))

            fuels_he = payload.get("fuels_he") or []
            gears_he = payload.get("gears_he") or []
            turbo_choice_he = payload.get("turbo_choice_he", "לא משנה")

            # ---- שלב 2: שימוש וסגנון ----
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
                excluded_colors = [
                    s.strip() for s in excluded_colors.split(",") if s.strip()
                ]

            # ---- שלב 3: סדר עדיפויות ----
            weights = payload.get("weights") or {
                "reliability": 5,
                "resale": 3,
                "fuel": 4,
                "performance": 2,
                "comfort": 3,
            }

            # ---- שלב 4: פרטים נוספים ----
            insurance_history = payload.get("insurance_history", "") or ""
            violations = payload.get("violations", "אין") or "אין"

            family_size = payload.get("family_size", "1-2") or "1-2"
            cargo_need = payload.get("cargo_need", "בינוני") or "בינוני"

            safety_required = payload.get("safety_required")
            if not safety_required:
                safety_required = payload.get("safety_required_radio", "כן")
            if not safety_required:
                safety_required = "כן"

            trim_level = payload.get("trim_level", "סטנדרטי") or "סטנדרטי"

            consider_supply = payload.get("consider_supply", "כן") or "כן"
            consider_market_supply = (consider_supply == "כן")

            fuel_price = float(payload.get("fuel_price", 7.0))
            electricity_price = float(payload.get("electricity_price", 0.65))

        except Exception as e:
            log_access_decision('/advisor_api', user_id, 'rejected', f'validation error: {str(e)}')
            return jsonify({"error": f"שגיאת קלט: {e}"}), 400

        # --- מיפוי דלק/גיר/טורבו מהעברית לערכים לוגיים ---
        fuels = [fuel_map.get(f, "gasoline") for f in fuels_he] if fuels_he else ["gasoline"]

        if "חשמלי" in fuels_he:
            gears = ["automatic"]
        else:
            gears = [gear_map.get(g, "automatic") for g in gears_he] if gears_he else ["automatic"]

        turbo_choice = turbo_map.get(turbo_choice_he, "any")

        # --- בניית user_profile כמו ב-Car Advisor (Streamlit) ---
        user_profile = make_user_profile(
            budget_min,
            budget_max,
            [year_min, year_max],
            fuels,
            gears,
            turbo_choice,
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
        )

        # שדות נוספים
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
            log_access_decision('/advisor_api', user_id, 'error', f'AI error: {parsed.get("_error")}')
            return jsonify({"error": parsed["_error"], "raw": parsed.get("_raw")}), 500

        result = car_advisor_postprocess(user_profile, parsed)

        # 🔴 שמירת היסטוריית המלצות למאגר
        try:
            rec_log = AdvisorHistory(
                user_id=current_user.id,
                profile_json=json.dumps(user_profile, ensure_ascii=False),
                result_json=json.dumps(result, ensure_ascii=False),
            )
            db.session.add(rec_log)
            db.session.commit()
        except Exception as e:
            print(f"[DB] ⚠️ failed to save advisor history: {e}")
            db.session.rollback()

        # Sanitize output before returning
        result = sanitize_advisor_response(result)
        
        return jsonify(result)

    @app.route('/analyze', methods=['POST'])
    @login_required
    def analyze_car():
        # Log access decision
        user_id = current_user.id if current_user.is_authenticated else None
        log_access_decision('/analyze', user_id, 'allowed', 'authenticated user')
        
        # 0) Input validation
        try:
            data = request.json
            if not data:
                return jsonify({'error': 'Invalid JSON'}), 400
            
            # Validate request against schema
            validated = validate_analyze_request(data)
            
            print(f"[ANALYZE 0/6] user={current_user.id} payload: {validated}")
            final_make = normalize_text(validated.get('make'))
            final_model = normalize_text(validated.get('model'))
            final_sub_model = normalize_text(validated.get('sub_model'))
            final_year = int(validated.get('year')) if validated.get('year') else None
            final_mileage = str(validated.get('mileage_range'))
            final_fuel = str(validated.get('fuel_type'))
            final_trans = str(validated.get('transmission'))
            if not (final_make and final_model and final_year):
                log_access_decision('/analyze', user_id, 'rejected', 'validation error: missing required fields')
                return jsonify({"error": "שגיאת קלט (שלב 0): נא למלא יצרן, דגם ושנה"}), 400
        except ValidationError as e:
            log_access_decision('/analyze', user_id, 'rejected', f'validation error: {e.field}')
            return jsonify({'error': f'{e.field}: {e.message}'}), 400
        except Exception as e:
            log_access_decision('/analyze', user_id, 'rejected', f'validation error: {str(e)}')
            return jsonify({"error": f"שגיאת קלט (שלב 0): {str(e)}"}), 400

        # 1) User quota
        try:
            today_start = datetime.combine(datetime.today().date(), time.min)
            today_end = datetime.combine(datetime.today().date(), time.max)
            user_searches_today = SearchHistory.query.filter(
                SearchHistory.user_id == current_user.id,
                SearchHistory.timestamp >= today_start,
                SearchHistory.timestamp <= today_end
            ).count()
            if user_searches_today >= USER_DAILY_LIMIT:
                log_access_decision('/analyze', user_id, 'rejected', f'quota exceeded: {user_searches_today}/{USER_DAILY_LIMIT}')
                return jsonify({"error": f"שגיאת מגבלה (שלב 1): ניצלת את {USER_DAILY_LIMIT} החיפושים היומיים שלך. נסה שוב מחר."}), 429
        except Exception as e:
            log_rejection("server_error", f"Quota check failed: {type(e).__name__}")
            traceback.print_exc()
            log_access_decision('/analyze', user_id, 'error', f'server error in quota check: {str(e)}')
            return jsonify({"error": f"שגיאת שרת (שלב 1): {str(e)}"}), 500

        # 2–3) Cache
        try:
            cutoff_date = datetime.now() - timedelta(days=MAX_CACHE_DAYS)
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
                # ✅ date format fix: YYYY-MM-DD
                result['source_tag'] = f"מקור: מטמון DB (נשמר ב-{cached.timestamp.strftime('%Y-%m-%d')})"
                # Sanitize cached output before returning
                result = sanitize_analyze_response(result)
                return jsonify(result)
        except Exception as e:
            print(f"[CACHE] ⚠️ {e}")

        # 4) AI call
        try:
            prompt = build_prompt(
                final_make, final_model, final_sub_model, final_year,
                final_fuel, final_trans, final_mileage
            )
            model_output = call_model_with_retry(prompt)
        except Exception as e:
            log_rejection("server_error", f"AI model call failed: {type(e).__name__}")
            traceback.print_exc()
            return jsonify({"error": f"שגיאת AI (שלב 4): {str(e)}"}), 500

        # 5) Mileage logic
        model_output, note = apply_mileage_logic(model_output, final_mileage)

        # 6) Save
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
            print(f"[DB] ⚠️ save failed: {e}")
            db.session.rollback()

        model_output['source_tag'] = f"מקור: ניתוח AI חדש (חיפוש {user_searches_today + 1}/{USER_DAILY_LIMIT})"
        model_output['mileage_note'] = note
        model_output['km_warn'] = False
        
        # Sanitize output before returning
        model_output = sanitize_analyze_response(model_output)
        
        return jsonify(model_output)

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
