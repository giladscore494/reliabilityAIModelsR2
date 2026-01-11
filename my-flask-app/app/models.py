from datetime import datetime
from sqlalchemy import desc
from sqlalchemy.orm import relationship
from flask_login import UserMixin

from app.extensions import db


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
