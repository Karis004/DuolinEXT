import json
import re
import time

import httpx

from .config import AI_TIMEOUT_SECONDS, Settings
from .logging_service import log_event

LESSON_PROMPT_VERSION = 4
OUTLINE_PROMPT_VERSION = 3
VOCABULARY_PROMPT_VERSION = 1


class AIError(ValueError):
    pass


class AITransientError(AIError):
    pass


class AIValidationError(AIError):
    pass


class AIConfigurationError(AIError):
    pass

SYSTEM_PROMPT = """你是一位为中文母语成年人设计系统法语课程的教师。
课程目标不是背词，而是让学习者逐步理解法语句子的结构，并能用已学词汇自主造句。
词汇与语法必须同步推进：每节课讲清一项可操作的语法能力，并有明确的先修关系、规则、变换过程和造句练习。
课程面向刚入门、学习时间有限的成年人：一节 5-10 分钟，只保留理解当前句型必需的信息。
不要假设学习者已经理解主语、谓语、宾语、补语、性数一致或动词变位；只有累计课程大纲明确记录过的知识才可视为已学。
讲解使用准确的语法与语音术语，但不做术语堆砌；不要使用中文谐音、幼儿式口型模仿或空泛鼓励。
发音只说明实际怎么读：给出 IPA，指出最容易读错或最影响连读的一点，不做音系学分析。
只输出有效 JSON，不要输出 Markdown 代码块。"""


GRAMMAR_SEQUENCE = """建议的语法能力顺序（根据词汇适配，但不得无故跳级）：
1. 句子成分与基本陈述句：主语 + 变位动词 + 宾语/表语/其他补语
2. 主语人称代词，以及 être / avoir 等高频动词的现在时
3. 名词阴阳性、单复数和不定/定冠词
4. 规则 -er 动词与常见不规则动词的现在时人称变位
5. 形容词性数一致及常见位置
6. 否定句 ne ... pas 与省音
7. 一般疑问句：语调、est-ce que；再逐步引入倒装
8. 特殊疑问词与疑问句词序
9. 所有格限定词、指示限定词
10. 介词与 à/de + 冠词缩合
11. 直接/间接宾语代词及位置
12. 命令式、近未来、复合过去时，再逐步进入从句
每节只引入一个主要新能力，可复用和考查已达到 practiced/usable 的旧能力。"""


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if not base:
        return "/v1/chat/completions"
    if not re.search(r"/v1$", base, flags=re.IGNORECASE):
        base = f"{base}/v1"
    return f"{base}/chat/completions"


def _redact_ai_error(text: str, settings: Settings) -> str:
    for secret in (settings.ai_api_key, settings.duolingo_jwt):
        if secret:
            text = text.replace(secret, "[已隐藏的密钥]")
    return text


def _format_ai_http_error(response: httpx.Response, settings: Settings, action: str) -> str:
    status = f"HTTP {response.status_code} {response.reason_phrase}".strip()
    request_id = next(
        (
            response.headers[name]
            for name in ("x-request-id", "x-correlation-id", "cf-ray", "x-amzn-requestid")
            if response.headers.get(name)
        ),
        None,
    )
    body = _redact_ai_error(response.text, settings)
    parts = [f"{action}失败（{status}）"]
    if request_id:
        parts.append(f"请求 ID：{request_id}")
    parts.append(f"服务端原始响应：\n{body if body.strip() else '（空响应）'}")
    return "\n".join(parts)


def _format_ai_network_error(exc: httpx.RequestError, settings: Settings, action: str) -> str:
    detail = _redact_ai_error(str(exc), settings).strip()
    return f"{action}网络请求失败（{exc.__class__.__name__}）：{detail or '没有更多错误信息'}"


def _message_content_text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(content, dict):
        text = content.get("text") or content.get("content")
        return text if isinstance(text, str) else ""
    return ""


