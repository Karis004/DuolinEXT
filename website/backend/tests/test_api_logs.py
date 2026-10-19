from app.logging_service import clear_logs, log_event
from app.main import logs as logs_endpoint


def test_logs_endpoint_returns_recent_entries():
    clear_logs()
    log_event(
        "ERROR",
        "ai",
        "模型不可用",
        details={"statusCode": 503, "requestId": "req-1"},
        request_id="req-1",
    )

    payload = logs_endpoint(limit=10)

    assert payload["total"] == 1
    assert payload["items"][0]["level"] == "ERROR"
    assert payload["items"][0]["details"]["statusCode"] == 503
    assert payload["items"][0]["requestId"] == "req-1"
