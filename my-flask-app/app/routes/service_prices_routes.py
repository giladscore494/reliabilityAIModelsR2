# -*- coding: utf-8 -*-
"""Service Prices routes blueprint."""

import json
import time as pytime
from datetime import datetime
from io import BytesIO

from flask import Blueprint, request, jsonify, current_app, render_template, send_file
from flask_login import login_required, current_user
from json_repair import repair_json
from werkzeug.exceptions import RequestEntityTooLarge

from app.extensions import db
from app.models import ServiceInvoice, LegalAcceptance
from app.legal import (
    TERMS_VERSION, PRIVACY_VERSION,
    INVOICE_FEATURE_KEY, INVOICE_FEATURE_CONSENT_VERSION,
    INVOICE_EXT_PROCESSING_KEY, INVOICE_ANON_STORAGE_KEY,
    INVOICE_EXT_PROCESSING_VERSION, INVOICE_ANON_STORAGE_VERSION,
    has_accepted_feature, parse_legal_confirm,
)
from app.quota import (
    check_and_increment_ip_rate_limit,
    get_client_ip,
    log_access_decision,
    compute_quota_window,
    reserve_daily_quota,
    finalize_quota_reservation,
    release_quota_reservation,
    PER_IP_PER_MIN_LIMIT,
)
from app.utils.http_helpers import api_error, api_ok, get_request_id
from app.services import service_prices_service

bp = Blueprint("service_prices", __name__)

# Allowed MIME types for invoice images
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/jpg"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
MAX_JSON_DECODE_ATTEMPTS = 3  # Handles double/triple-encoded report_json payloads.


def _legal_gating_error(code: str, message: str, required: dict, status: int = 428):
    """Return a JSON error for legal gating failures."""
    rid = get_request_id()
    resp = jsonify({
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
        "required": required,
        "request_id": rid,
    })
    resp.status_code = status
    resp.headers["X-Request-ID"] = rid
    return resp


def check_legal_acceptance(user_id: int):
    """
    Check if user has accepted required Terms and Privacy versions.
    Returns None if accepted, or error response if not.
    """
    terms_version = current_app.config.get("TERMS_VERSION", TERMS_VERSION)
    privacy_version = current_app.config.get("PRIVACY_VERSION", PRIVACY_VERSION)
    
    existing = LegalAcceptance.query.filter_by(
        user_id=user_id,
        terms_version=terms_version,
        privacy_version=privacy_version,
    ).first()
    
    if not existing:
        return _legal_gating_error(
            "LEGAL_ACCEPTANCE_REQUIRED",
            "חובה לאשר תנאי שימוש ומדיניות פרטיות לפני שימוש בפיצ'ר",
            {"terms_version": terms_version, "privacy_version": privacy_version},
        )
    
    return None


def check_feature_consent(user_id: int):
    """
    Check if user has accepted BOTH invoice scanner feature consents:
    1. External processing consent
    2. Anonymized storage consent
    Returns None if both accepted, or error response if either is missing.
    """
    # Check external processing consent
    if not has_accepted_feature(user_id, INVOICE_EXT_PROCESSING_KEY, INVOICE_EXT_PROCESSING_VERSION):
        return _legal_gating_error(
            "FEATURE_CONSENT_REQUIRED",
            "חובה לאשר אישור ייעודי לעיבוד חשבונית לפני שימוש בפיצ'ר",
            {"feature_key": INVOICE_EXT_PROCESSING_KEY, "version": INVOICE_EXT_PROCESSING_VERSION},
        )

    if not has_accepted_feature(user_id, INVOICE_ANON_STORAGE_KEY, INVOICE_ANON_STORAGE_VERSION):
        return _legal_gating_error(
            "FEATURE_CONSENT_REQUIRED",
            "חובה לאשר אישור לשמירת נתונים אנונימיים לפני שימוש בפיצ'ר",
            {"feature_key": INVOICE_ANON_STORAGE_KEY, "version": INVOICE_ANON_STORAGE_VERSION},
        )

    return None