def _parse_json_content(content: str) -> dict:
    cleaned = re.sub(
        r"<think>.*?</think>", "", content, flags=re.IGNORECASE | re.DOTALL
    ).strip()
    cleaned = re.sub(
        r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE
    ).strip()

    decoder = json.JSONDecoder()
    for index, character in enumerate(cleaned):
        if character not in "{[":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError(
        f"AI 返回内容无法解析为课程 JSON（返回类型为 text，长度 {len(content)}）。"
    )


def _daily_prompt(
    words: list[dict],
    skill_title: str,
    level_index: int,
    session_index: int,
    course_context: dict | None = None,
) -> str:
    context = course_context or {
        "previousOutline": None,
        "recentLessons": [],
    }
    return f"""请生成一节约 5-10 分钟的法语补充课。目标是 1-2 屏读完，学完能马上用本课词汇造出一句同类句。

多邻国技能：{skill_title}
Session：{session_index}
本课词汇：{json.dumps(words, ensure_ascii=False)}
此前课程上下文：{json.dumps(context, ensure_ascii=False)}

{GRAMMAR_SEQUENCE}

课程取舍：
- 一节只解决一个语法点。与当前句型无关的用法、例外和扩展一律省略。
- 从上述顺序中选择与给定词汇能自然结合、且最早尚未掌握的一项作为 primaryTopic。
- 讲解要说明句子为什么这样排列、哪个词为什么变化，以及学习者如何自己造出同类句。
- 阅读 previousOutline.grammarLedger。没有大纲或 ledger 为空时，从“句子成分与基本陈述句”开始，不得直接假设学习者会造句。
- status=introduced 的知识只做简短复现；status=practiced 可安排一次检查；status=usable 可直接使用，不再解释。
- 不得把大纲中晚于本 Session 的内容当作先修知识。
- 中文解释用短句，每个字段只说一个重点；不写背景铺垫、鼓励语或同义反复。
- 整节中文解释总量控制在 800-1100 字。宁可少讲一个细节，也不要把页面写满。

严格返回以下结构。所有需要朗读的法语内容必须放在明确的法语字段中，不要把整句法语藏在中文 explanation 里：
{{
  "title": "简短中文标题",
  "overview": "本课词汇主题与可造句能力，不超过 50 字",
  "wordNotes": [{{"word": "法语单词", "ipa": "IPA 音标", "partOfSpeech": "词性及必要的阴阳性", "forms": "本课需要知道的词形变化，可为空", "explanation": "中文用法，1 句，不超过 45 字"}}],
  "pronunciation": {{
    "focus": "一句话说明本课最该注意的读法，不超过 40 字",
    "wordIpa": [{{"word": "本课法语词", "ipa": "规范 IPA"}}],
    "rules": [{{
      "title": "规则名称",
      "type": "spelling|sound|liaison|enchaînement|elision|rhythm",
      "explanation": "触发条件和实际读法，不超过 50 字",
      "examples": [{{"french": "本课中的词或短语", "ipa": "对应 IPA"}}],
      "contrastWithEnglish": "仅当英法同一拼写容易误读时写一句，否则留空"
    }}]
  }},
  "grammarLesson": {{
    "stage": "A0/A1 阶段与能力名称",
    "primaryTopic": "本课唯一的主要新语法能力",
    "objective": "学完后能独立完成的一个造句任务，不超过 40 字",
    "prerequisites": ["本课会复用的已学知识，最多 3 项；没有则为空"],
    "sentencePattern": "抽象句型骨架，例如 主语 + être变位 + 表语",
    "coreExplanation": "用 2-3 句说明句子成分、词序和变化原因，不超过 90 字",
    "rules": [{{
      "title": "核心规则",
      "explanation": "规则及为什么这样变化，不超过 50 字",
      "forms": [{{"french": "词形或结构", "chinese": "含义", "note": "人称/性数/使用条件"}}]
    }}],
    "transformations": [{{
      "label": "陈述变否定/陈述变疑问/人称或性数变化等",
      "before": "变化前法语句子",
      "after": "变化后法语句子",
      "explanation": "指出移动、添加或发生词形变化的部分，不超过 40 字"
    }}],
    "buildSteps": [{{
      "step": 1,
      "french": "逐步构造出的法语成分或句子",
      "chinese": "中文意思",
      "explanation": "这一步选择该形式和位置的原因，不超过 35 字"
    }}],
    "commonMistakes": [{{"wrong": "典型错误", "correct": "正确形式", "explanation": "错误原因，不超过 40 字"}}],
    "reviewPoint": "本课如何复用一项之前学过的语法能力，一句话，可为空"
  }},
  "examples": [{{"french": "法语例句", "ipa": "整句 IPA 音标", "chinese": "中文翻译", "note": "标出本课句型和关键词形，不超过 30 字"}}],
  "exercises": [{{"type": "identify|transform|build|translate", "question": "题目", "french": "法语题干，可为空", "constraints": ["必须使用的结构"], "answerFrench": "法语答案", "answer": "中文答案或补充", "explanation": "按句子成分和词形解析答案，不超过 50 字"}}],
  "summary": ["本课学会的词汇或造句能力，2-3 条"]
}}

质量要求：
- 5-10 分钟能读完：中文解释总量约 800-1100 字，单个中文段落最多 2 句。
- wordNotes 覆盖全部给定词汇，每条只写 1 句用法；必须有 IPA 和准确词性；名词注明阴阳性，动词注明不定式和本课出现的变位。
- grammarLesson 是课程主体，但不能写成语法讲义：1-2 条 rules、1 个 transformation、2-3 个 buildSteps、1-2 个 commonMistakes。
- 3 道 exercises，至少 1 道 transform 和 1 道 build，不要重复同一个句型。
- examples 2-3 句，尽量复用本课词汇，可以加入最少量 A0 功能词使句子自然。
- pronunciation 只回答“怎么读”：wordIpa 覆盖全部给定词汇；rules 只选 1-2 个本课真实出现、影响词形或连读的规则，每条最多 1 个例子。不要逐音素拆解，不要描述口型或气流，不要中文谐音；没有值得说明的现象时 rules 为空。
- 只有英法同一拼写或同一音标确实容易混淆时才写 contrastWithEnglish。
- 不虚构规则、词性、性别或变位。"""


