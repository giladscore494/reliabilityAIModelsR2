from datetime import datetime
from sqlalchemy import desc, event
from sqlalchemy.orm import relationship, validates
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator, Text
from flask_login import UserMixin
import json

from app.extensions import db


class JSONEncodedText(TypeDecorator):
    """
    A type that stores JSON as Text but validates it on assignment.
    Use JSONB on PostgreSQL, fallback to Text on other databases.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if isinstance(value, str):
                # Validate it's valid JSON
                json.loads(value)
                return value
            return json.dumps(value, ensure_ascii=False)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return value  # Return as string, let caller parse if needed
        return value

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(JSONB())
        else:
            return dialect.type_descriptor(Text())


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(200), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(100))
    service_price_checks_count = db.Column(db.Integer, nullable=False, default=0)

    searches = relationship(
        "SearchHistory",
        cascade="all, delete-orphan",
        backref="user",
        lazy=True,
    )
    advisor_searches = relationship(
        "AdvisorHistory",
        cascade="all, delete-orphan",
        backref="user",
        lazy=True,
    )
    daily_quota_usages = relationship(
        "DailyQuotaUsage",
        cascade="all, delete-orphan",
        backref="user",
        lazy=True,
    )
    quota_reservations = relationship(
        "QuotaReservation",
        cascade="all, delete-orphan",
        backref="user",
        lazy=True,
    )
    legal_acceptances = relationship(
        "LegalAcceptance",
        cascade="all, delete-orphan",
        backref="user",
        lazy=True,
    )
    comparison_histories = relationship(
        "ComparisonHistory",
        cascade="all, delete-orphan",
        backref="user",
        lazy=True,
    )
    service_invoices = relationship(
        "ServiceInvoice",
        cascade="all, delete-orphan",
        backref="user",
        lazy=True,
    )
    feature_acceptances = relationship(
        "LegalFeatureAcceptance",
        cascade="all, delete-orphan",
        backref="user",
        lazy=True,
    )


class DailyQuotaUsage(db.Model):
    """
    Tracks per-user daily quota usage with atomic increments.
    """

    __tablename__ = "daily_quota_usage"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    day = db.Column(db.Date, nullable=False, index=True)
    count = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "day", name="uq_user_day_quota_usage"),
        db.Index("ix_quota_day_user", "day", "user_id"),
    )

    def __repr__(self):
        return f"<DailyQuotaUsage user_id={self.user_id} day={self.day} count={self.count}>"


class QuotaReservation(db.Model):
    """
    Reservation records to ensure fair quota consumption (reserve -> finalize/refund).
    """

    __tablename__ = "quota_reservation"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    day = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, index=True)  # reserved | consumed | released
    request_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_reservation_user_day_status", "user_id", "day", "status"),
    )

    def __repr__(self):
        return f"<QuotaReservation user_id={self.user_id} day={self.day} status={self.status}>"


class SearchHistory(db.Model):
    __table_args__ = (
        db.Index("ix_search_history_user_cache_ts", "user_id", "cache_key", desc("timestamp")),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    cache_key = db.Column(db.String(64), nullable=True)
    make = db.Column(db.String(100))
    model = db.Column(db.String(100))
    year = db.Column(db.Integer)
    mileage_range = db.Column(db.String(100))
    fuel_type = db.Column(db.String(100))
    transmission = db.Column(db.String(100))
    result_json = db.Column(db.Text, nullable=False)
    duration_ms = db.Column(db.Integer, nullable=True)


class AdvisorHistory(db.Model):
    """
    היסטוריית מנוע ההמלצות:
    - profile_json: כל הפרופיל של המשתמש (שאלון מלא)
    - result_json: כל ההמלצות + כל הפרמטרים וההסברים לכל רכב
    """

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    profile_json = db.Column(db.Text, nullable=False)
    result_json = db.Column(db.Text, nullable=False)
    duration_ms = db.Column(db.Integer, nullable=True)


class IpRateLimit(db.Model):
    """
    Per-IP short-window rate limiting (minute buckets).
    """

    __tablename__ = "ip_rate_limit"

    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(64), nullable=False, index=True)
    window_start = db.Column(db.DateTime, nullable=False, index=True)
    count = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("ip", "window_start", name="uq_ip_window"),
        db.Index("ix_ip_window", "ip", "window_start"),
    )


class LegalAcceptance(db.Model):
    __tablename__ = "legal_acceptance"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    terms_version = db.Column(db.String(32), nullable=False)
    privacy_version = db.Column(db.String(32), nullable=False)
    accepted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    accepted_ip = db.Column(db.String(64), nullable=False)
    accepted_user_agent = db.Column(db.String(512), nullable=True)
    source = db.Column(db.String(32), nullable=False, default="web")

    __table_args__ = (
        db.UniqueConstraint("user_id", "terms_version", "privacy_version", name="uq_legal_acceptance_user_version"),
        db.Index("ix_legal_acceptance_user_version", "user_id", "terms_version", "privacy_version"),
    )


class ComparisonHistory(db.Model):
    """
    Stores car comparison results for the Car Comparison feature.
    Supports comparisons of up to 3 cars with full source transparency.
    Uses JSONB on PostgreSQL, Text with JSON validation on other databases.
    """
    __tablename__ = "comparison_history"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=True)
    session_id = db.Column(db.String(64), nullable=True, index=True)
    
    # JSON columns - use JSONB on Postgres, validated Text elsewhere
    cars_selected = db.Column(JSONEncodedText, nullable=False)  # JSON array of selected cars
    model_json_raw = db.Column(JSONEncodedText, nullable=True)  # Raw model output JSON
    computed_result = db.Column(JSONEncodedText, nullable=True)  # Computed scores and winners JSON
    sources_index = db.Column(JSONEncodedText, nullable=True)  # Sources index JSON
    
    # Metadata columns
    model_name = db.Column(db.String(64), nullable=False, default="gemini-3-flash")
    grounding_enabled = db.Column(db.Boolean, nullable=False, default=True)
    prompt_version = db.Column(db.String(32), nullable=False, default="v1")
    request_hash = db.Column(db.String(64), nullable=True)  # Index defined in __table_args__
    duration_ms = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        db.Index("ix_comparison_history_user_created", "user_id", desc("created_at")),
        db.Index("ix_comparison_history_request_hash", "request_hash"),
    )

    @validates('cars_selected', 'model_json_raw', 'computed_result', 'sources_index')
    def validate_json_fields(self, key, value):
        """Validate that JSON fields contain valid JSON."""
        if value is None:
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, str):
            # Validate it's parseable JSON
            try:
                json.loads(value)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in {key}: {e}")
            return value
        raise ValueError(f"Invalid type for {key}: expected dict, list, or JSON string")


class ServiceInvoice(db.Model):
    """
    Stores service invoice data (redacted/structured only, no raw images).
    Used for Service Price Check feature.
    """
    __tablename__ = "service_invoice"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)

    make = db.Column(db.String(100), nullable=True, index=True)
    model = db.Column(db.String(100), nullable=True, index=True)
    year = db.Column(db.Integer, nullable=True, index=True)
    mileage = db.Column(db.Integer, nullable=True, index=True)
    region = db.Column(db.String(64), nullable=True, index=True)  # coarse region only
    garage_type = db.Column(db.String(16), nullable=True, index=True)  # dealer/private/unknown
    invoice_date = db.Column(db.Date, nullable=True, index=True)

    total_price_ils = db.Column(db.Integer, nullable=True)
    currency = db.Column(db.String(8), nullable=False, default="ILS")

    parsed_json = db.Column(JSONEncodedText, nullable=False)  # sanitized + redacted OCR structure
    report_json = db.Column(JSONEncodedText, nullable=False)  # final computed report for download
    duration_ms = db.Column(db.Integer, nullable=True)
    request_id = db.Column(db.String(64), nullable=True)

    items = relationship(
        "ServiceInvoiceItem",
        cascade="all, delete-orphan",
        backref="invoice",
        lazy=True,
    )

    __table_args__ = (
        db.Index("ix_service_invoice_cohort", "make", "model", "year", "region", "garage_type"),
    )


class ServiceInvoiceItem(db.Model):
    """
    Canonicalized line items from a service invoice.
    """
    __tablename__ = "service_invoice_item"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("service_invoice.id", ondelete="CASCADE"), nullable=False, index=True)

    canonical_code = db.Column(db.String(64), nullable=False, index=True)  # e.g. oil_change, brake_pads_front
    category = db.Column(db.String(32), nullable=True, index=True)  # brakes/engine/etc
    raw_description = db.Column(db.String(512), nullable=True)

    price_ils = db.Column(db.Integer, nullable=True, index=True)
    labor_ils = db.Column(db.Integer, nullable=True)
    parts_ils = db.Column(db.Integer, nullable=True)
    qty = db.Column(db.Integer, nullable=True)
    confidence = db.Column(db.Float, nullable=True)

    __table_args__ = (
        db.Index("ix_service_invoice_item_code_price", "canonical_code", "price_ils"),
    )


class ServicePriceBenchmarkItem(db.Model):
    """
    Unique anonymized dataset derived from user-consented invoice extractions.
    Stores ONLY minimal fields: no PII, no raw images, no report_json, no web samples.
    Used to improve pricing benchmarks over time.
    """
    __tablename__ = "service_price_benchmark_item"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    canonical_code = db.Column(db.String(64), nullable=False, index=True)
    category = db.Column(db.String(32), nullable=True, index=True)
    price_ils = db.Column(db.Integer, nullable=True, index=True)
    parts_ils = db.Column(db.Integer, nullable=True)
    labor_ils = db.Column(db.Integer, nullable=True)
    qty = db.Column(db.Integer, nullable=True)

    make = db.Column(db.String(100), nullable=True, index=True)
    model = db.Column(db.String(100), nullable=True, index=True)
    year_bucket = db.Column(db.String(16), nullable=True, index=True)  # e.g. "2020-2024"
    mileage_bucket = db.Column(db.String(16), nullable=True, index=True)  # e.g. "50000-100000"
    region = db.Column(db.String(64), nullable=True, index=True)
    garage_type = db.Column(db.String(16), nullable=True, index=True)
    invoice_month = db.Column(db.String(7), nullable=True, index=True)  # e.g. "2026-01"

    __table_args__ = (
        db.Index("ix_benchmark_code_make_model", "canonical_code", "make", "model"),
    )


class LegalFeatureAcceptance(db.Model):
    """
    Feature-specific legal acceptance records.
    Scalable approach: one table for all feature consents (e.g., invoice_scanner).
    """
    __tablename__ = "legal_feature_acceptance"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    feature_key = db.Column(db.String(64), nullable=False, index=True)  # e.g. "invoice_scanner"
    version = db.Column(db.String(32), nullable=False)  # e.g. "2026-02-07"
    accepted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "feature_key", "version", name="uq_feature_acceptance"),
    )