def _safe_parse_report_json(raw_report):
    """Parse report JSON safely, attempting repair when needed."""
    was_repaired = False
    if isinstance(raw_report, memoryview):
        raw_report = raw_report.tobytes()
    if isinstance(raw_report, (bytes, bytearray)):
        raw_report = raw_report.decode("utf-8", errors="replace")
    if isinstance(raw_report, (dict, list)):
        return raw_report, was_repaired
    if raw_report is None:
        return None, was_repaired
    if not isinstance(raw_report, str):
        return None, was_repaired
    payload = raw_report
    for _ in range(MAX_JSON_DECODE_ATTEMPTS):
        # Each iteration peels one JSON encoding layer.
        if isinstance(payload, (dict, list)):
            return payload, was_repaired
        if not isinstance(payload, str):
            break
        try:
            payload = json.loads(payload)
            if isinstance(payload, (dict, list)):
                return payload, was_repaired
            continue
        except json.JSONDecodeError:
            try:
                repaired = repair_json(payload)
                was_repaired = True
                payload = json.loads(repaired)
                if isinstance(payload, (dict, list)):
                    return payload, was_repaired
                continue
            except Exception:
                break
    if isinstance(payload, (dict, list)):
        return payload, was_repaired
    return None, was_repaired


@bp.route("/service-prices", methods=["GET"])
@login_required
def service_prices_page():
    """Render the Service Price Check page."""
    log_access_decision("/service-prices", current_user.id, "allowed", "authenticated user")
    
    # Check legal acceptance status for frontend
    terms_version = current_app.config.get("TERMS_VERSION", TERMS_VERSION)
    privacy_version = current_app.config.get("PRIVACY_VERSION", PRIVACY_VERSION)
    
    legal_accepted = LegalAcceptance.query.filter_by(
        user_id=current_user.id,
        terms_version=terms_version,
        privacy_version=privacy_version,
    ).first() is not None
    
    ext_processing_accepted = has_accepted_feature(
        current_user.id,
        INVOICE_EXT_PROCESSING_KEY,
        INVOICE_EXT_PROCESSING_VERSION,
    )
    anon_storage_accepted = has_accepted_feature(
        current_user.id,
        INVOICE_ANON_STORAGE_KEY,
        INVOICE_ANON_STORAGE_VERSION,
    )

    return render_template(
        "service_prices.html",
        user=current_user,
        is_logged_in=True,
        legal_accepted=legal_accepted,
        feature_accepted=ext_processing_accepted and anon_storage_accepted,
        ext_processing_accepted=ext_processing_accepted,
        anon_storage_accepted=anon_storage_accepted,
        terms_version=terms_version,
        privacy_version=privacy_version,
        feature_key=INVOICE_FEATURE_KEY,
        feature_version=INVOICE_FEATURE_CONSENT_VERSION,
        ext_processing_key=INVOICE_EXT_PROCESSING_KEY,
        ext_processing_version=INVOICE_EXT_PROCESSING_VERSION,
        anon_storage_key=INVOICE_ANON_STORAGE_KEY,
        anon_storage_version=INVOICE_ANON_STORAGE_VERSION,
        max_file_mb=MAX_FILE_SIZE // (1024 * 1024),
        request_limit_mb=round(current_app.config.get("SERVICE_PRICES_ANALYZE_LIMIT_BYTES", 6 * 1024 * 1024) / (1024 * 1024)),
    )