def _outline_prompt(
    previous_outline: dict | None,
    lesson_content: dict,
    skill_title: str,
    level_index: int,
    session_index: int,
) -> str:
    return f"""请在一节新课生成完成后，更新一份紧凑的累计法语课程大纲。

当前位置：{skill_title} / Session {session_index}
此前累计大纲：{json.dumps(previous_outline, ensure_ascii=False)}
本节课程：{json.dumps(lesson_content, ensure_ascii=False)}

严格返回以下 JSON 结构：
{{
  "title": "法语学习总览",
  "currentPosition": "当前学到哪里",
  "courseSummary": "不超过100字的累计进度总结",
  "learnedThemes": ["已经覆盖的交流主题，最多8项"],
  "grammarLedger": [{{
    "topic": "语法能力名称",
    "status": "introduced|practiced|usable",
    "canDo": "学习者目前能自主完成什么",
    "lastPosition": "最近讲解或练习该能力的 Skill / Session"
  }}],
  "pronunciationProgress": ["已经明确学过的语音规则，最多6项"],
  "recentSessions": [{{"position": "Skill / Session", "focus": "本节核心，不超过30字"}}],
  "nextFocus": ["接下来值得巩固的方向，最多4项"]
}}

状态更新规则：本节首次系统讲解的 primaryTopic 标为 introduced；在后续至少一节课中再次用于造句或变换后可升为 practiced；在至少两节更晚课程中作为先修并实际用于造句或变换后才可标 usable。这里记录的是课程覆盖和可复用状态，不是假定学习者通过了测验；不得仅因出现一次就标 usable。

要求：这是累计大纲，不是本节课复述；按语法先修顺序保留全部 grammarLedger，不要删除较早基础知识；相同 topic 合并并更新状态；保留最近最多 6 节 recentSessions；只写课程中确实出现过的能力；不要复述课程正文；整体保持紧凑，供下一节课作为上下文使用。"""


