# -*- coding: utf-8 -*-
"""Daily quota and reservation helpers."""

import logging
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.config import MAX_ACTIVE_RESERVATIONS, QUOTA_RESERVATION_TTL_SECONDS
from app.extensions import db
from app.models import DailyQuotaUsage, QuotaReservation
from app.quota import QuotaInternalError
from app.utils.http_helpers import _utcnow

logger = logging.getLogger(__name__)


def get_daily_quota_usage(user_id: int, day_key: date) -> int:
    """Return today's usage count without mutating state."""
    quota = DailyQuotaUsage.query.filter_by(user_id=user_id, day=day_key).first()
    return quota.count if quota else 0


def cleanup_expired_reservations(user_id: int, day_key: date, now_utc: Optional[datetime] = None) -> int:
    """
    Remove stale reservations that were never finalized to avoid blocking quota.
    """
    now = now_utc or _utcnow()
    expire_before = now - timedelta(seconds=QUOTA_RESERVATION_TTL_SECONDS)
    deleted = (
        db.session.query(QuotaReservation)
        .filter(
            QuotaReservation.user_id == user_id,
            QuotaReservation.day == day_key,
            QuotaReservation.status == "reserved",
            QuotaReservation.created_at < expire_before,
        )
        .delete(synchronize_session=False)
    )
    # Optional cleanup of already released/consumed rows older than TTL to control growth
    db.session.query(QuotaReservation).filter(
        QuotaReservation.user_id == user_id,
        QuotaReservation.day < (day_key - timedelta(days=7))
    ).delete(synchronize_session=False)
    return deleted


def _get_or_create_quota_row(user_id: int, day_key: date, now_utc: datetime) -> DailyQuotaUsage:
    bind = db.session.get_bind()
    dialect_name = bind.dialect.name if bind else ""
    base_values = {"user_id": user_id, "day": day_key, "count": 0, "updated_at": now_utc}

    try:
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            stmt = pg_insert(DailyQuotaUsage).values(**base_values).on_conflict_do_nothing(
                constraint="uq_user_day_quota_usage"
            )
            db.session.execute(stmt)
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            stmt = sqlite_insert(DailyQuotaUsage).values(**base_values).on_conflict_do_nothing(
                index_elements=["user_id", "day"]
            )
            db.session.execute(stmt)
    except IntegrityError:
        db.session.rollback()
    except SQLAlchemyError:
        db.session.rollback()

    try:
        quota = (
            db.session.query(DailyQuotaUsage)
            .filter_by(user_id=user_id, day=day_key)
            .with_for_update()
            .first()
        )
    except SQLAlchemyError:
        db.session.rollback()
        quota = (
            db.session.query(DailyQuotaUsage)
            .filter_by(user_id=user_id, day=day_key)
            .first()
        )
    if quota is None:
        quota = DailyQuotaUsage(user_id=user_id, day=day_key, count=0, updated_at=now_utc)
        db.session.add(quota)
        db.session.flush()
    return quota


def reserve_daily_quota(user_id: int, day_key: date, limit: int, request_id: str, now_utc: Optional[datetime] = None) -> Tuple[bool, int, int, Optional[int]]:
    """
    Reserve a quota slot (reserved -> finalize or release).

    Returns:
        allowed (bool): whether reservation succeeded
        consumed_count (int): already consumed count
        reserved_count (int): active reserved count AFTER this call (if allowed)
        reservation_id (int|None): id of created reservation if allowed
    """
    now = now_utc or _utcnow()
    try:
        try:
            cleanup_expired_reservations(user_id, day_key, now)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logging.getLogger(__name__).warning(
                "[QUOTA] cleanup_expired_reservations failed for user=%s day=%s",
                user_id,
                day_key,
            )
        with db.session.begin_nested():
            quota = _get_or_create_quota_row(user_id, day_key, now)
            consumed_count = quota.count

            active_reserved = (
                db.session.query(QuotaReservation)
                .filter_by(user_id=user_id, day=day_key, status="reserved")
                .count()
            )

            if active_reserved >= MAX_ACTIVE_RESERVATIONS:
                db.session.rollback()
                return False, consumed_count, active_reserved, None

            if (consumed_count + active_reserved) >= limit:
                db.session.rollback()
                return False, consumed_count, active_reserved, None

            reservation = QuotaReservation(
                user_id=user_id,
                day=day_key,
                status="reserved",
                request_id=request_id,
                created_at=now,
                updated_at=now,
            )
            db.session.add(reservation)
            db.session.flush()
            reservation_id = reservation.id

        db.session.commit()
        return True, consumed_count, active_reserved + 1, reservation_id
    except SQLAlchemyError as e:
        db.session.rollback()
        logging.getLogger(__name__).exception("[QUOTA] Reservation failed for user %s", user_id)
        raise QuotaInternalError() from e


