# -*- coding: utf-8 -*-
"""
Debug Pack (Production-safe)
- Request ID לכל בקשה
- לוכד חריגות + CSRFError + RateLimit + BadRequest ומחזיר JSON מפורט
- שומר אירועים ל-DB (SQLAlchemy) כדי שתוכל לקרוא /owner/debug/events
- מקבל גם שגיאות מהדפדפן דרך /api/client-error
- נותן "איך לתקן" בצורה דטרמיניסטית (בלי AI)
"""

from __future__ import annotations

import os
import re
import time
import uuid
import json
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException, BadRequest, Forbidden, TooManyRequests
from flask_wtf.csrf import CSRFError

# Optional: אם יש flask-login בפרויקט
try:
    from flask_login import current_user  # type: ignore
except Exception:
    current_user = None  # type: ignore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rid() -> str:
    return uuid.uuid4().hex[:12]


def _safe_str(x: Any, limit: int = 4000) -> str:
    try:
        s = str(x)
    except Exception:
        s = "<unprintable>"
    if len(s) > limit:
        return s[:limit] + "…(truncated)"
    return s


def _sanitize_headers(h: Dict[str, str]) -> Dict[str, str]:
    # לא לשמור סודות
    redacted = {}
    for k, v in h.items():
        lk = k.lower()
        if any(t in lk for t in ["authorization", "cookie", "token", "secret", "key"]):
            redacted[k] = "<redacted>"
        else:
            redacted[k] = _safe_str(v, 500)
    return redacted


def _sanitize_json(obj: Any, limit: int = 2000) -> Any:
    # לא לשמור מידע "כבד" / סודות
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "<non-serializable>"
    if len(s) <= limit:
        return obj
    return "<json too large - truncated>"


