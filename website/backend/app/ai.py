import json
import re

import httpx

from .config import Settings

LESSON_PROMPT_VERSION = 3
OUTLINE_PROMPT_VERSION = 2
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
词汇与语法必须同步推进：每节课深入教授一项可操作的语法能力，并有明确的先修关系、规则、变换过程和造句练习。
不要假设学习者已经理解主语、谓语、宾语、补语、性数一致或动词变位；只有累计课程大纲明确记录过的知识才可视为已学。
讲解面向成年人，使用准确的语法与语音术语；不要使用中文谐音、幼儿式口型模仿或空泛鼓励。
讲解必须准确、具体，优先解释句法结构、冠词与性数、动词人称变位、疑问与否定、固定搭配和容易直译出错的地方。
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
    return f"""请生成一节约 15-20 分钟的法语补充课。内容适合屏幕阅读，信息完整但避免大段文字。

多邻国技能：{skill_title}
Session：{session_index}
本课词汇：{json.dumps(words, ensure_ascii=False)}
此前课程上下文：{json.dumps(context, ensure_ascii=False)}

{GRAMMAR_SEQUENCE}

课程衔接规则：
- 阅读 previousOutline.grammarLedger。没有大纲或 ledger 为空时，从“句子成分与基本陈述句”开始，不得直接假设学习者会造句。
- status=introduced 的知识需要继续解释和练习；status=practiced 可直接使用但至少安排一次检查；status=usable 可作为先修知识，不重复完整讲授。
- 本课选择一个与给定词汇能自然结合、且在上述顺序中最早尚未掌握的语法能力作为 primaryTopic。
- 语法讲解必须回答“句子为什么这样排列、哪个词为什么变化、学习者如何自己造出同类句子”，不能只描述例句含义。
- 不得把大纲中晚于本 Session 的内容当作先修知识。

严格返回以下结构。所有需要朗读的法语内容必须放在明确的法语字段中，不要把整句法语藏在中文 explanation 里：
{{
  "title": "简短中文标题",
  "overview": "本课词汇主题与可造句能力（不超过80字）",
  "wordNotes": [{{"word": "法语单词", "ipa": "IPA 音标", "partOfSpeech": "词性及必要的阴阳性", "forms": "本课需要知道的词形变化，可为空", "explanation": "中文用法讲解，1-2句，不超过70字"}}],
  "pronunciation": {{
    "focus": "本课真正出现的语音规则概述；若无特殊规则则明确说明",
    "wordIpa": [{{"word": "本课法语词", "ipa": "规范 IPA"}}],
    "rules": [{{
      "title": "规则名称",
      "type": "spelling|sound|liaison|enchaînement|elision|rhythm",
      "explanation": "准确说明触发条件和实际读法",
      "examples": [{{"french": "本课中的词或短语", "ipa": "对应 IPA"}}],
      "contrastWithEnglish": "仅当英法同一拼写或音标容易混淆时说明，可为空"
    }}]
  }},
  "grammarLesson": {{
    "stage": "A0/A1 阶段与能力名称",
    "primaryTopic": "本课唯一的主要新语法能力",
    "objective": "学完后学习者能够自主完成的造句任务",
    "prerequisites": ["本课会复用的已学知识；没有则为空"],
    "sentencePattern": "抽象句型骨架，例如 主语 + être变位 + 表语",
    "coreExplanation": "用短句解释各句子成分、词序、变化原因及适用范围，不超过150字",
    "rules": [{{
      "title": "具体规则",
      "explanation": "规则及为什么这样变化，1-2句，不超过80字",
      "forms": [{{"french": "词形或结构", "chinese": "含义", "note": "人称/性数/使用条件"}}]
    }}],
    "transformations": [{{
      "label": "陈述变否定/陈述变疑问/人称或性数变化等",
      "before": "变化前法语句子",
      "after": "变化后法语句子",
      "explanation": "指出移动、添加或发生词形变化的部分，不超过65字"
    }}],
    "buildSteps": [{{
      "step": 1,
      "french": "逐步构造出的法语成分或句子",
      "chinese": "中文意思",
      "explanation": "这一步选择该形式和位置的原因，不超过55字"
    }}],
    "commonMistakes": [{{"wrong": "典型错误", "correct": "正确形式", "explanation": "错误原因，不超过55字"}}],
    "reviewPoint": "本课如何复用一项之前学过的语法能力，可为空"
  }},
  "examples": [{{"french": "法语例句", "ipa": "整句 IPA 音标", "chinese": "中文翻译", "note": "标出本课句型和关键词形，不超过50字"}}],
  "exercises": [{{"type": "identify|transform|build|translate", "question": "题目", "french": "法语题干，可为空", "constraints": ["必须使用的结构"], "answerFrench": "法语答案", "answer": "中文答案或补充", "explanation": "按句子成分和词形解析答案，不超过70字"}}],
  "summary": ["本课学会的词汇和造句能力"]
}}

质量要求：
- 中文解释使用短句或分点式信息；每个字段只解释一个重点，不重复 overview 或其他字段。
- 不写背景铺垫、鼓励语或同义反复；单个中文段落最多 2 句，避免超过 3 行屏幕文字。
- wordNotes 覆盖全部给定词汇，必须有 IPA 和准确词性；名词注明阴阳性，动词注明不定式和本课出现的变位。
- grammarLesson 是课程主体，不能空泛。至少 2 条 rules、1 个 transformation、3 个 buildSteps、2 个 commonMistakes。
- transformations 只教授当前阶段可理解的变化；若本课尚未学否定或疑问，用人称、性数或成分替换展示结构变化。
- 4-6 个 exercises，必须包含至少一道逐步造句 build 和一道句子变换 transform，不能全部是识别题。
- examples 和造句尽量复用本课词汇，可以加入最少量 A0 功能词使句子自然。
- pronunciation.wordIpa 覆盖全部给定词汇；rules 只选 1-3 个本课真实出现且可归纳的规则。不要逐词写“口型/气流/像中文某音”，不要中文谐音；没有特殊现象时 rules 可为空。
- 只有存在真实的拼写或音值干扰时才与英语对比。准确区分 liaison、enchaînement、élision 和普通连读；不要把法语重音写成英语式单词固定重音。
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
  "courseSummary": "不超过120字的累计进度总结",
  "learnedThemes": ["已经覆盖的交流主题，最多8项"],
  "grammarLedger": [{{
    "topic": "语法能力名称",
    "status": "introduced|practiced|usable",
    "canDo": "学习者目前能自主完成什么",
    "lastPosition": "最近讲解或练习该能力的 Skill / Session"
  }}],
  "pronunciationProgress": ["已经明确学过的语音规则，最多6项"],
  "recentSessions": [{{"position": "Skill / Session", "focus": "本节核心，不超过40字"}}],
  "nextFocus": ["接下来值得巩固的方向，最多4项"]
}}

状态更新规则：本节首次系统讲解的 primaryTopic 标为 introduced；在后续至少一节课中再次用于造句或变换后可升为 practiced；在至少两节更晚课程中作为先修并实际用于造句或变换后才可标 usable。这里记录的是课程覆盖和可复用状态，不是假定学习者通过了测验；不得仅因出现一次就标 usable。

要求：这是累计大纲，不是本节课复述；按语法先修顺序保留全部 grammarLedger，不要删除较早基础知识；相同 topic 合并并更新状态；保留最近最多 6 节 recentSessions；只写课程中确实出现过的能力；整体保持紧凑，供下一节课作为上下文使用。"""


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
    pronunciation["rules"] = normalised_rules[:3]
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
            "rules": 2,
            "transformations": 1,
            "buildSteps": 3,
            "commonMistakes": 2,
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
    if len(exercises) < 4:
        issues.append("exercises 少于 4 项")
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
) -> dict:
    if not settings.ai_configured:
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

    effective_timeout = timeout_seconds or settings.ai_timeout_seconds
    try:
        response = httpx.post(
            _chat_completions_url(settings.ai_base_url),
            headers={
                "Authorization": f"Bearer {settings.ai_api_key}",
                "Content-Type": "application/json",
            },
            json=request_body,
            timeout=httpx.Timeout(effective_timeout, connect=10.0),
        )
    except httpx.TimeoutException as exc:
        raise AITransientError(
            f"AI 请求超时（超过 {effective_timeout:g} 秒）。"
        ) from exc
    except httpx.RequestError as exc:
        raise AITransientError(f"AI 网络请求失败：{exc.__class__.__name__}。") from exc
    if response.status_code in (401, 403):
        raise AIConfigurationError(f"AI 鉴权失败（HTTP {response.status_code}）。")
    if response.status_code >= 400:
        detail = response.text.strip().replace("\n", " ")
        if len(detail) > 240:
            detail = f"{detail[:240]}..."
        error_type = (
            AITransientError
            if response.status_code in {408, 409, 425, 429}
            or response.status_code >= 500
            else AIError
        )
        raise error_type(
            f"AI 请求失败（HTTP {response.status_code}）："
            f"{detail or '服务端未返回详情'}"
        )

    try:
        payload = response.json()
        message = payload["choices"][0]["message"]
        content = _message_content_text(message)
        if not content:
            raise ValueError("AI 响应中没有可解析的 message.content。")
        return _parse_json_content(content)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AIValidationError("AI 响应结构中没有有效的课程内容。") from exc