@bp.route("/api/service-prices/analyze", methods=["POST"])
@login_required
def analyze_invoice():
    """
    Analyze an uploaded invoice image.
    Requires legal acceptance AND feature consent BEFORE any AI call.
    """
    start_time_ms = int(pytime.time() * 1000)
    user_id = current_user.id
    request_id = get_request_id()
    
    log_access_decision("/api/service-prices/analyze", user_id, "processing", "authenticated user")
    
    # IP rate limiting
    client_ip = get_client_ip()
    per_ip_limit = current_app.config.get("PER_IP_PER_MIN_LIMIT", PER_IP_PER_MIN_LIMIT)
    ip_allowed, ip_count, ip_resets_at = check_and_increment_ip_rate_limit(client_ip, limit=per_ip_limit)
    
    if not ip_allowed:
        retry_after = max(0, int((ip_resets_at - datetime.utcnow()).total_seconds()))
        resp = api_error(
            "rate_limited",
            "חרגת ממגבלת הבקשות לדקה.",
            status=429,
            details={
                "limit": per_ip_limit,
                "used": ip_count,
                "remaining": max(0, per_ip_limit - ip_count),
                "resets_at": ip_resets_at.isoformat(),
            },
        )
        resp.headers["Retry-After"] = str(retry_after)
        return resp
    
    # ============================================
    # LEGAL GATE #1: Terms & Privacy Acceptance
    # MUST execute BEFORE any file reading or AI call
    # ============================================
    legal_error = check_legal_acceptance(user_id)
    if legal_error:
        log_access_decision("/api/service-prices/analyze", user_id, "rejected", "legal acceptance required")
        return legal_error
    
    # ============================================
    # LEGAL GATE #2: Feature Consent
    # MUST execute BEFORE any file reading or AI call
    # ============================================
    consent_error = check_feature_consent(user_id)
    if consent_error:
        log_access_decision("/api/service-prices/analyze", user_id, "rejected", "feature consent required")
        return consent_error
    
    # Determine if anonymized storage consent was given
    anon_storage_consented = has_accepted_feature(
        user_id, INVOICE_ANON_STORAGE_KEY, INVOICE_ANON_STORAGE_VERSION
    )
    
    # ============================================
    # Now we can proceed with file handling
    # ============================================
    
    # Check if file is present
    if "invoice_image" not in request.files:
        return api_error("missing_file", "נדרש להעלות קובץ חשבונית", status=400)
    
    file = request.files["invoice_image"]
    
    if not file or not file.filename:
        return api_error("missing_file", "נדרש להעלות קובץ חשבונית", status=400)
    
    # Validate MIME type
    mime_type = file.content_type or file.mimetype
    if mime_type not in ALLOWED_MIME_TYPES:
        return api_error(
            "invalid_file_type",
            "סוג קובץ לא נתמך. יש להעלות JPG או PNG בלבד.",
            status=400,
            details={"allowed": list(ALLOWED_MIME_TYPES), "received": mime_type},
        )
    
    # Read file bytes (don't save to disk)
    try:
        image_bytes = file.read()
        if len(image_bytes) > MAX_FILE_SIZE:
            return api_error(
                "file_too_large",
                f"הקובץ גדול מדי. מקסימום {MAX_FILE_SIZE // (1024*1024)}MB.",
                status=413,
            )
    except Exception as e:
        current_app.logger.error(f"Failed to read uploaded file: {e}")
        return api_error("file_read_error", "שגיאה בקריאת הקובץ", status=500)
    
    # Get optional overrides from form data
    overrides = {}
    for field in ["make", "model", "year", "mileage", "region", "garage_type"]:
        value = request.form.get(field)
        if value:
            if field in ("year", "mileage"):
                try:
                    overrides[field] = int(value)
                except ValueError:
                    pass
            else:
                overrides[field] = value
    
    # Process invoice
    try:
        report, invoice_id = service_prices_service.handle_invoice_analysis(
            user_id=user_id,
            image_bytes=image_bytes,
            mime_type=mime_type,
            request_id=request_id,
            overrides=overrides if overrides else None,
            anon_storage_consented=anon_storage_consented,
        )
        
        log_access_decision("/api/service-prices/analyze", user_id, "success", f"invoice_id={invoice_id}")
        
        return api_ok({
            "invoice_id": invoice_id,
            "report": report,
        })
        
    except Exception as e:
        current_app.logger.exception(f"Invoice analysis failed: {e}")
        return api_error(
            "analysis_failed",
            "שגיאה בניתוח החשבונית. אנא נסה שוב.",
            status=500,
        )


