import httpx

from app.ai import _request_ai_json
from app.config import Settings
from app.logging_service import clear_logs, list_logs


def test_ai_request_logs_metadata_without_prompt_or_content(monkeypatch):
    clear_logs()
    response = httpx.Response(
        200,
        json={
            "model": "test-model",
            "choices": [{"message": {"content": '{"title": "sample"}'}}],
            "usage": {"total_tokens": 12},
        },
    )
    monkeypatch.setattr("app.ai.httpx.post", lambda *_args, **_kwargs: response)
    settings = Settings(
        ai_api_key="secret-key",
        ai_base_url="https://api.example.com/v1",
        ai_model="test-model",
        ai_reasoning_effort="low",
        _env_file=None,
    )

    result = _request_ai_json(settings, "private prompt text", request_kind="lesson")
    logs = list_logs(category="ai")

    assert result == {"title": "sample"}
    assert logs["total"] == 2
    assert logs["items"][0]["message"] == "AI 响应成功：lesson"
    assert logs["items"][1]["message"] == "AI 请求开始：lesson"
    assert logs["items"][1]["details"]["promptChars"] == len("private prompt text")
    assert "prompt" not in logs["items"][1]["details"]
    assert "private prompt text" not in str(logs)
    assert "sample" not in str(logs)
    assert "secret-key" not in str(logs)


def test_ai_failure_logs_redacted_upstream_body(monkeypatch):
    clear_logs()
    response = httpx.Response(
        503,
        text='{"error":"model unavailable","key":"secret-key"}',
        headers={"x-request-id": "req-503"},
    )
    monkeypatch.setattr("app.ai.httpx.post", lambda *_args, **_kwargs: response)
    settings = Settings(
        ai_api_key="secret-key",
        ai_base_url="https://api.example.com/v1",
        ai_model="test-model",
        _env_file=None,
    )

    try:
        _request_ai_json(settings, "prompt", request_kind="lesson")
    except Exception:
        pass
    logs = list_logs(category="ai")

    assert logs["items"][0]["level"] == "ERROR"
    assert logs["items"][0]["details"]["statusCode"] == 503
    assert logs["items"][0]["details"]["requestId"] == "req-503"
    assert "secret-key" not in str(logs)
    assert "[已隐藏的密钥]" in str(logs)
