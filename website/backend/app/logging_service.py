import json
import logging
import threading
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_
from sqlmodel import Session, col, select

from .config import get_settings
from .models import SystemLog

logger = logging.getLogger(__name__)

LOG_RETENTION = 5000
MAX_MESSAGE_CHARS = 2000
MAX_DETAILS_CHARS = 12000
PRUNE_EVERY_WRITES = 200
LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}

_engine = None
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_prune_lock = threading.Lock()
_writes_since_prune = 0


def set_request_id(value: str):
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    return _request_id.get()


def _get_engine():
    global _engine
    if _engine is None:
        from .database import engine

        _engine = engine
    return _engine


def redact_text(text: str, settings=None) -> str:
    if not text:
        return text
    try:
        active_settings = settings or get_settings()
    except Exception:
        return text
    for secret in (active_settings.ai_api_key, active_settings.duolingo_jwt):
        if secret:
            text = text.replace(secret, "[已隐藏的密钥]")
    return text


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {"unserializable": str(value)[:MAX_DETAILS_CHARS]}


def log_event(
    level: str,
    category: str,
    message: str,
    *,
    details: Any = None,
    duration_ms: int | None = None,
    request_id: str | None = None,
) -> int | None:
    """Write one developer log entry. Never raises into the caller."""
    global _writes_since_prune

    normalized_level = (level or "INFO").upper()
    if normalized_level not in LEVELS:
        normalized_level = "INFO"
    safe_message = redact_text(str(message or ""))[:MAX_MESSAGE_CHARS]
    payload = _json_safe(details if details is not None else {})
    encoded_details = json.dumps(payload, ensure_ascii=False)
    if len(encoded_details) > MAX_DETAILS_CHARS:
        encoded_details = json.dumps(
            {
                "truncated": True,
                "preview": encoded_details[:MAX_DETAILS_CHARS],
            },
            ensure_ascii=False,
        )
    encoded_details = redact_text(encoded_details)
    active_request_id = request_id or get_request_id()

    try:
        timestamp = datetime.now(ZoneInfo(get_settings().app_timezone))
    except Exception:
        timestamp = datetime.now()

    entry = SystemLog(
        created_at=timestamp,
        level=normalized_level,
        category=str(category or "app")[:40],
        message=safe_message,
        details_json=encoded_details,
        duration_ms=duration_ms,
        request_id=active_request_id,
    )

    db: Session | None = None
    try:
        db = Session(_get_engine())
        db.add(entry)
        db.commit()
        db.refresh(entry)
        entry_id = entry.id
    except Exception as exc:  # Logging must never break a request.
        try:
            if db is not None:
                db.rollback()
        except Exception:
            pass
        logger.warning("Failed to write system log: %s", exc)
        return None
    finally:
        if db is not None:
            db.close()

    should_prune = False
    with _prune_lock:
        _writes_since_prune += 1
        if _writes_since_prune >= PRUNE_EVERY_WRITES:
            _writes_since_prune = 0
            should_prune = True
    if should_prune:
        prune_logs()
    return entry_id


def prune_logs(keep: int = LOG_RETENTION) -> None:
    try:
        with Session(_get_engine()) as session:
            newest = session.exec(select(func.max(SystemLog.id))).one()
            if newest is None or newest <= keep:
                return
            session.exec(delete(SystemLog).where(SystemLog.id <= newest - keep))
            session.commit()
    except Exception as exc:
        logger.warning("Failed to prune system logs: %s", exc)


def _decode_details(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}


def serialize_log(row: SystemLog) -> dict:
    return {
        "id": row.id,
        "createdAt": row.created_at.isoformat(timespec="milliseconds"),
        "level": row.level,
        "category": row.category,
        "message": row.message,
        "details": _decode_details(row.details_json),
        "durationMs": row.duration_ms,
        "requestId": row.request_id,
    }


def list_logs(
    *,
    limit: int = 200,
    level: str | None = None,
    category: str | None = None,
    query: str | None = None,
    after_id: int | None = None,
) -> dict:
    safe_limit = max(1, min(int(limit), 500))
    filters = []
    if level and level.upper() != "ALL":
        filters.append(SystemLog.level == level.upper())
    if category and category.upper() != "ALL":
        filters.append(SystemLog.category == category)
    search = (query or "").strip()
    if search:
        filters.append(
            or_(
                SystemLog.message.contains(search),
                SystemLog.details_json.contains(search),
                SystemLog.category.contains(search),
                SystemLog.request_id.contains(search),
            )
        )
    if after_id is not None:
        filters.append(SystemLog.id > after_id)

    try:
        with Session(_get_engine()) as session:
            statement = select(SystemLog)
            count_statement = select(func.count()).select_from(SystemLog)
            if filters:
                statement = statement.where(*filters)
                count_statement = count_statement.where(*filters)
            rows = session.exec(
                statement.order_by(col(SystemLog.id).desc()).limit(safe_limit)
            ).all()
            total = session.exec(count_statement).one()
    except Exception as exc:
        logger.warning("Failed to read system logs: %s", exc)
        return {"items": [], "total": 0, "latestId": None, "retention": LOG_RETENTION}

    items = [serialize_log(row) for row in rows]
    return {
        "items": items,
        "total": total,
        "latestId": items[0]["id"] if items else None,
        "retention": LOG_RETENTION,
    }


def export_logs() -> list[dict]:
    try:
        with Session(_get_engine()) as session:
            rows = session.exec(
                select(SystemLog)
                .order_by(col(SystemLog.id).asc())
                .limit(LOG_RETENTION)
            ).all()
    except Exception as exc:
        logger.warning("Failed to export system logs: %s", exc)
        return []
    return [serialize_log(row) for row in rows]


def clear_logs() -> int:
    try:
        with Session(_get_engine()) as session:
            total = session.exec(
                select(func.count()).select_from(SystemLog)
            ).one()
            session.exec(delete(SystemLog))
            session.commit()
            return int(total or 0)
    except Exception as exc:
        logger.warning("Failed to clear system logs: %s", exc)
        return 0
