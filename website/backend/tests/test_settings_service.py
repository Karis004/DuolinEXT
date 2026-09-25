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