def _normalise_lesson_content(content: dict, words: list[dict]) -> dict:
    """Keep older or partially structured model output renderable."""
    result = dict(content)
    result.setdefault("title", "今天的法语补充课")
    result.setdefault("overview", "围绕今天的词汇，补充发音、语法和自然用法。")
    for key in ("wordNotes", "grammarPoints", "examples", "exercises", "summary"):
        if not isinstance(result.get(key), list):
            result[key] = []

    pronunciation = result.get("pronunciation")
    if not isinstance(pronunciation, dict):
        pronunciation = {}
    pronunciation.setdefault("focus", "本课词汇的 IPA 与语音规则。")
    word_ipa = pronunciation.get("wordIpa")
    if not isinstance(word_ipa, list) or not word_ipa:
        note_by_word = {
            str(note.get("word", "")).casefold(): note
            for note in result["wordNotes"]
            if isinstance(note, dict)
        }
        word_ipa = [
            {
                "word": str(word.get("text", "")),
                "ipa": str(
                    note_by_word.get(
                        str(word.get("text", "")).casefold(), {}
                    ).get("ipa", "")
                ),
            }
            for word in words
        ]
    pronunciation["wordIpa"] = [
        item for item in word_ipa if isinstance(item, dict)
    ]
    rules = pronunciation.get("rules")
    normalised_rules = []
    if isinstance(rules, list):
        for item in rules:
            if not isinstance(item, dict):
                continue
            rule = dict(item)
            examples = rule.get("examples")
            rule["examples"] = (
                [example for example in examples if isinstance(example, dict)]
                if isinstance(examples, list)
                else []
            )
            normalised_rules.append(rule)
    pronunciation["rules"] = normalised_rules[:2]
    items = pronunciation.get("items")
    pronunciation["items"] = (
        [item for item in items if isinstance(item, dict)]
        if isinstance(items, list)
        else []
    )
    result["pronunciation"] = pronunciation
    grammar = result.get("grammarLesson")
    if not isinstance(grammar, dict):
        result["grammarLesson"] = None
    else:
        grammar = dict(grammar)
        for key in (
            "stage",
            "primaryTopic",
            "objective",
            "sentencePattern",
            "coreExplanation",
            "reviewPoint",
        ):
            if not isinstance(grammar.get(key), str):
                grammar[key] = ""
        for key in (
            "prerequisites",
            "rules",
            "transformations",
            "buildSteps",
            "commonMistakes",
        ):
            if not isinstance(grammar.get(key), list):
                grammar[key] = []
        normalised_grammar_rules = []
        for item in grammar["rules"]:
            if not isinstance(item, dict):
                continue
            rule = dict(item)
            forms = rule.get("forms")
            rule["forms"] = (
                [form for form in forms if isinstance(form, dict)]
                if isinstance(forms, list)
                else []
            )
            normalised_grammar_rules.append(rule)
        grammar["rules"] = normalised_grammar_rules
        for key in ("transformations", "buildSteps", "commonMistakes"):
            grammar[key] = [
                item for item in grammar[key] if isinstance(item, dict)
            ]
        result["grammarLesson"] = grammar
    return result


