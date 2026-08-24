from app.ai import _chat_completions_url


def test_chat_completions_url_adds_v1_for_proxy_root():
    assert _chat_completions_url("https://api.example.com") == (
        "https://api.example.com/v1/chat/completions"
    )


def test_chat_completions_url_preserves_v1_base():
    assert _chat_completions_url("https://api.example.com/v1/") == (
        "https://api.example.com/v1/chat/completions"
    )
