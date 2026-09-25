import httpx
import pytest

from app.ai import AIError, _format_ai_network_error, ask_tutor
from app.config import Settings, get_settings, reload_settings
from app.settings_service import (
    _safe_endpoint,
    settings_status,
    test_ai_connection as check_ai_connection,
    test_duolingo_connection as check_duolingo_connection,
)


class FakeResponse:
    status_code = 200

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": "OK"}}]}


class FakeDuolingoClient:
    @staticmethod
    def fetch_current_course():
        return {
            "currentCourse": {"pathSectioned": []},
            "currentCourseId": "course-id",
            "learningLanguage": "fr",
            "fromLanguage": "zh",
        }

    @staticmethod
    def extract_course_progress(_course_data):
        return [{"skillId": "one"}, {"skillId": "two"}]


def test_safe_endpoint_removes_credentials_and_query():
    assert _safe_endpoint("https://user:secret@api.example.com/v1/?token=hidden") == (
        "https://api.example.com/v1"
    )
    assert _safe_endpoint("https://api.example.com:not-a-port/v1") == (
        "已填写，但 URL 格式无效"
    )


def test_settings_status_never_returns_secrets():
    settings = Settings(
        duolingo_jwt="jwt-secret",
        duolingo_user_id="12345678",
        ai_api_key="ai-secret",
        ai_base_url="https://api.example.com/v1?token=hidden",
        ai_model="model-name",
        _env_file=None,
    )

    payload = settings_status(settings)

    assert payload["duolingo"]["configured"] is True
    assert payload["duolingo"]["userIdHint"] == "***5678"
    assert payload["ai"]["endpoint"] == "https://api.example.com/v1"
    assert "jwt-secret" not in str(payload)
    assert "ai-secret" not in str(payload)
    assert "hidden" not in str(payload)


def test_ai_connection_uses_small_request(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["body"] = kwargs["json"]
        return FakeResponse()

    monkeypatch.setattr("app.settings_service.httpx.post", fake_post)
    result = check_ai_connection(
        Settings(
            ai_api_key="key",
            ai_base_url="https://api.example.com/v1",
            ai_model="model-name",
            ai_reasoning_effort="low",
            _env_file=None,
        )
    )

    assert result["success"] is True
    assert result["message"] == "OK"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["body"]["max_tokens"] == 16
    assert captured["body"]["reasoning_effort"] == "low"


def test_ai_connection_shows_full_upstream_error_and_request_id(monkeypatch):
    long_detail = "上游暂不可用\n" + "具体原因" * 100

    def fake_post(_url, **_kwargs):
        return httpx.Response(
            503,
            text=f"{long_detail}\nsecret-key-123",
            headers={"x-request-id": "request-42"},
        )

    monkeypatch.setattr("app.settings_service.httpx.post", fake_post)
    settings = Settings(
        ai_api_key="secret-key-123",
        ai_base_url="https://api.example.com/v1",
        ai_model="model-name",
        _env_file=None,
    )

    with pytest.raises(AIError) as error:
        check_ai_connection(settings)

    message = str(error.value)
    assert "HTTP 503 Service Unavailable" in message
    assert "请求 ID：request-42" in message
    assert long_detail in message
    assert "secret-key-123" not in message
    assert "[已隐藏的密钥]" in message


def test_ai_tutor_shows_upstream_response_body(monkeypatch):
    monkeypatch.setattr(
        "app.ai.httpx.post",
        lambda _url, **_kwargs: httpx.Response(503, text='{"error":"provider overloaded"}'),
    )
    settings = Settings(
        ai_api_key="secret-key-123",
        ai_base_url="https://api.example.com/v1",
        ai_model="model-name",
        _env_file=None,
    )

    with pytest.raises(AIError, match="provider overloaded"):
        ask_tutor(
            settings,
            skill_title="基础",
            session_index=1,
            course_outline=None,
            selected_text="",
            question="bonjour 是什么意思？",
        )


def test_ai_network_error_includes_exception_detail_without_key():
    settings = Settings(ai_api_key="secret-key-123", _env_file=None)
    error = httpx.ConnectError("DNS failed for secret-key-123")

    message = _format_ai_network_error(error, settings, "AI 连接检测")

    assert "ConnectError" in message
    assert "DNS failed" in message
    assert "secret-key-123" not in message


def test_duolingo_connection_is_read_only_course_check():
    result = check_duolingo_connection(
        Settings(
            duolingo_jwt="jwt",
            duolingo_user_id="12345678",
            _env_file=None,
        ),
        FakeDuolingoClient(),
    )

    assert result == {
        "success": True,
        "latencyMs": result["latencyMs"],
        "courseId": "course-id",
        "learningLanguage": "fr",
        "fromLanguage": "zh",
        "skillCount": 2,
    }


def test_reload_settings_discards_cached_environment_values(monkeypatch):
    monkeypatch.setenv("AI_MODEL", "before-reload")
    get_settings.cache_clear()
    assert get_settings().ai_model == "before-reload"

    monkeypatch.setenv("AI_MODEL", "after-reload")
    assert reload_settings().ai_model == "after-reload"

    get_settings.cache_clear()