def _validate_lesson_design(content: dict, words: list[dict]) -> None:
    issues = []
    grammar = content.get("grammarLesson")
    if not isinstance(grammar, dict):
        issues.append("缺少 grammarLesson")
    else:
        for key in (
            "primaryTopic",
            "objective",
            "sentencePattern",
            "coreExplanation",
        ):
            if not str(grammar.get(key) or "").strip():
                issues.append(f"grammarLesson.{key} 为空")
        requirements = {
            "rules": 1,
            "transformations": 1,
            "buildSteps": 2,
            "commonMistakes": 1,
        }
        for key, minimum in requirements.items():
            if len(grammar.get(key) or []) < minimum:
                issues.append(f"grammarLesson.{key} 少于 {minimum} 项")

    exercises = content.get("exercises") or []
    exercise_types = {
        str(item.get("type") or "")
        for item in exercises
        if isinstance(item, dict)
    }
    if len(exercises) < 3:
        issues.append("exercises 少于 3 项")
    for required_type in ("build", "transform"):
        if required_type not in exercise_types:
            issues.append(f"exercises 缺少 {required_type} 类型")

    expected_words = {
        str(word.get("text") or "").casefold() for word in words if word.get("text")
    }
    noted_words = {
        str(note.get("word") or "").casefold()
        for note in content.get("wordNotes") or []
        if isinstance(note, dict)
    }
    ipa_words = {
        str(item.get("word") or "").casefold()
        for item in (content.get("pronunciation") or {}).get("wordIpa") or []
        if isinstance(item, dict) and str(item.get("ipa") or "").strip()
    }
    if not expected_words.issubset(noted_words):
        issues.append("wordNotes 未覆盖全部本课词汇")
    if not expected_words.issubset(ipa_words):
        issues.append("pronunciation.wordIpa 未覆盖全部本课词汇或缺少 IPA")

    if issues:
        raise ValueError("AI 课程结构不完整：" + "；".join(issues) + "。")


def _normalise_outline_content(content: dict) -> dict:
    result = dict(content)
    result.setdefault("title", "法语学习总览")
    result.setdefault("currentPosition", "课程起步阶段")
    result.setdefault("courseSummary", "课程大纲正在逐步建立。")
    for key in (
        "learnedThemes",
        "grammarProgress",
        "grammarLedger",
        "pronunciationProgress",
        "recentSessions",
        "nextFocus",
    ):
        if not isinstance(result.get(key), list):
            result[key] = []
    result["learnedThemes"] = result["learnedThemes"][-8:]
    result["grammarProgress"] = result["grammarProgress"][-12:]
    grammar_ledger = []
    for item in result["grammarLedger"]:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        if entry.get("status") not in {"introduced", "practiced", "usable"}:
            entry["status"] = "introduced"
        grammar_ledger.append(entry)
    result["grammarLedger"] = grammar_ledger[-20:]
    result["pronunciationProgress"] = result["pronunciationProgress"][-6:]
    result["recentSessions"] = result["recentSessions"][-6:]
    result["nextFocus"] = result["nextFocus"][-4:]
    return result


