from flask import redirect
from app.factory import (
    create_app,
    call_gemini_grounded_once,
    current_user_daily_limit,
    AI_CALL_TIMEOUT_SEC,
    GLOBAL_DAILY_LIMIT,
    USER_DAILY_LIMIT,
    MAX_CACHE_DAYS,
    PER_IP_PER_MIN_LIMIT,
    QUOTA_RESERVATION_TTL_SECONDS,
    MAX_ACTIVE_RESERVATIONS,
)
from app.extensions import db, login_manager, oauth, migrate
import app.extensions as extensions
from app.models import User, SearchHistory, AdvisorHistory, DailyQuotaUsage, QuotaReservation, IpRateLimit, LeasingAdvisorHistory
from app.quota import (
    resolve_app_timezone,
    compute_quota_window,
    reserve_daily_quota,
    finalize_quota_reservation,
    release_quota_reservation,
    check_and_increment_ip_rate_limit,
    log_access_decision,
    cleanup_expired_reservations,
    rollback_quota_increment,
    get_daily_quota_usage,
    get_client_ip,
    parse_owner_emails,
    ModelOutputInvalidError,
    QuotaInternalError,
)

__all__ = [
    "create_app",
    "call_gemini_grounded_once",
    "db",
    "login_manager",
    "oauth",
    "migrate",
    "User",
    "SearchHistory",
    "AdvisorHistory",
    "DailyQuotaUsage",
    "QuotaReservation",
    "IpRateLimit",
    "LeasingAdvisorHistory",
    "resolve_app_timezone",
    "compute_quota_window",
    "reserve_daily_quota",
    "finalize_quota_reservation",
    "release_quota_reservation",
    "check_and_increment_ip_rate_limit",
    "log_access_decision",
    "cleanup_expired_reservations",
    "rollback_quota_increment",
    "get_daily_quota_usage",
    "get_client_ip",
    "parse_owner_emails",
    "ModelOutputInvalidError",
    "QuotaInternalError",
    "AI_CALL_TIMEOUT_SEC",
    "GLOBAL_DAILY_LIMIT",
    "USER_DAILY_LIMIT",
    "MAX_CACHE_DAYS",
    "PER_IP_PER_MIN_LIMIT",
    "QUOTA_RESERVATION_TTL_SECONDS",
    "MAX_ACTIVE_RESERVATIONS",
    "get_ai_client",
    "get_advisor_client",
    "current_user_daily_limit",
    "redirect",
]


def get_ai_client():
    return extensions.ai_client


def get_advisor_client():
    return extensions.advisor_client
