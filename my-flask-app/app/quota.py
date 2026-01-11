import logging
import os
from datetime import datetime, time, timedelta, date
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from flask import request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.models import DailyQuotaUsage, QuotaReservation, IpRateLimit

# Defaults (overridden by main shim/tests)
GLOBAL_DAILY_LIMIT = 1000
USER_DAILY_LIMIT = 5
MAX_CACHE_DAYS = 45
PER_IP_PER_MIN_LIMIT = 20
QUOTA_RESERVATION_TTL_SECONDS = 600
MAX_ACTIVE_RESERVATIONS = 1


def resolve_app_timezone() -> Tuple[ZoneInfo, str]:
    tz_name = os.environ.get("APP_TZ", "UTC").strip() or "UTC"
    try:
        return ZoneInfo(tz_name), tz_name
    except Exception:
        fallback = "UTC"
        print(f"[QUOTA] ⚠️ Invalid APP_TZ='{tz_name}', falling back to UTC")
        return ZoneInfo(fallback), fallback


def compute_quota_window(tz: ZoneInfo, *, now: Optional[datetime] = None) -> Tuple[date, datetime, datetime, datetime, datetime, int]:
    now_utc = now.astimezone(ZoneInfo("UTC")) if now else datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
    now_tz = now_utc.astimezone(tz) if tz else now_utc
    day_key = now_tz.date()
    window_start = datetime.combine(day_key, time.min, tzinfo=tz)
    window_end = datetime.combine(day_key, time.max, tzinfo=tz)
    resets_at = datetime.combine(day_key + timedelta(days=1), time.min, tzinfo=tz)
    retry_after = max(0, int((resets_at - now_tz).total_seconds()))
    return day_key, window_start, window_end, resets_at, now_tz, retry_after


def parse_owner_emails(raw: str) -> list:
    return [item.strip().lower() for item in (raw or "").split(",") if item and item.strip()]


class ModelOutputInvalidError(ValueError):
    pass


class QuotaInternalError(RuntimeError):
    pass


def log_access_decision(route_name: str, user_id: Optional[int], decision: str, reason: str = ""):
    user_info = f"user_id={user_id}" if user_id else "anonymous"
    log_msg = f"[ACCESS] {route_name} | {user_info} | {decision}"
    if reason:
        log_msg += f" | {reason}"
    print(log_msg)


def get_client_ip() -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip() if request else ""
    ip = xff or (request.remote_addr or "")
    return ip[:64] if ip else "unknown"


def get_daily_quota_usage(user_id: int, day_key: date) -> int:
    quota = DailyQuotaUsage.query.filter_by(user_id=user_id, day=day_key).first()
    return quota.count if quota else 0


def cleanup_expired_reservations(user_id: int, day_key: date, now_utc: Optional[datetime] = None) -> int:
    now = now_utc or datetime.utcnow()
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
        pass

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
        quota = DailyQuotaUsage(user_id=user_id, day=day_key, count=0, updated_at=now_utc)
        db.session.add(quota)
        db.session.flush()
    return quota


def reserve_daily_quota(user_id: int, day_key: date, limit: int, request_id: str, now_utc: Optional[datetime] = None):
    now = now_utc or datetime.utcnow()
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


def finalize_quota_reservation(reservation_id: Optional[int], user_id: int, day_key: date, now_utc: Optional[datetime] = None):
    if not reservation_id:
        return False, get_daily_quota_usage(user_id, day_key)
    now = now_utc or datetime.utcnow()
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
    except SQLAlchemyError:
        db.session.rollback()
        return False, get_daily_quota_usage(user_id, day_key)


def release_quota_reservation(reservation_id: Optional[int], user_id: int, day_key: date, now_utc: Optional[datetime] = None) -> bool:
    if not reservation_id:
        return False
    now = now_utc or datetime.utcnow()
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
    except SQLAlchemyError:
        db.session.rollback()
        return False


def check_and_increment_ip_rate_limit(ip: str, limit: int = PER_IP_PER_MIN_LIMIT, now_utc: Optional[datetime] = None):
    now = now_utc or datetime.utcnow()
    window_start = now.replace(second=0, microsecond=0)
    resets_at = window_start + timedelta(minutes=1)
    cleanup_before = window_start - timedelta(days=1)

    def _increment_record():
        db.session.query(IpRateLimit).filter(IpRateLimit.window_start < cleanup_before).delete(synchronize_session=False)

        bind = db.session.get_bind()
        dialect_name = bind.dialect.name if bind else ""
        base_values = {"ip": ip, "window_start": window_start, "count": 1, "updated_at": now}
        try:
            if dialect_name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                stmt = (
                    pg_insert(IpRateLimit)
                    .values(**base_values)
                    .on_conflict_do_update(
                        index_elements=["ip", "window_start"],
                        set_={"count": IpRateLimit.__table__.c.count + 1, "updated_at": now},
                    )
                    .returning(IpRateLimit.count)
                )
                result = db.session.execute(stmt)
                new_count = result.scalar_one()
            elif dialect_name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                stmt = (
                    sqlite_insert(IpRateLimit)
                    .values(**base_values)
                    .on_conflict_do_update(
                        index_elements=["ip", "window_start"],
                        set_={"count": IpRateLimit.__table__.c.count + 1, "updated_at": now},
                    )
                )
                db.session.execute(stmt)
                record = (
                    db.session.query(IpRateLimit)
                    .filter_by(ip=ip, window_start=window_start)
                    .first()
                )
                new_count = record.count if record else 0
            else:
                raise SQLAlchemyError("dialect_upsert_not_supported")

            if new_count > limit:
                db.session.rollback()
                record = (
                    db.session.query(IpRateLimit)
                    .filter_by(ip=ip, window_start=window_start)
                    .first()
                )
                current_count = record.count if record else limit
                return False, current_count
            return True, new_count
        except IntegrityError:
            db.session.rollback()
        except SQLAlchemyError:
            pass

        try:
            record = (
                db.session.query(IpRateLimit)
                .filter_by(ip=ip, window_start=window_start)
                .with_for_update()
                .first()
            )
        except SQLAlchemyError:
            record = (
                db.session.query(IpRateLimit)
                .filter_by(ip=ip, window_start=window_start)
                .first()
            )

        if record is None:
            record = IpRateLimit(ip=ip, window_start=window_start, count=0, updated_at=now)
            db.session.add(record)
            db.session.flush()

        if record.count >= limit:
            db.session.rollback()
            return False, record.count

        record.count += 1
        record.updated_at = now
        return True, record.count

    try:
        with db.session.begin_nested():
            ok, count = _increment_record()
            if not ok:
                return False, count, resets_at

        db.session.commit()
        return True, count, resets_at
    except IntegrityError:
        db.session.rollback()
        try:
            with db.session.begin_nested():
                ok, count = _increment_record()
                if not ok:
                    return False, count, resets_at

            db.session.commit()
            return True, count, resets_at
        except Exception:
            db.session.rollback()
            return False, 0, resets_at


def rollback_quota_increment(user_id: int, day_key: date) -> int:
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
                quota.updated_at = datetime.utcnow()
                current = quota.count
            else:
                current = quota.count if quota else 0
        db.session.commit()
        return current
    except SQLAlchemyError:
        db.session.rollback()
        return 0