def _request_ai_json(
    settings: Settings,
    prompt: str,
    *,
    system_prompt: str = SYSTEM_PROMPT,
    timeout_seconds: float | None = None,
    use_reasoning: bool = True,
    request_kind: str = "lesson",
) -> dict:
    if not settings.ai_configured:
        log_event(
            "ERROR",
            "ai",
            f"AI 配置不完整：{request_kind}",
            details={"kind": request_kind},
        )
        raise AIConfigurationError("AI_API_KEY、AI_BASE_URL、AI_MODEL 尚未配置完整。")

    request_body = {
        "model": settings.ai_model,
        "temperature": 0.35,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    if use_reasoning and settings.ai_reasoning_effort:
        request_body["reasoning_effort"] = settings.ai_reasoning_effort

    effective_timeout = timeout_seconds or AI_TIMEOUT_SECONDS
    url = _chat_completions_url(settings.ai_base_url)
    request_details = {
        "kind": request_kind,
        "url": url,
        "model": settings.ai_model,
        "reasoningEffort": (
            settings.ai_reasoning_effort if use_reasoning else None
        ),
        "timeoutSeconds": effective_timeout,
        "promptChars": len(prompt),
        "systemPromptChars": len(system_prompt),
        "messageCount": 2,
        "responseFormat": "json_object",
    }
    started = time.monotonic()
    log_event("INFO", "ai", f"AI 请求开始：{request_kind}", details=request_details)

    try:
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.ai_api_key}",
                "Content-Type": "application/json",
            },
            json=request_body,
            timeout=httpx.Timeout(effective_timeout, connect=10.0),
        )
    except httpx.TimeoutException as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        log_event(
            "ERROR",
            "ai",
            f"AI 请求超时：{request_kind}",
            details={
                **request_details,
                "elapsedMs": elapsed_ms,
                "errorType": exc.__class__.__name__,
                "error": str(exc),
            },
            duration_ms=elapsed_ms,
        )
        raise AITransientError(
            f"AI 请求超时（上限 {effective_timeout:g} 秒）。\n"
            + _format_ai_network_error(exc, settings, "AI")
        ) from exc
    except httpx.RequestError as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        log_event(
            "ERROR",
            "ai",
            f"AI 网络请求失败：{request_kind}",
            details={
                **request_details,
                "elapsedMs": elapsed_ms,
                "errorType": exc.__class__.__name__,
                "error": str(exc),
            },
            duration_ms=elapsed_ms,
        )
        raise AITransientError(_format_ai_network_error(exc, settings, "AI")) from exc

    elapsed_ms = round((time.monotonic() - started) * 1000)
    request_id = next(
        (
            response.headers[name]
            for name in ("x-request-id", "x-correlation-id", "cf-ray", "x-amzn-requestid")
            if response.headers.get(name)
        ),
        None,
    )
    response_details = {
        **request_details,
        "statusCode": response.status_code,
        "reasonPhrase": response.reason_phrase,
        "elapsedMs": elapsed_ms,
        "bodyChars": len(response.text),
        "requestId": request_id,
    }

    if response.status_code in (401, 403):
        log_event(
            "ERROR",
            "ai",
            f"AI 鉴权失败：{request_kind}（HTTP {response.status_code}）",
            details={
                **response_details,
                "errorBody": _redact_ai_error(response.text, settings)[:4000],
            },
            duration_ms=elapsed_ms,
        )
        raise AIConfigurationError(_format_ai_http_error(response, settings, "AI 鉴权"))
    if response.status_code >= 400:
        error_type = (
            AITransientError
            if response.status_code in {408, 409, 425, 429}
            or response.status_code >= 500
            else AIError
        )
        log_event(
            "ERROR" if response.status_code >= 500 else "WARNING",
            "ai",
            f"AI 请求失败：{request_kind}（HTTP {response.status_code}）",
            details={
                **response_details,
                "errorBody": _redact_ai_error(response.text, settings)[:4000],
            },
            duration_ms=elapsed_ms,
        )
        raise error_type(_format_ai_http_error(response, settings, "AI 请求"))

    content = ""
    try:
        payload = response.json()
        message = payload["choices"][0]["message"]
        content = _message_content_text(message)
        if not content:
            raise ValueError("AI 响应中没有可解析的 message.content。")
        parsed = _parse_json_content(content)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        log_event(
            "ERROR",
            "ai",
            f"AI 响应解析失败：{request_kind}",
            details={
                **response_details,
                "errorType": exc.__class__.__name__,
                "error": str(exc),
                "contentChars": len(content),
            },
            duration_ms=elapsed_ms,
        )
        raise AIValidationError("AI 响应结构中没有有效的课程内容。") from exc

    usage = payload.get("usage") if isinstance(payload, dict) else None
    server_model = payload.get("model") if isinstance(payload, dict) else None
    log_event(
        "INFO",
        "ai",
        f"AI 响应成功：{request_kind}",
        details={
            **response_details,
            "serverModel": server_model,
            "contentChars": len(content),
            "parseOk": True,
            "usage": usage,
        },
        duration_ms=elapsed_ms,
    )
    return parsed