def _suggest_fix(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    מחזיר:
    - probable_cause
    - how_to_fix (צעדים קונקרטיים)
    """
    status = event.get("status_code")
    err_type = (event.get("error_type") or "").lower()
    path = event.get("path") or ""
    msg = (event.get("message") or "").lower()

    # CSRF
    if err_type == "csrferror" or "csrf" in msg or status == 403:
        return {
            "probable_cause": "CSRF token חסר/לא תואם בבקשת POST/PUT/DELETE.",
            "how_to_fix": [
                "בצד לקוח: למשוך token מ-GET /api/csrf פעם אחת.",
                "בכל POST לשלוח header בשם X-CSRFToken (או X-CSRF-Token) עם הערך.",
                "לוודא שיש credentials (cookies) לאותו דומיין (same-origin).",
                "אם יש fetch: להוסיף headers Content-Type: application/json + Accept: application/json.",
                "אם עדיין 403: לבדוק שאין לך שתי מערכות CSRF במקביל (Flask-WTF + middleware אחר).",
            ],
        }

    # Rate limit
    if status == 429 or "ratelimit" in msg or isinstance(event.get("exception_class"), str) and "RateLimit" in event.get("exception_class"):
        return {
            "probable_cause": "נחסמת בגלל Rate Limit (Limiter).",
            "how_to_fix": [
                "להגדיל limit לנתיב הרלוונטי או להחריג endpoints פנימיים.",
                "לוודא שהדפדפן לא שולח שתי בקשות כפולות (double submit / double click).",
                "להוסיף debounce ב-JS או disable לכפתור בזמן טעינה.",
            ],
        }

    # Bad JSON / Validation
    if status == 400 or err_type in ["badrequest", "jsondecodeerror"]:
        return {
            "probable_cause": "JSON לא תקין או חסרים שדות חובה.",
            "how_to_fix": [
                "בצד לקוח: לשלוח JSON בלבד (לא FormData) אם השרת מצפה ל-JSON.",
                "להוסיף Content-Type: application/json.",
                "להדפיס ב-console את payload שנשלח ולוודא ששם השדות תואם לשרת.",
            ],
        }

    # Auth
    if status in (401, 302) or "login" in msg or "unauthorized" in msg:
        return {
            "probable_cause": "בעיה בהזדהות/סשן (לא מחובר או עוגיות לא נשמרות).",
            "how_to_fix": [
                "לוודא שהבקשה נשלחת לאותו דומיין (yedaarechev.com) ולא ל-Render URL במקביל.",
                "לבדוק ש-cookie של session לא נחסם (SameSite/HTTPS).",
                "אם יש reverse proxy: לוודא ProxyFix מוגדר נכון.",
            ],
        }

    # Default
    return {
        "probable_cause": "תקלה כללית (שרת/לוגיקה/שגיאת קוד).",
        "how_to_fix": [
            "לפתוח /owner/debug/events ולמצוא את האירוע האחרון עם request_id.",
            "לבדוק error_type + traceback.",
            "אם זו שגיאת KeyError/AttributeError: חסר שדה או מודל לא תואם.",
        ],
    }


def init_debug_pack(app, db, limiter=None) -> None:
    """
    app: Flask instance
    db: SQLAlchemy instance
    limiter: Flask-Limiter instance (אופציונלי)
    """

    # --- Model בתוך init כדי שלא ישבור import סדר ---
    class DebugEvent(db.Model):  # type: ignore
        __tablename__ = "debug_events"

        id = db.Column(db.Integer, primary_key=True)
        ts_utc = db.Column(db.String(40), nullable=False, index=True)
        request_id = db.Column(db.String(40), nullable=False, index=True)

        kind = db.Column(db.String(40), nullable=False)  # http / exception / client_error
        method = db.Column(db.String(10), nullable=True)
        path = db.Column(db.String(400), nullable=True)
        status_code = db.Column(db.Integer, nullable=True)

        message = db.Column(db.Text, nullable=True)
        error_type = db.Column(db.String(120), nullable=True)
        exception_class = db.Column(db.String(200), nullable=True)

        remote_addr = db.Column(db.String(80), nullable=True)
        user_agent = db.Column(db.String(400), nullable=True)

        headers_json = db.Column(db.Text, nullable=True)
        body_json = db.Column(db.Text, nullable=True)

        traceback_text = db.Column(db.Text, nullable=True)
        suggestion_json = db.Column(db.Text, nullable=True)

    # חשיפה למודול למקרה שתצטרך (לא חובה)
    app.DebugEvent = DebugEvent  # type: ignore

    debug_bp = Blueprint("debug_bp", __name__)

    def _owner_ok() -> bool:
        # 1) מפתח דרך header
        owner_key = (request.headers.get("X-Owner-Key") or "").strip()
        env_key = (os.getenv("OWNER_DEBUG_KEY") or "").strip()
        if env_key and owner_key and owner_key == env_key:
            return True

        # 2) רשימת מיילים מורשים (אם flask-login קיים)
        env_emails = (os.getenv("OWNER_EMAILS") or "").strip()
        if env_emails and current_user is not None:
            try:
                if current_user.is_authenticated:
                    allowed = {e.strip().lower() for e in env_emails.split(",") if e.strip()}
                    u_email = (getattr(current_user, "email", "") or "").strip().lower()
                    if u_email and u_email in allowed:
                        return True
            except Exception:
                pass

        return False

    def _store_event(ev: Dict[str, Any]) -> None:
        try:
            rec = DebugEvent(
                ts_utc=_utc_now_iso(),
                request_id=ev.get("request_id") or _rid(),
                kind=ev.get("kind") or "unknown",
                method=ev.get("method"),
                path=ev.get("path"),
                status_code=ev.get("status_code"),
                message=ev.get("message"),
                error_type=ev.get("error_type"),
                exception_class=ev.get("exception_class"),
                remote_addr=ev.get("remote_addr"),
                user_agent=ev.get("user_agent"),
                headers_json=json.dumps(ev.get("headers") or {}, ensure_ascii=False),
                body_json=json.dumps(ev.get("body") or {}, ensure_ascii=False),
                traceback_text=ev.get("traceback"),
                suggestion_json=json.dumps(ev.get("suggestion") or {}, ensure_ascii=False),
            )
            db.session.add(rec)  # type: ignore
            db.session.commit()  # type: ignore
        except Exception:
            try:
                db.session.rollback()  # type: ignore
            except Exception:
                pass

    @app.before_request
    def _dbg_before_request():
        g.request_id = request.headers.get("X-Request-Id") or _rid()
        g._t0 = time.time()

    @app.after_request
    def _dbg_after_request(resp):
        # תיעוד בסיסי של כל בקשה (אפשר לכבות עם DEBUG_PACK_HTTP_LOG=0)
        if (os.getenv("DEBUG_PACK_HTTP_LOG", "1") or "1") != "0":
            try:
                ev = {
                    "request_id": getattr(g, "request_id", _rid()),
                    "kind": "http",
                    "method": request.method,
                    "path": request.path,
                    "status_code": resp.status_code,
                    "message": None,
                    "error_type": None,
                    "exception_class": None,
                    "remote_addr": request.headers.get("CF-Connecting-IP") or request.remote_addr,
                    "user_agent": request.headers.get("User-Agent"),
                    "headers": _sanitize_headers(dict(request.headers)),
                    "body": _sanitize_json(_get_request_payload_safely()),
                    "traceback": None,
                    "suggestion": None,
                }
                _store_event(ev)
            except Exception:
                pass

        # להחזיר request-id ללקוח בכל תגובה
        try:
            resp.headers["X-Request-Id"] = getattr(g, "request_id", _rid())
        except Exception:
            pass
        return resp

    def _get_request_payload_safely() -> Any:
        # לא לקרוא stream פעמיים אם כבר נקרא
        if request.method in ("POST", "PUT", "PATCH"):
            # JSON
            if request.is_json:
                try:
                    return request.get_json(silent=True)
                except Exception:
                    return "<invalid json>"
            # form
            try:
                if request.form:
                    return dict(request.form)
            except Exception:
                pass
        return None

    def _json_error_response(status: int, payload: Dict[str, Any]):
        payload = dict(payload)
        payload.setdefault("request_id", getattr(g, "request_id", _rid()))
        return jsonify(payload), status

    # --- Error Handlers (JSON + storage + suggestion) ---

    @app.errorhandler(CSRFError)
    def _handle_csrf(e: CSRFError):
        ev = {
            "request_id": getattr(g, "request_id", _rid()),
            "kind": "exception",
            "method": request.method,
            "path": request.path,
            "status_code": 403,
            "message": _safe_str(getattr(e, "description", "CSRF failure")),
            "error_type": "CSRFError",
            "exception_class": e.__class__.__name__,
            "remote_addr": request.headers.get("CF-Connecting-IP") or request.remote_addr,
            "user_agent": request.headers.get("User-Agent"),
            "headers": _sanitize_headers(dict(request.headers)),
            "body": _sanitize_json(_get_request_payload_safely()),
            "traceback": None,
        }
        ev["suggestion"] = _suggest_fix(ev)
        _store_event(ev)
        return _json_error_response(403, {
            "ok": False,
            "error": "csrf_failed",
            "message": ev["message"],
            "debug": {
                "path": request.path,
                "method": request.method,
                "probable_cause": ev["suggestion"]["probable_cause"],
                "how_to_fix": ev["suggestion"]["how_to_fix"],
            }
        })

    @app.errorhandler(TooManyRequests)
    def _handle_429(e: TooManyRequests):
        ev = {
            "request_id": getattr(g, "request_id", _rid()),
            "kind": "exception",
            "method": request.method,
            "path": request.path,
            "status_code": 429,
            "message": _safe_str(getattr(e, "description", "Too many requests")),
            "error_type": "RateLimit",
            "exception_class": e.__class__.__name__,
            "remote_addr": request.headers.get("CF-Connecting-IP") or request.remote_addr,
            "user_agent": request.headers.get("User-Agent"),
            "headers": _sanitize_headers(dict(request.headers)),
            "body": _sanitize_json(_get_request_payload_safely()),
            "traceback": None,
        }
        ev["suggestion"] = _suggest_fix(ev)
        _store_event(ev)
        return _json_error_response(429, {
            "ok": False,
            "error": "rate_limited",
            "message": ev["message"],
            "debug": {
                "probable_cause": ev["suggestion"]["probable_cause"],
                "how_to_fix": ev["suggestion"]["how_to_fix"],
            }
        })

    @app.errorhandler(BadRequest)
    def _handle_400(e: BadRequest):
        ev = {
            "request_id": getattr(g, "request_id", _rid()),
            "kind": "exception",
            "method": request.method,
            "path": request.path,
            "status_code": 400,
            "message": _safe_str(getattr(e, "description", "Bad Request")),
            "error_type": "BadRequest",
            "exception_class": e.__class__.__name__,
            "remote_addr": request.headers.get("CF-Connecting-IP") or request.remote_addr,
            "user_agent": request.headers.get("User-Agent"),
            "headers": _sanitize_headers(dict(request.headers)),
            "body": _sanitize_json(_get_request_payload_safely()),
            "traceback": None,
        }
        ev["suggestion"] = _suggest_fix(ev)
        _store_event(ev)
        return _json_error_response(400, {
            "ok": False,
            "error": "bad_request",
            "message": ev["message"],
            "debug": {
                "probable_cause": ev["suggestion"]["probable_cause"],
                "how_to_fix": ev["suggestion"]["how_to_fix"],
            }
        })

    @app.errorhandler(Exception)
    def _handle_any_exception(e: Exception):
        # אם זו שגיאת HTTP רגילה (404 וכו') תן ל-Flask לטפל (אבל עדיין אפשר ללוגג אם רוצים)
        if isinstance(e, HTTPException):
            return e

        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        tb = _safe_str(tb, 12000)

        ev = {
            "request_id": getattr(g, "request_id", _rid()),
            "kind": "exception",
            "method": request.method,
            "path": request.path,
            "status_code": 500,
            "message": _safe_str(e, 2000),
            "error_type": "Exception",
            "exception_class": e.__class__.__name__,
            "remote_addr": request.headers.get("CF-Connecting-IP") or request.remote_addr,
            "user_agent": request.headers.get("User-Agent"),
            "headers": _sanitize_headers(dict(request.headers)),
            "body": _sanitize_json(_get_request_payload_safely()),
            "traceback": tb,
        }
        ev["suggestion"] = _suggest_fix(ev)
        _store_event(ev)

        return _json_error_response(500, {
            "ok": False,
            "error": "server_error",
            "message": "Unhandled server exception",
            "debug": {
                "request_id": ev["request_id"],
                "exception_class": ev["exception_class"],
                "error_message": ev["message"],
                "probable_cause": ev["suggestion"]["probable_cause"],
                "how_to_fix": ev["suggestion"]["how_to_fix"],
            }
        })

    # --- Debug endpoints (Owner only) ---

    @debug_bp.get("/owner/debug/events")
    def owner_debug_events():
        if not _owner_ok():
            raise Forbidden("Owner debug access denied. Provide X-Owner-Key or set OWNER_EMAILS.")

        try:
            limit = int(request.args.get("limit", "50"))
        except Exception:
            limit = 50
        limit = max(1, min(limit, 500))

        rows = (DebugEvent.query.order_by(DebugEvent.id.desc()).limit(limit).all())  # type: ignore
        out = []
        for r in rows:
            out.append({
                "id": r.id,
                "ts_utc": r.ts_utc,
                "request_id": r.request_id,
                "kind": r.kind,
                "method": r.method,
                "path": r.path,
                "status_code": r.status_code,
                "message": r.message,
                "error_type": r.error_type,
                "exception_class": r.exception_class,
                "remote_addr": r.remote_addr,
                "user_agent": r.user_agent,
                "headers": json.loads(r.headers_json) if r.headers_json else None,
                "body": json.loads(r.body_json) if r.body_json else None,
                "traceback": r.traceback_text,
                "suggestion": json.loads(r.suggestion_json) if r.suggestion_json else None,
            })
        return jsonify({"ok": True, "events": out})

    @debug_bp.post("/api/client-error")
    def api_client_error():
        # לא דורש owner — כי זה בא מהמשתמש עצמו, אבל אנחנו מסננים מידע
        data = request.get_json(silent=True) or {}
        ev = {
            "request_id": getattr(g, "request_id", _rid()),
            "kind": "client_error",
            "method": "CLIENT",
            "path": request.headers.get("X-Client-Path") or request.referrer or "",
            "status_code": None,
            "message": _safe_str(data.get("message") or "client error"),
            "error_type": _safe_str(data.get("type") or "ClientError", 120),
            "exception_class": _safe_str(data.get("name") or "", 200),
            "remote_addr": request.headers.get("CF-Connecting-IP") or request.remote_addr,
            "user_agent": request.headers.get("User-Agent"),
            "headers": _sanitize_headers(dict(request.headers)),
            "body": _sanitize_json(data),
            "traceback": _safe_str(data.get("stack") or "", 12000) if data.get("stack") else None,
        }
        ev["suggestion"] = _suggest_fix(ev)
        _store_event(ev)
        return jsonify({"ok": True})

    @debug_bp.get("/api/debug/ping")
    def debug_ping():
        return jsonify({"ok": True, "request_id": getattr(g, "request_id", _rid())})

    app.register_blueprint(debug_bp)

    # ensure table exists
    try:
        with app.app_context():
            db.create_all()
    except Exception:
        pass
