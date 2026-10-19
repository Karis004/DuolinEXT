import json

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from app import logging_service
from app.config import Settings
from app.logging_service import (
    clear_logs,
    export_logs,
    list_logs,
    log_event,
    prune_logs,
)


def install_engine(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(logging_service, "_engine", engine)
    return engine


def test_log_event_round_trip_and_filters(monkeypatch):
    install_engine(monkeypatch)

    log_event(
        "INFO",
        "ai",
        "AI 请求开始",
        details={"kind": "lesson", "statusCode": 200},
        duration_ms=12,
        request_id="req-1",
    )
    log_event(
        "ERROR",
        "http",
        "GET /api/example -> 500",
        details={"statusCode": 500},
    )

    data = list_logs()

    assert data["total"] == 2
    assert data["items"][0]["level"] == "ERROR"
    assert data["items"][1]["details"]["kind"] == "lesson"
    assert data["items"][1]["requestId"] == "req-1"
    assert list_logs(level="ERROR")["total"] == 1
    assert list_logs(category="ai")["total"] == 1
    assert list_logs(query="req-1")["total"] == 1
    assert list_logs(query="500")["total"] == 1


def test_log_event_redacts_configured_secrets(monkeypatch):
    install_engine(monkeypatch)
    monkeypatch.setattr(
        logging_service,
        "get_settings",
        lambda: Settings(
            ai_api_key="ai-secret-value",
            duolingo_jwt="jwt-secret-value",
            _env_file=None,
        ),
    )

    log_event(
        "ERROR",
        "ai",
        "failed with ai-secret-value",
        details={"body": "jwt-secret-value"},
    )

    serialized = json.dumps(list_logs(), ensure_ascii=False)

    assert "ai-secret-value" not in serialized
    assert "jwt-secret-value" not in serialized
    assert "[已隐藏的密钥]" in serialized


def test_prune_keeps_only_latest_entries(monkeypatch):
    install_engine(monkeypatch)
    for index in range(4):
        log_event("INFO", "app", f"entry {index}")

    prune_logs(keep=2)
    data = list_logs()

    assert data["total"] == 2
    assert [item["message"] for item in data["items"]] == ["entry 3", "entry 2"]


def test_export_and_clear(monkeypatch):
    install_engine(monkeypatch)
    log_event("INFO", "app", "first")
    log_event("INFO", "app", "second")

    exported = export_logs()
    assert [item["message"] for item in exported] == ["first", "second"]

    assert clear_logs() == 2
    assert list_logs()["total"] == 0