TUTOR_SYSTEM_PROMPT = """你是 DuolinEXT 的法语学习即时辅导老师。
用户正在学习法语。只回答用户当前的小问题，优先使用中文，必要时保留法语例子。
回答准确、直接、简短，通常不超过 120 字；不要复述上下文，不要写课程总结，不要展开无关语法。
如果问题信息不足，明确指出需要补充什么。只返回纯文本，不要 Markdown 标题。"""


def ask_tutor(
    settings: Settings,
    *,
    skill_title: str,
    session_index: int,
    course_outline: dict | None,
    selected_text: str,
    question: str,
    messages: list[dict] | None = None,
) -> str:
    compact_messages = []
    for message in (messages or [])[-6:]:
        role = message.get("role")
        text = str(message.get("content") or "").strip()
        if role in {"user", "assistant"} and text:
            compact_messages.append({"role": role, "content": text[:800]})
    prompt = json.dumps(
        {
            "course": f"{skill_title} / Session {session_index}",
            "outline": course_outline or {},
            "selectedText": selected_text[:1000],
            "question": question[:1000],
            "recentConversation": compact_messages,
        },
        ensure_ascii=False,
    )
    request_body = {
        "model": settings.ai_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": TUTOR_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 320,
    }
    if settings.ai_reasoning_effort:
        request_body["reasoning_effort"] = settings.ai_reasoning_effort
    if not settings.ai_configured:
        log_event("ERROR", "tutor", "AI 辅导配置不完整")
        raise AIConfigurationError("AI_API_KEY、AI_BASE_URL、AI_MODEL 尚未配置完整。")
    tutor_timeout = min(AI_TIMEOUT_SECONDS, 35)
    request_details = {
        "kind": "tutor",
        "url": _chat_completions_url(settings.ai_base_url),
        "model": settings.ai_model,
        "reasoningEffort": settings.ai_reasoning_effort or None,
        "timeoutSeconds": tutor_timeout,
        "promptChars": len(prompt),
        "questionChars": len(question),
        "selectedChars": len(selected_text),
        "historyCount": len(compact_messages),
        "maxTokens": 320,
    }
    started = time.monotonic()
    log_event("INFO", "tutor", "AI 辅导请求开始", details=request_details)
    try:
        response = httpx.post(
            _chat_completions_url(settings.ai_base_url),
            headers={
                "Authorization": f"Bearer {settings.ai_api_key}",
                "Content-Type": "application/json",
            },
            json=request_body,
            timeout=httpx.Timeout(tutor_timeout, connect=5.0),
        )
    except httpx.TimeoutException as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        log_event(
            "ERROR",
            "tutor",
            "AI 辅导请求超时",
            details={
                **request_details,
                "elapsedMs": elapsed_ms,
                "errorType": exc.__class__.__name__,
                "error": str(exc),
            },
            duration_ms=elapsed_ms,
        )
        raise AITransientError(
            f"AI 辅导请求超时（上限 {tutor_timeout:g} 秒）。\n"
            + _format_ai_network_error(exc, settings, "AI 辅导")
        ) from exc
    except httpx.RequestError as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        log_event(
            "ERROR",
            "tutor",
            "AI 辅导网络请求失败",
            details={
                **request_details,
                "elapsedMs": elapsed_ms,
                "errorType": exc.__class__.__name__,
                "error": str(exc),
            },
            duration_ms=elapsed_ms,
        )
        raise AITransientError(_format_ai_network_error(exc, settings, "AI 辅导")) from exc

    elapsed_ms = round((time.monotonic() - started) * 1000)
    request_id = next(
        (
            response.headers[name]
            for name in ("x-request-id", "x-correlation-id", "cf-ray", "x-amzn-requestid")
            if response.headers.get(name)
        ),
        None,
    )
    response_details = {
        **request_details,
        "statusCode": response.status_code,
        "reasonPhrase": response.reason_phrase,
        "elapsedMs": elapsed_ms,
        "bodyChars": len(response.text),
        "requestId": request_id,
    }
    if response.status_code >= 400:
        log_event(
            "ERROR" if response.status_code >= 500 else "WARNING",
            "tutor",
            f"AI 辅导请求失败（HTTP {response.status_code}）",
            details={
                **response_details,
                "errorBody": _redact_ai_error(response.text, settings)[:4000],
            },
            duration_ms=elapsed_ms,
        )
        raise AIError(_format_ai_http_error(response, settings, "AI 辅导请求"))
    try:
        payload = response.json()
        answer = _message_content_text(payload["choices"][0]["message"]).strip()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        log_event(
            "ERROR",
            "tutor",
            "AI 辅导响应解析失败",
            details={
                **response_details,
                "errorType": exc.__class__.__name__,
                "error": str(exc),
            },
            duration_ms=elapsed_ms,
        )
        raise AIValidationError("AI 辅导没有返回有效回答。") from exc
    if not answer:
        log_event(
            "ERROR",
            "tutor",
            "AI 辅导返回空回答",
            details=response_details,
            duration_ms=elapsed_ms,
        )
        raise AIValidationError("AI 辅导没有返回有效回答。")
    log_event(
        "INFO",
        "tutor",
        "AI 辅导响应成功",
        details={
            **response_details,
            "answerChars": len(answer),
            "usage": payload.get("usage") if isinstance(payload, dict) else None,
        },
        duration_ms=elapsed_ms,
    )
    return answer