@bp.route("/api/service-prices/download/<int:invoice_id>", methods=["GET"])
@login_required
def download_report(invoice_id: int):
    """Download a report as JSON file."""
    try:
        user_id = current_user.id

        invoice = ServiceInvoice.query.filter_by(
            id=invoice_id,
            user_id=user_id,
        ).first()

        if not invoice:
            return api_error("not_found", "דוח לא נמצא", status=404)

        report, _ = _safe_parse_report_json(invoice.report_json)
        if report is None:
            raw_text = invoice.report_json if isinstance(invoice.report_json, str) else json.dumps(invoice.report_json)
            buffer = BytesIO(raw_text.encode("utf-8", errors="replace"))
            buffer.seek(0)
            return send_file(
                buffer,
                mimetype="text/plain",
                as_attachment=True,
                download_name=f"service_price_report_{invoice_id}.txt",
            )

        report_bytes = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
        buffer = BytesIO(report_bytes)
        buffer.seek(0)

        return send_file(
            buffer,
            mimetype="application/json",
            as_attachment=True,
            download_name=f"service_price_report_{invoice_id}.json",
        )
    except Exception:
        current_app.logger.exception("Failed to download service price report.")
        return api_error("download_failed", "שגיאה בהורדת הדוח", status=500)


@bp.route("/service-prices/report/<int:invoice_id>", methods=["GET"])
@login_required
def service_prices_report(invoice_id: int):
    """Render a printable HTML report."""
    user_id = current_user.id

    invoice = ServiceInvoice.query.filter_by(
        id=invoice_id,
        user_id=user_id,
    ).first()

    if not invoice:
        return api_error("not_found", "דוח לא נמצא", status=404)

    report, _ = _safe_parse_report_json(invoice.report_json)
    if report is None:
        return render_template(
            "service_prices_report.html",
            invoices=[],
            error_message="שגיאה בפענוח נתוני הדוח - פורמט JSON לא תקין",
        )

    return render_template(
        "service_prices_report.html",
        invoices=[{"invoice": invoice, "report": report}],
    )


@bp.route("/service-prices/report/all", methods=["GET"])
@login_required
def service_prices_report_all():
    """Render all user's printable HTML reports."""
    user_id = current_user.id

    invoices = ServiceInvoice.query.filter_by(
        user_id=user_id,
    ).order_by(ServiceInvoice.created_at.desc()).all()

    report_entries = []
    for invoice in invoices:
        report, _ = _safe_parse_report_json(invoice.report_json)
        report_entries.append({
            "invoice": invoice,
            "report": report or {},
            "error": report is None,
        })

    return render_template(
        "service_prices_report.html",
        invoices=report_entries,
    )


@bp.route("/api/service-prices/history", methods=["GET"])
@login_required
def list_invoices():
    """List user's invoice history."""
    user_id = current_user.id
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    
    pagination = ServiceInvoice.query.filter_by(
        user_id=user_id,
    ).order_by(
        ServiceInvoice.created_at.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)
    
    invoices = []
    for inv in pagination.items:
        duration_sec = round(inv.duration_ms / 1000, 1) if inv.duration_ms else None
        invoices.append({
            "id": inv.id,
            "created_at": inv.created_at.isoformat(),
            "make": inv.make,
            "model": inv.model,
            "year": inv.year,
            "total_price_ils": inv.total_price_ils,
            "garage_type": inv.garage_type,
            "duration_ms": inv.duration_ms,
            "duration_sec": duration_sec,
        })
    
    return api_ok({
        "invoices": invoices,
        "total": pagination.total,
        "page": pagination.page,
        "pages": pagination.pages,
    })


