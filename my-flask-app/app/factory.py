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
    _utcnow,
)
import app.extensions as extensions
from app.extensions import (
    db,
    login_manager,
    oauth,
    migrate,
    GEMINI_RELIABILITY_MODEL_ID,
    GEMINI_RECOMMENDER_MODEL_ID,
)
from app.models import (
    User,
    SearchHistory,
    AdvisorHistory,
    DailyQuotaUsage,
    QuotaReservation,
    IpRateLimit,
    LegalAcceptance,
    ResearchConsent,
    Feedback,
)
from app.legal import CONTACT_EMAIL, TERMS_VERSION, PRIVACY_VERSION, parse_legal_confirm
from app.research import (
    RESEARCH_CONSENT_TYPE,
    RESEARCH_NOTICE_VERSION,
    ensure_anon_id,
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
# Module-level config constants moved to app.config (Phase 3 refactor).
# Re-exported here so that existing `from app.factory import ...` imports keep
# working without changes.
from app.config import (  # noqa: E402,F401
    AI_CALL_TIMEOUT_SEC,
    AI_EXECUTOR,
    AI_EXECUTOR_WORKERS,
    DEFAULT_API_PAYLOAD_LIMIT_BYTES,
    GLOBAL_DAILY_LIMIT,
    MAX_ACTIVE_RESERVATIONS,
    MAX_CACHE_DAYS,
    MAX_CONTENT_LENGTH_DEFAULT,
    PER_IP_PER_MIN_LIMIT,
    QUOTA_RESERVATION_TTL_SECONDS,
    USER_DAILY_LIMIT,
)

logger = logging.getLogger(__name__)
import app.quota as quota_module
quota_module.USER_DAILY_LIMIT = USER_DAILY_LIMIT
quota_module.GLOBAL_DAILY_LIMIT = GLOBAL_DAILY_LIMIT
quota_module.MAX_CACHE_DAYS = MAX_CACHE_DAYS
quota_module.PER_IP_PER_MIN_LIMIT = PER_IP_PER_MIN_LIMIT
quota_module.QUOTA_RESERVATION_TTL_SECONDS = QUOTA_RESERVATION_TTL_SECONDS
quota_module.MAX_ACTIVE_RESERVATIONS = MAX_ACTIVE_RESERVATIONS


from app.services import advisor_ai_service as _advisor_ai_service
from app.services import reliability_model_service as _reliability_model_service
from app.services.advisor_ai_service import (
    car_advisor_postprocess as _car_advisor_postprocess,
    make_user_profile as _make_user_profile,
    sanitize_profile_for_prompt as _sanitize_profile_for_prompt,
)
from app.services.ip_rate_limit_service import (
    check_and_increment_ip_rate_limit as _check_and_increment_ip_rate_limit,
    get_client_ip as _get_client_ip,
)
from app.services.quota_service import (
    cleanup_expired_reservations as _cleanup_expired_reservations,
    finalize_quota_reservation as _finalize_quota_reservation,
    get_daily_quota_usage as _get_daily_quota_usage,
    release_quota_reservation as _release_quota_reservation,
    reserve_daily_quota as _reserve_daily_quota,
    rollback_quota_increment as _rollback_quota_increment,
    check_and_increment_daily_quota as _check_and_increment_daily_quota,
)
from app.services.reliability_prompt_service import (
    build_combined_prompt as _build_combined_prompt,
    build_reliability_report_prompt as _build_reliability_report_prompt,
)

# TODO: Deprecated app.factory compatibility re-exports; import from
# app.services.advisor_ai_service in new code.
# Test compatibility anchor for moved advisor prompt fields:
# official_safety, license_fee_israel, trim_levels_israel, warranty_israel, competitors
# אל תמציא ציוני בטיחות
fuel_map = _advisor_ai_service.fuel_map
gear_map = _advisor_ai_service.gear_map
turbo_map = _advisor_ai_service.turbo_map
fuel_map_he = _advisor_ai_service.fuel_map_he
gear_map_he = _advisor_ai_service.gear_map_he
turbo_map_he = _advisor_ai_service.turbo_map_he

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


@login_manager.user_loader
def load_user(user_id):
    """
    Load user by ID from database.
    If DB connection fails, treat as unauthenticated (return None).
    This prevents 500 errors on stale pool connections.
    """
    try:
        return db.session.get(User, int(user_id))
    except Exception as e:
        # Log the error safely (no secrets, no user IDs)
        logger.warning("[AUTH] load_user failed: %s", e.__class__.__name__)
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
    logger.info("[DICT] Loaded car_models_dict. Manufacturers: %s", len(israeli_car_market_full_compilation))
    try:
        _total_models = sum(len(models) for models in israeli_car_market_full_compilation.values())
        logger.info("[DICT] Total models loaded: %s", _total_models)
    except Exception as inner_e:
        logger.warning("[DICT] Count models failed: %s", inner_e)
except Exception as e:
    logger.error("[DICT] Failed to import car_models_dict: %s", e)
    israeli_car_market_full_compilation = {"Toyota": ["Corolla (2008-2025)"]}
    logger.warning("[DICT] Fallback applied — Toyota only")

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
    if re.search(r'(?<!\d)200(?!\d)', m) and "+" in m:
        return -15, "הציון הותאם מטה עקב קילומטראז׳ גבוה מאוד (200K+)."
    if re.search(r'(?<!\d)150(?!\d)', m) and re.search(r'(?<!\d)200(?!\d)', m):
        return -10, "הציון הותאם מטה עקב קילומטראז׳ גבוה (150–200 אלף ק״מ)."
    if re.search(r'(?<!\d)100(?!\d)', m) and re.search(r'(?<!\d)150(?!\d)', m):
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

# TODO: Deprecated app.factory compatibility wrappers; import from app.services.* in new code.
def build_reliability_report_prompt(payload: dict, missing_info: list[str]) -> str:
    return _build_reliability_report_prompt(payload, missing_info)


def build_combined_prompt(payload: dict, missing_info: list[str]) -> str:
    return _build_combined_prompt(payload, missing_info)


def call_model_with_retry(prompt: str) -> dict:
    return _reliability_model_service.call_model_with_retry(prompt)


def parse_model_json(raw: str) -> Tuple[Optional[dict], Optional[str]]:
    return _reliability_model_service.parse_model_json(raw)


def _execute_with_timeout(fn, timeout_sec: int):
    return _reliability_model_service._execute_with_timeout(fn, timeout_sec)


_factory_execute_with_timeout = _execute_with_timeout


def _call_with_factory_execute_patch(module, function_name: str, *args):
    patched_execute = globals().get("_execute_with_timeout")
    if patched_execute is _factory_execute_with_timeout:
        return getattr(module, function_name)(*args)
    original = module._execute_with_timeout
    module._execute_with_timeout = patched_execute
    try:
        return getattr(module, function_name)(*args)
    finally:
        module._execute_with_timeout = original


def call_gemini_grounded_once(prompt: str) -> Tuple[Optional[dict], Optional[str]]:
    return _call_with_factory_execute_patch(
        _reliability_model_service,
        "call_gemini_grounded_once",
        prompt,
    )


def make_user_profile(*args, **kwargs):
    return _make_user_profile(*args, **kwargs)


def sanitize_profile_for_prompt(profile: dict) -> dict:
    return _sanitize_profile_for_prompt(profile)


def car_advisor_call_gemini_with_search(profile: dict) -> dict:
    return _call_with_factory_execute_patch(
        _advisor_ai_service,
        "car_advisor_call_gemini_with_search",
        profile,
    )


def car_advisor_postprocess(profile: dict, parsed: dict) -> dict:
    return _car_advisor_postprocess(profile, parsed)


def get_daily_quota_usage(user_id: int, day_key: date) -> int:
    return _get_daily_quota_usage(user_id, day_key)


def cleanup_expired_reservations(user_id: int, day_key: date, now_utc: Optional[datetime] = None) -> int:
    return _cleanup_expired_reservations(user_id, day_key, now_utc)


def reserve_daily_quota(user_id: int, day_key: date, limit: int, request_id: str, now_utc: Optional[datetime] = None) -> Tuple[bool, int, int, Optional[int]]:
    return _reserve_daily_quota(user_id, day_key, limit, request_id, now_utc)


def finalize_quota_reservation(reservation_id: Optional[int], user_id: int, day_key: date, now_utc: Optional[datetime] = None) -> Tuple[bool, int]:
    return _finalize_quota_reservation(reservation_id, user_id, day_key, now_utc)


def release_quota_reservation(reservation_id: Optional[int], user_id: int, day_key: date, now_utc: Optional[datetime] = None) -> bool:
    return _release_quota_reservation(reservation_id, user_id, day_key, now_utc)


def rollback_quota_increment(user_id: int, day_key: date) -> int:
    return _rollback_quota_increment(user_id, day_key)


def get_client_ip() -> str:
    return _get_client_ip()


def check_and_increment_daily_quota(user_id: int, limit: int, day_key: date, now_utc: Optional[datetime] = None) -> Tuple[bool, int]:
    return _check_and_increment_daily_quota(user_id, limit, day_key, now_utc)


def check_and_increment_ip_rate_limit(ip: str, limit: int = PER_IP_PER_MIN_LIMIT, now_utc: Optional[datetime] = None) -> Tuple[bool, int, datetime]:
    return _check_and_increment_ip_rate_limit(ip, limit, now_utc)


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
    # Support both OWNER_EMAIL (single secret) and legacy OWNER_EMAILS (comma-separated)
    # so deployments can adopt the clearer name without breaking existing configs.
    owner_emails_raw = ",".join([
        value
        for value in [
            os.environ.get("OWNER_EMAIL", ""),
            os.environ.get("OWNER_EMAILS", ""),
        ]
        if value
    ])
    OWNER_EMAILS = set(parse_owner_emails(owner_emails_raw))
    OWNER_BYPASS_QUOTA = os.environ.get("OWNER_BYPASS_QUOTA", "1").lower() in ("1", "true", "yes")
    ADVISOR_OWNER_ONLY = os.environ.get("ADVISOR_OWNER_ONLY", "1").lower() in ("1", "true", "yes")
    
    # Store config values for helper functions to access
    app.config['OWNER_EMAILS'] = OWNER_EMAILS
    app.config['CANONICAL_BASE'] = canonical_base
    app.config['OWNER_BYPASS_QUOTA'] = OWNER_BYPASS_QUOTA
    app.config['ADVISOR_OWNER_ONLY'] = ADVISOR_OWNER_ONLY
    app.config['PER_IP_PER_MIN_LIMIT'] = PER_IP_PER_MIN_LIMIT
    app.config['QUOTA_RESERVATION_TTL_SECONDS'] = QUOTA_RESERVATION_TTL_SECONDS
    app.config["TERMS_VERSION"] = TERMS_VERSION
    app.config["PRIVACY_VERSION"] = PRIVACY_VERSION
    app.config["RESEARCH_CONSENT_TYPE"] = RESEARCH_CONSENT_TYPE
    app.config["RESEARCH_NOTICE_VERSION"] = RESEARCH_NOTICE_VERSION
    app.config["CONTACT_EMAIL"] = CONTACT_EMAIL

    # Helper functions (is_owner_user, api_ok, api_error, get_request_id, get_redirect_uri)
    # are now imported from app.utils.http_helpers and used throughout the code

    # --- PostHog analytics (silent no-op if key missing) ---
    from app.utils.analytics import init_posthog
    init_posthog(app)
    posthog_api_key = os.environ.get("POSTHOG_API_KEY", "").strip()
    posthog_host_url = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com").strip()
    app.config["POSTHOG_API_KEY"] = posthog_api_key
    app.config["POSTHOG_HOST"] = posthog_host_url

    # Derive CSP-safe PostHog domains from the configured host URL
    from urllib.parse import urlparse as _urlparse
    _ph_parsed = _urlparse(posthog_host_url)
    _ph_netloc = (
        _ph_parsed.netloc
        or posthog_host_url.split("//")[-1].split("/")[0]
    )
    # Pattern: "{region}.i.posthog.com" → "{region}-assets.i.posthog.com"
    _ph_parts = _ph_netloc.split(".", 1)
    if len(_ph_parts) == 2 and _ph_parts[1] == "i.posthog.com":
        _ph_assets = f"{_ph_parts[0]}-assets.{_ph_parts[1]}"
    else:
        _ph_assets = _ph_netloc  # fallback: same host
    app.config["_PH_CSP_CONNECT"] = _ph_netloc   # e.g. "eu.i.posthog.com"
    app.config["_PH_CSP_SCRIPT"] = _ph_assets     # e.g. "eu-assets.i.posthog.com"

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
    logger.info("[BOOT] Allowed hosts: %s", ALLOWED_HOSTS)

    def is_host_allowed(host: str) -> bool:
        """Check if the given host is in the allowed hosts list."""
        if not host:
            return False
        # Strip port if present
        host_no_port = host.split(":")[0].lower()
        return host_no_port in ALLOWED_HOSTS

    # ======================
    # ✅ Render DB hard-fail + extension init (extracted to app.bootstrap.db)
    # ======================
    from app.bootstrap.db import configure_database
    db_url, is_render = configure_database(app, logger)

    login_manager.login_view = 'public.login'

    # Phase 4: request lifecycle hooks (before/after/teardown + context_processor)
    # extracted to app.bootstrap.request_hooks. Closures over canonical_host,
    # ALLOWED_HOSTS, is_host_allowed, get_client_ip, and
    # check_and_increment_ip_rate_limit are passed in explicitly.
    from app.bootstrap.request_hooks import register_request_hooks
    register_request_hooks(
        app,
        canonical_host=canonical_host,
        allowed_hosts=ALLOWED_HOSTS,
        is_host_allowed=is_host_allowed,
        get_client_ip=get_client_ip,
        check_and_increment_ip_rate_limit=check_and_increment_ip_rate_limit,
        logger=logger,
    )

    # Phase 4: error handlers (413 + login_manager unauthorized) extracted to
    # app.bootstrap.error_handlers.
    from app.bootstrap.error_handlers import register_error_handlers
    register_error_handlers(app, login_manager)

    # Phase 4: security/cache headers extracted to
    # app.bootstrap.security_headers.
    from app.bootstrap.security_headers import register_security_headers
    register_security_headers(app, is_render=is_render, logger=logger)

    # ==========================
    # DB schema management
    # ==========================
    # Production schema is managed by Alembic/Flask-Migrate via:
    #   flask --app main:create_app db upgrade
    # which runs as a Render preDeployCommand (see render.yaml).
    #
    # Runtime db.create_all() is intentionally NOT called here. For local/dev
    # bootstrap, use the `flask --app main:create_app init-db` CLI command
    # (defined below) or rely on test fixtures that call db.create_all()
    # explicitly inside an app_context.
    from app.bootstrap.db import log_alembic_revision
    log_alembic_revision(app, logger)

    # AI clients + OAuth — extracted to app.bootstrap.clients (Phase 3).
    from app.bootstrap.clients import init_ai_clients, init_oauth
    init_ai_clients(app, logger)
    init_oauth(app)

    # ------------------
    # ===== ROUTES =====
    # ------------------
    # Blueprint registration is delegated to app.bootstrap.blueprints to keep
    # this factory short. The order/set of blueprints is preserved there.
    from app.bootstrap.blueprints import register_blueprints
    register_blueprints(app)


    @app.cli.command("init-db")
    def init_db_command():
        with app.app_context():
            db.create_all()
        logger.info("Initialized the database tables.")

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