VOCABULARY_SYSTEM_PROMPT = """你是法语词汇形态分析器。面向刚入门的中文学习者。
对输入的法语表面词形，找出最合理的原型并给出少量最有用的核心形态。
只保留初学阶段高频、能帮助识别句中变化的形式，不生成完整词典或罕见时态。
只输出有效 JSON，不要 Markdown。"""


def generate_vocabulary_profiles(settings: Settings, words: list[dict]) -> list[dict]:
    prompt = f"""请分析这些法语词形：{json.dumps(words, ensure_ascii=False)}

返回 {{"items": [{{
  "word": "输入的表面词形",
  "lemma": "原型",
  "partOfSpeech": "动词|名词|形容词|代词|介词|副词|冠词|其他",
  "gender": "阳性|阴性|不适用",
  "meaning": "最常见中文义",
  "usageNote": "一句简短识别提示",
  "forms": [{{"form": "核心变形", "label": "现在时第一人称单数/阴性单数等", "note": "一句短提示"}}]
}}]}}

规则：动词至少给不定式、现在时第一/第二/第三人称单数、第一人称复数和过去分词；名词给阴阳性与单复数；形容词给阴阳性与单复数。其他词只给真实且有助识别的形式。forms 最多 8 个，必须包含输入词形。"""
    result = _request_ai_json(
        settings,
        prompt,
        system_prompt=VOCABULARY_SYSTEM_PROMPT,
        timeout_seconds=min(AI_TIMEOUT_SECONDS, 30),
        use_reasoning=False,
        request_kind="vocabulary",
    )
    items = result.get("items")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def generate_lesson_content(
    settings: Settings,
    words: list[dict],
    skill_title: str = "法语课程",
    level_index: int = 0,
    session_index: int = 1,
    course_context: dict | None = None,
) -> dict:
    content = _request_ai_json(
        settings,
        _daily_prompt(
            words,
            skill_title,
            level_index,
            session_index,
            course_context,
        ),
        request_kind="lesson",
    )
    normalised = _normalise_lesson_content(content, words)
    try:
        _validate_lesson_design(normalised, words)
    except ValueError as exc:
        raise AIValidationError(str(exc)) from exc
    return normalised


def generate_outline_content(
    settings: Settings,
    previous_outline: dict | None,
    lesson_content: dict,
    skill_title: str,
    level_index: int,
    session_index: int,
) -> dict:
    content = _request_ai_json(
        settings,
        _outline_prompt(
            previous_outline,
            lesson_content,
            skill_title,
            level_index,
            session_index,
        ),
        request_kind="outline",
    )
    return _normalise_outline_content(content)
