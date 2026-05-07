from datetime import datetime, timezone
from sqlalchemy import desc
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
    feature_acceptances = relationship(
        "LegalFeatureAcceptance",
        cascade="all, delete-orphan",
        backref="user",
        lazy=True,
    )
    research_consents = relationship(
        "ResearchConsent",
        cascade="all, delete-orphan",
        backref="user",
        lazy=True,
    )
    research_response_sessions = relationship(
        "ResearchResponseSession",
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
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

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
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    __table_args__ = (
        db.Index("ix_reservation_user_day_status", "user_id", "day", "status"),
    )

    def __repr__(self):
        return f"<QuotaReservation user_id={self.user_id} day={self.day} status={self.status}>"


class SearchHistory(db.Model):
    __table_args__ = (
        db.Index("ix_search_history_user_cache_ts", "user_id", "cache_key", desc("timestamp")),
        db.Index(
            "idx_search_history_public_examples",
            "example_slug",
            postgresql_where=db.text("is_public_example = true"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    cache_key = db.Column(db.String(64), nullable=True)
    make = db.Column(db.String(100))
    model = db.Column(db.String(100))
    year = db.Column(db.Integer)
    mileage_range = db.Column(db.String(100))
    fuel_type = db.Column(db.String(100))
    transmission = db.Column(db.String(100))
    result_json = db.Column(JSONEncodedText, nullable=False)
    duration_ms = db.Column(db.Integer, nullable=True)
    is_public_example = db.Column(db.Boolean, nullable=False, default=False, server_default="false")
    example_slug = db.Column(db.String(64), nullable=True, unique=True)


class AdvisorHistory(db.Model):
    """
    היסטוריית מנוע ההמלצות:
    - profile_json: כל הפרופיל של המשתמש (שאלון מלא)
    - result_json: כל ההמלצות + כל הפרמטרים וההסברים לכל רכב
    """

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    profile_json = db.Column(db.Text, nullable=False)
    result_json = db.Column(JSONEncodedText, nullable=False)
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
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

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
    accepted_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
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
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=True)
    session_id = db.Column(db.String(64), nullable=True, index=True)
    
    # JSON columns - use JSONB on Postgres, validated Text elsewhere
    cars_selected = db.Column(JSONEncodedText, nullable=False)  # JSON array of selected cars
    model_json_raw = db.Column(JSONEncodedText, nullable=True)  # Raw model output JSON
    computed_result = db.Column(JSONEncodedText, nullable=True)  # Computed scores and winners JSON
    sources_index = db.Column(JSONEncodedText, nullable=True)  # Sources index JSON
    
    # Metadata columns
    model_name = db.Column(db.String(64), nullable=False, default="gemini-3.1-flash")
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


class LegalFeatureAcceptance(db.Model):
    """
    Feature-specific legal acceptance records.
    Scalable approach: one table for all feature consents.
    """
    __tablename__ = "legal_feature_acceptance"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    feature_key = db.Column(db.String(64), nullable=False, index=True)  # per-feature consent key
    version = db.Column(db.String(32), nullable=False)  # e.g. "2026-02-07"
    accepted_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    __table_args__ = (
        db.UniqueConstraint("user_id", "feature_key", "version", name="uq_feature_acceptance"),
    )


class ResearchConsent(db.Model):
    __tablename__ = "research_consent"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=True, index=True)
    anon_id = db.Column(db.String(64), nullable=True, index=True)
    consent_type = db.Column(db.String(32), nullable=False, default="research_questions", index=True)
    terms_version = db.Column(db.String(32), nullable=False)
    privacy_version = db.Column(db.String(32), nullable=False)
    research_notice_version = db.Column(db.String(32), nullable=False)
    accepted_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    accepted_ip = db.Column(db.String(64), nullable=False)
    accepted_user_agent = db.Column(db.String(512), nullable=True)
    accepted_lang = db.Column(db.String(32), nullable=True)
    accepted_source = db.Column(db.String(64), nullable=False, default="web")
    is_explicit = db.Column(db.Boolean, nullable=False, default=True)
    is_informed = db.Column(db.Boolean, nullable=False, default=True)
    
    # New columns for refactor 2026-04-25
    consent_given = db.Column(db.Boolean, nullable=True, default=True, server_default="true")
    source_page = db.Column(db.String(64), nullable=True)
    ip_hash = db.Column(db.String(128), nullable=True)
    user_agent_hash = db.Column(db.String(128), nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "consent_type",
            "terms_version",
            "privacy_version",
            "research_notice_version",
            name="uq_research_consent_user_version",
        ),
        db.UniqueConstraint(
            "anon_id",
            "consent_type",
            "terms_version",
            "privacy_version",
            "research_notice_version",
            name="uq_research_consent_anon_version",
        ),
        db.Index(
            "ix_research_consent_lookup_user",
            "user_id",
            "consent_type",
            "research_notice_version",
            "accepted_at",
        ),
        db.Index(
            "ix_research_consent_lookup_anon",
            "anon_id",
            "consent_type",
            "research_notice_version",
            "accepted_at",
        ),
    )


class ResearchResponseSession(db.Model):
    __tablename__ = "research_response_session"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=True, index=True)
    anon_id = db.Column(db.String(64), nullable=True, index=True)
    flow_type = db.Column(db.String(32), nullable=False, index=True)
    source_analysis_type = db.Column(db.String(64), nullable=False, index=True)
    source_record_id = db.Column(db.Integer, nullable=True, index=True)
    vehicle_context_json = db.Column(JSONEncodedText, nullable=True)
    consent_id = db.Column(db.Integer, db.ForeignKey("research_consent.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(32), nullable=False, default="submitted", index=True)
    
    # New columns for refactor 2026-04-25
    question_version = db.Column(db.String(32), nullable=True)
    related_search_history_id = db.Column(db.Integer, db.ForeignKey("search_history.id", ondelete="SET NULL"), nullable=True)
    related_advisor_history_id = db.Column(db.Integer, db.ForeignKey("advisor_history.id", ondelete="SET NULL"), nullable=True)
    related_compare_history_id = db.Column(db.Integer, db.ForeignKey("comparison_history.id", ondelete="SET NULL"), nullable=True)

    consent = relationship("ResearchConsent", lazy=True)
    responses = relationship(
        "ResearchResponse",
        cascade="all, delete-orphan",
        backref="session",
        lazy=True,
    )

    __table_args__ = (
        db.Index(
            "ix_research_session_flow_source",
            "flow_type",
            "source_analysis_type",
            "source_record_id",
        ),
        db.Index(
            "ix_research_session_subject_flow_created",
            "user_id",
            "anon_id",
            "flow_type",
            "created_at",
        ),
    )


class ResearchResponse(db.Model):
    __tablename__ = "research_response"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("research_response_session.id", ondelete="CASCADE"), nullable=False, index=True)
    question_code = db.Column(db.String(64), nullable=False, index=True)
    flow_type = db.Column(db.String(32), nullable=False, index=True)
    response_json = db.Column(JSONEncodedText, nullable=False)
    answered_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    is_required = db.Column(db.Boolean, nullable=False, default=False)
    question_version = db.Column(db.String(32), nullable=False)
    consent_id = db.Column(db.Integer, db.ForeignKey("research_consent.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # New column for refactor 2026-04-25
    answer_type = db.Column(db.String(32), nullable=True)

    consent = relationship("ResearchConsent", lazy=True)

    __table_args__ = (
        db.UniqueConstraint("session_id", "question_code", name="uq_research_response_session_question"),
        db.Index(
            "ix_research_response_flow_question_answered",
            "flow_type",
            "question_code",
            "answered_at",
        ),
    )


class Feedback(db.Model):
    """CTA feedback (thumbs up/down) on analyses."""
    __tablename__ = "feedback"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    search_history_id = db.Column(db.Integer, db.ForeignKey("search_history.id", ondelete="SET NULL"), nullable=True)
    is_positive = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        server_default=db.func.now(),
    )

    __table_args__ = (
        db.Index("ix_feedback_user_created", "user_id", "created_at"),
        db.Index("ix_feedback_search_history", "search_history_id"),
        db.UniqueConstraint("user_id", "search_history_id", name="uq_feedback_user_search"),
    )
