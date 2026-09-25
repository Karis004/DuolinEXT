from time import monotonic
from urllib.parse import urlsplit, urlunsplit

import httpx

from .ai import (
    AIConfigurationError,
    AIError,
    AITransientError,
    AIValidationError,
    _chat_completions_url,
    _message_content_text,
)
from .config import AI_TIMEOUT_SECONDS, Settings
from .duolingo import DuolingoClient, DuolingoError


def _safe_endpoint(value: str) -> str | None:
    if not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.hostname:
        return "已填写，但 URL 格式无效"
    host = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        return "已填写，但 URL 格式无效"
    if port:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def _masked_id(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    return f"***{value[-4:]}" if len(value) > 4 else "已填写"


def settings_status(settings: Settings) -> dict:
    duolingo_fields = (
        settings.duolingo_jwt,
        settings.duolingo_user_id,
        settings.duolingo_course_id,
        settings.duolingo_from_language,
    )
    return {
        "duolingo": {
            "configured": all(value.strip() for value in duolingo_fields),
            "jwtConfigured": bool(settings.duolingo_jwt.strip()),
            "userIdConfigured": bool(settings.duolingo_user_id.strip()),
            "userIdHint": _masked_id(settings.duolingo_user_id),
            "courseId": settings.duolingo_course_id.strip() or None,
            "fromLanguage": settings.duolingo_from_language.strip() or None,
        },
        "ai": {
            "configured": settings.ai_configured,
            "apiKeyConfigured": bool(settings.ai_api_key.strip()),
            "endpoint": _safe_endpoint(settings.ai_base_url),
            "model": settings.ai_model.strip() or None,
            "reasoningEffort": settings.ai_reasoning_effort.strip() or "关闭",
        },
    }


def test_ai_connection(settings: Settings) -> dict:
    if not settings.ai_configured:
        raise AIConfigurationError("AI_API_KEY、AI_BASE_URL、AI_MODEL 尚未配置完整。")

    request_body = {
        "model": settings.ai_model,
        "temperature": 0,
        "max_tokens": 16,
        "messages": [
            {
                "role": "system",
                "content": "This is a connection test. Reply with exactly OK.",
            },
            {"role": "user", "content": "OK"},
        ],
    }
    if settings.ai_reasoning_effort:
        request_body["reasoning_effort"] = settings.ai_reasoning_effort

    started = monotonic()
    try:
        response = httpx.post(
            _chat_completions_url(settings.ai_base_url),
            headers={
                "Authorization": f"Bearer {settings.ai_api_key}",
                "Content-Type": "application/json",
            },
            json=request_body,
            timeout=httpx.Timeout(min(AI_TIMEOUT_SECONDS, 15), connect=5.0),
        )
    except httpx.TimeoutException as exc:
        raise AITransientError("AI 连接测试超时。") from exc
    except httpx.RequestError as exc:
        raise AITransientError(f"AI 连接失败：{exc.__class__.__name__}。") from exc

    if response.status_code in {401, 403}:
        raise AIConfigurationError(f"AI 鉴权失败（HTTP {response.status_code}）。")
    if response.status_code >= 400:
        raise AIError(f"AI 服务返回 HTTP {response.status_code}。")

    try:
        answer = _message_content_text(response.json()["choices"][0]["message"]).strip()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIValidationError("AI 服务已响应，但返回格式不兼容。") from exc
    if not answer:
        raise AIValidationError("AI 服务已响应，但没有返回文本。")

    return {
        "success": True,
        "latencyMs": round((monotonic() - started) * 1000),
        "model": settings.ai_model,
        "message": answer[:80],
    }


def test_duolingo_connection(
    settings: Settings,
    client: DuolingoClient | None = None,
) -> dict:
    if not settings.duolingo_jwt.strip():
        raise DuolingoError("token_missing", "未配置 DUOLINGO_JWT。")
    if not settings.duolingo_user_id.strip():
        raise DuolingoError("user_id_missing", "未配置 DUOLINGO_USER_ID。")

    started = monotonic()
    active_client = client or DuolingoClient(settings)
    course_data = active_client.fetch_current_course()
    skills = active_client.extract_course_progress(course_data)
    return {
        "success": True,
        "latencyMs": round((monotonic() - started) * 1000),
        "courseId": course_data.get("currentCourseId") or settings.duolingo_course_id,
        "learningLanguage": course_data.get("learningLanguage") or settings.duolingo_course_id,
        "fromLanguage": course_data.get("fromLanguage") or settings.duolingo_from_language,
        "skillCount": len(skills),
    }