def finalize_quota_reservation(reservation_id: Optional[int], user_id: int, day_key: date, now_utc: Optional[datetime] = None) -> Tuple[bool, int]:
    """
    Mark reservation as consumed and increment quota counter.
    """
    if not reservation_id:
        return False, get_daily_quota_usage(user_id, day_key)
    now = now_utc or _utcnow()
    try:
        with db.session.begin_nested():
            reservation = (
                db.session.query(QuotaReservation)
                .filter_by(id=reservation_id, user_id=user_id, day=day_key)
                .with_for_update()
                .first()
            )
            if not reservation or reservation.status != "reserved":
                db.session.rollback()
                return False, get_daily_quota_usage(user_id, day_key)

            quota = _get_or_create_quota_row(user_id, day_key, now)
            quota.count += 1
            quota.updated_at = now

            reservation.status = "consumed"
            reservation.updated_at = now

        db.session.commit()
        return True, quota.count
    except SQLAlchemyError as e:
        logger.error("[QUOTA] Finalize failed for user %s: %s", user_id, type(e).__name__)
        db.session.rollback()
        return False, get_daily_quota_usage(user_id, day_key)


def release_quota_reservation(reservation_id: Optional[int], user_id: int, day_key: date, now_utc: Optional[datetime] = None) -> bool:
    """
    Release a reservation (refund quota) if it was still reserved.
    """
    if not reservation_id:
        return False
    now = now_utc or _utcnow()
    try:
        with db.session.begin_nested():
            reservation = (
                db.session.query(QuotaReservation)
                .filter_by(id=reservation_id, user_id=user_id, day=day_key)
                .with_for_update()
                .first()
            )
            if reservation and reservation.status == "reserved":
                reservation.status = "released"
                reservation.updated_at = now
        db.session.commit()
        return True
    except SQLAlchemyError as e:
        logger.warning("[QUOTA] Release failed for user %s: %s", user_id, type(e).__name__)
        db.session.rollback()
        return False


def check_and_increment_daily_quota(user_id: int, limit: int, day_key: date, now_utc: Optional[datetime] = None) -> Tuple[bool, int]:
    """
    Atomically check and increment the daily quota for a user.
    
    Phase 1E: Race-safe quota enforcement using DailyQuotaUsage table with unique constraint.
    
    Args:
        user_id: The user's ID
        limit: The daily limit (e.g., USER_DAILY_LIMIT)
        
    Returns:
        Tuple of (allowed: bool, current_count: int)
        - allowed: True if within quota (incremented), False if quota exceeded
        - current_count: The count AFTER increment (if allowed) or current count (if rejected)
    """

    now = now_utc or _utcnow()

    try:
        with db.session.begin_nested():
            try:
                quota = (
                    db.session.query(DailyQuotaUsage)
                    .filter_by(user_id=user_id, day=day_key)
                    .with_for_update()
                    .first()
                )
            except SQLAlchemyError:
                quota = (
                    db.session.query(DailyQuotaUsage)
                    .filter_by(user_id=user_id, day=day_key)
                    .first()
                )

            if quota is None:
                quota = DailyQuotaUsage(user_id=user_id, day=day_key, count=0, updated_at=now)
                db.session.add(quota)
                db.session.flush()

            if quota.count >= limit:
                db.session.rollback()
                return False, quota.count

            quota.count += 1
            quota.updated_at = now

        db.session.commit()
        return True, quota.count

    except IntegrityError:
        db.session.rollback()
        # Retry once after handling potential race on insert
        try:
            with db.session.begin_nested():
                quota = (
                    db.session.query(DailyQuotaUsage)
                    .filter_by(user_id=user_id, day=day_key)
                    .with_for_update()
                    .first()
                )

                if quota is None:
                    quota = DailyQuotaUsage(user_id=user_id, day=day_key, count=0, updated_at=now)
                    db.session.add(quota)
                    db.session.flush()

                if quota.count >= limit:
                    db.session.rollback()
                    return False, quota.count

                quota.count += 1
                quota.updated_at = now

            db.session.commit()
            return True, quota.count
        except SQLAlchemyError as e:
            logger.error("[QUOTA] Error after retry for user %s: %s", user_id, type(e).__name__)
            db.session.rollback()
            return False, 0

    except SQLAlchemyError as e:
        # Unexpected error, log and deny to be safe
        logger.error("[QUOTA] Error checking quota for user %s: %s", user_id, type(e).__name__)
        db.session.rollback()
        return False, 0


def rollback_quota_increment(user_id: int, day_key: date) -> int:
    """
    Roll back a previously recorded quota increment (best-effort).
    """
    try:
        with db.session.begin_nested():
            quota = (
                db.session.query(DailyQuotaUsage)
                .filter_by(user_id=user_id, day=day_key)
                .with_for_update()
                .first()
            )
            if quota and quota.count > 0:
                quota.count -= 1
                quota.updated_at = _utcnow()
                current = quota.count
            else:
                current = quota.count if quota else 0
        db.session.commit()
        return current
    except SQLAlchemyError as e:
        logger.error("[QUOTA] rollback failed for user %s: %s", user_id, type(e).__name__)
        db.session.rollback()
        return 0