TUTOR_SYSTEM_PROMPT = """你是 DuolinEx 的法语学习即时辅导老师。
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
    if not settings.ai_configured:
        raise AIConfigurationError("AI_API_KEY、AI_BASE_URL、AI_MODEL 尚未配置完整。")
    try:
        response = httpx.post(
            _chat_completions_url(settings.ai_base_url),
            headers={
                "Authorization": f"Bearer {settings.ai_api_key}",
                "Content-Type": "application/json",
            },
            json=request_body,
            timeout=httpx.Timeout(min(settings.ai_timeout_seconds, 35), connect=5.0),
        )
    except httpx.TimeoutException as exc:
        raise AITransientError("AI 辅导请求超时。") from exc
    except httpx.RequestError as exc:
        raise AITransientError("AI 辅导网络请求失败。") from exc
    if response.status_code >= 400:
        raise AIError(f"AI 辅导请求失败（HTTP {response.status_code}）。")
    try:
        answer = _message_content_text(response.json()["choices"][0]["message"]).strip()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIValidationError("AI 辅导没有返回有效回答。") from exc
    if not answer:
        raise AIValidationError("AI 辅导没有返回有效回答。")
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
        timeout_seconds=min(settings.ai_timeout_seconds, 30),
        use_reasoning=False,
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
    )
    return _normalise_outline_content(content)