@bp.route("/api/service-prices/export", methods=["GET"])
@login_required
def export_all_reports():
    """Export all user's reports as JSONL."""
    user_id = current_user.id
    
    invoices = ServiceInvoice.query.filter_by(
        user_id=user_id,
    ).order_by(
        ServiceInvoice.created_at.desc()
    ).all()
    
    # Build JSONL content
    lines = []
    for inv in invoices:
        try:
            report, _ = _safe_parse_report_json(inv.report_json)
            if report is None:
                continue
            lines.append(json.dumps({
                "id": inv.id,
                "created_at": inv.created_at.isoformat(),
                "report": report,
            }, ensure_ascii=False))
        except Exception:
            continue
    
    content = "\n".join(lines).encode("utf-8")
    buffer = BytesIO(content)
    buffer.seek(0)
    
    return send_file(
        buffer,
        mimetype="application/x-ndjson",
        as_attachment=True,
        download_name=f"service_price_reports_export_{datetime.utcnow().strftime('%Y%m%d')}.jsonl",
    )


@bp.route("/api/service-prices/eta", methods=["GET"])
@login_required
def get_eta():
    """Return estimated time for invoice analysis."""
    from sqlalchemy import func
    user_id = current_user.id

    # User's rolling average (last 20)
    user_subq = db.session.query(
        ServiceInvoice.duration_ms
    ).filter(
        ServiceInvoice.user_id == user_id,
        ServiceInvoice.duration_ms.isnot(None),
    ).order_by(ServiceInvoice.created_at.desc()).limit(20).subquery()

    user_avg = db.session.query(func.avg(user_subq.c.duration_ms)).scalar()

    # Global average (last 200)
    global_subq = db.session.query(
        ServiceInvoice.duration_ms
    ).filter(
        ServiceInvoice.duration_ms.isnot(None),
    ).order_by(ServiceInvoice.created_at.desc()).limit(200).subquery()

    global_avg = db.session.query(func.avg(global_subq.c.duration_ms)).scalar()

    eta_user_s = round(user_avg / 1000, 1) if user_avg else None
    eta_global_s = round(global_avg / 1000, 1) if global_avg else None

    return api_ok({
        "eta_user_s": eta_user_s,
        "eta_global_s": eta_global_s,
    })


@bp.route("/service-prices/history", methods=["GET"])
@login_required
def service_prices_history_page():
    """Service prices history list page."""
    return render_template(
        "service_prices.html",
        user=current_user,
        is_logged_in=True,
        legal_accepted=True,
        feature_accepted=True,
        ext_processing_accepted=True,
        anon_storage_accepted=True,
        terms_version=TERMS_VERSION,
        privacy_version=PRIVACY_VERSION,
        feature_key=INVOICE_FEATURE_KEY,
        feature_version=INVOICE_FEATURE_CONSENT_VERSION,
        ext_processing_key=INVOICE_EXT_PROCESSING_KEY,
        ext_processing_version=INVOICE_EXT_PROCESSING_VERSION,
        anon_storage_key=INVOICE_ANON_STORAGE_KEY,
        anon_storage_version=INVOICE_ANON_STORAGE_VERSION,
        show_history=True,
        max_file_mb=MAX_FILE_SIZE // (1024 * 1024),
        request_limit_mb=round(current_app.config.get("SERVICE_PRICES_ANALYZE_LIMIT_BYTES", 6 * 1024 * 1024) / (1024 * 1024)),
    )


@bp.route("/service-prices/history/<int:invoice_id>", methods=["GET"])
@login_required
def service_prices_history_detail(invoice_id: int):
    """Service prices history detail - return report JSON."""
    user_id = current_user.id

    invoice = ServiceInvoice.query.filter_by(
        id=invoice_id,
        user_id=user_id,
    ).first()

    if not invoice:
        return api_error("not_found", "דוח לא נמצא", status=404)

    try:
        report, _ = _safe_parse_report_json(invoice.report_json)
        if report is None:
            raise ValueError("Invalid report")
    except Exception:
        return api_error("invalid_report", "שגיאה בקריאת הדוח", status=500)

    return api_ok({
        "invoice_id": invoice.id,
        "created_at": invoice.created_at.isoformat(),
        "make": invoice.make,
        "model": invoice.model,
        "year": invoice.year,
        "total_price_ils": invoice.total_price_ils,
        "report": report,
    })
