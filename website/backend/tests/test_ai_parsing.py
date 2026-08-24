import pytest

from app.ai import (
    LESSON_PROMPT_VERSION,
    OUTLINE_PROMPT_VERSION,
    _daily_prompt,
    _message_content_text,
    _normalise_lesson_content,
    _normalise_outline_content,
    _parse_json_content,
    _validate_lesson_design,
)


def test_parse_json_from_markdown_and_extra_text():
    content = "课程如下：\n```json\n{\"title\": \"今天的法语课\", \"summary\": []}\n```\n"
    assert _parse_json_content(content)["title"] == "今天的法语课"


def test_parse_json_after_thinking_block():
    content = "<think>先组织内容</think>\n{\"title\": \"法语课\"}"
    assert _parse_json_content(content)["title"] == "法语课"


def test_read_segmented_message_content():
    message = {"content": [{"type": "text", "text": "{\"title\": \"法语课\"}"}]}
    assert _message_content_text(message) == '{"title": "法语课"}'


def test_normalise_lesson_content_adds_pronunciation_fallback():
    content = {
        "wordNotes": [{"word": "bonjour", "ipa": "/bɔ̃.ʒuʁ/", "explanation": "你好"}],
    }
    result = _normalise_lesson_content(content, [{"text": "bonjour"}])
    assert result["pronunciation"]["wordIpa"][0]["word"] == "bonjour"
    assert result["pronunciation"]["wordIpa"][0]["ipa"] == "/bɔ̃.ʒuʁ/"
    assert result["pronunciation"]["items"] == []
    assert result["pronunciation"]["rules"] == []
    assert result["examples"] == []


def test_v2_prompt_requires_progressive_sentence_building_and_technical_pronunciation():
    prompt = _daily_prompt(
        [{"text": "bonjour", "translations": ["你好"]}],
        "Greetings",
        0,
        1,
    )

    assert LESSON_PROMPT_VERSION == 3
    assert OUTLINE_PROMPT_VERSION == 2
    assert '"grammarLesson"' in prompt
    assert '"sentencePattern"' in prompt
    assert '"buildSteps"' in prompt
    assert "句子成分与基本陈述句" in prompt
    assert "不要中文谐音" in prompt
    assert "rules 只选 1-3 个" in prompt


def test_normalise_v2_lesson_and_outline_nested_structures():
    lesson = _normalise_lesson_content(
        {
            "grammarLesson": {"primaryTopic": "基本陈述句", "rules": [{}]},
            "pronunciation": {"rules": [{"title": "省音"}]},
        },
        [{"text": "bonjour"}],
    )
    outline = _normalise_outline_content(
        {
            "grammarLedger": [
                {
                    "topic": "基本陈述句",
                    "status": "introduced",
                    "canDo": "识别句子骨架",
                    "lastPosition": "Session 1",
                }
            ]
        }
    )

    assert lesson["grammarLesson"]["buildSteps"] == []
    assert lesson["grammarLesson"]["rules"][0]["forms"] == []
    assert lesson["pronunciation"]["rules"][0]["examples"] == []
    assert outline["grammarLedger"][0]["status"] == "introduced"


def test_v2_design_validation_rejects_shallow_grammar():
    with pytest.raises(ValueError, match="grammarLesson.rules"):
        _validate_lesson_design(
            {
                "grammarLesson": {
                    "primaryTopic": "基本陈述句",
                    "objective": "能够造句",
                    "sentencePattern": "主语 + 动词",
                    "coreExplanation": "解释句子成分。",
                    "rules": [],
                    "transformations": [],
                    "buildSteps": [],
                    "commonMistakes": [],
                },
                "wordNotes": [{"word": "bonjour"}],
                "pronunciation": {
                    "wordIpa": [{"word": "bonjour", "ipa": "/bɔ̃.ʒuʁ/"}]
                },
                "exercises": [],
            },
            [{"text": "bonjour"}],
        )
