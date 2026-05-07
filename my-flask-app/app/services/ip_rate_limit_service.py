# -*- coding: utf-8 -*-
"""Client IP and per-minute IP rate-limit helpers."""

from datetime import datetime, timedelta
from typing import Optional, Tuple

from flask import request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.config import PER_IP_PER_MIN_LIMIT
from app.extensions import db
from app.models import IpRateLimit
from app.utils.http_helpers import _utcnow


def get_client_ip() -> str:
    """Resolve client IP — use remote_addr which is already set by ProxyFix."""
    ip = request.remote_addr or ""
    return ip[:64] if ip else "unknown"


def check_and_increment_ip_rate_limit(ip: str, limit: int = PER_IP_PER_MIN_LIMIT, now_utc: Optional[datetime] = None) -> Tuple[bool, int, datetime]:
    """
    Atomically enforce per-IP minute window limit.
    """
    now = now_utc or _utcnow()
    window_start = now.replace(second=0, microsecond=0)
    resets_at = window_start + timedelta(minutes=1)
    cleanup_before = window_start - timedelta(days=1)

    def _increment_record() -> Tuple[bool, int]:
        # Cleanup old buckets to avoid unbounded growth (best-effort, same transaction).
        db.session.query(IpRateLimit).filter(IpRateLimit.window_start < cleanup_before).delete(synchronize_session=False)

        # Try dialect upsert to avoid duplicate inserts under concurrency
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
            # Fallback to legacy lock-based approach if dialect upsert unavailable
            db.session.rollback()

        try:
            record = (
                db.session.query(IpRateLimit)
                .filter_by(ip=ip, window_start=window_start)
                .with_for_update()
                .first()
            )
        except SQLAlchemyError:
            db.session.rollback()
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
