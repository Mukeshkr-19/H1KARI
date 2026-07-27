"""Current fallback-provider configuration and adapter tests (no network)."""

from core.router import AIRouter, PROVIDER_CONFIGS


def test_current_fallback_model_defaults(monkeypatch):
    for prefix in ("GROQ", "CEREBRAS", "NVIDIA", "COHERE", "OPENROUTER"):
        for quality in ("FAST", "BALANCED", "SMART"):
            monkeypatch.delenv(f"{prefix}_{quality}_MODEL", raising=False)

    assert PROVIDER_CONFIGS["groq"]["models"] == {
        "fast": "qwen/qwen3-32b",
        "balanced": "qwen/qwen3-32b",
        "smart": "openai/gpt-oss-120b",
    }
    assert PROVIDER_CONFIGS["cerebras"]["models"] == {
        "fast": "llama3.1-8b",
        "balanced": "zai-glm-4.7",
        "smart": "gpt-oss-120b",
    }
    assert PROVIDER_CONFIGS["nvidia"]["models"] == {
        "fast": "nvidia/nemotron-3-nano-30b-a3b",
        "balanced": "nvidia/nemotron-3-super-120b-a12b",
        "smart": "nvidia/nemotron-3-ultra-550b-a55b",
    }
    assert set(PROVIDER_CONFIGS["cohere"]["models"].values()) == {
        "command-a-plus-05-2026"
    }
    assert set(PROVIDER_CONFIGS["openrouter"]["models"].values()) == {
        "openrouter/free"
    }


def test_provider_keys_strip_whitespace_and_google_alias(monkeypatch):
    monkeypatch.setenv("GOOGLE_AI_STUDIO_KEY", "   ")
    monkeypatch.setenv("GEMINI_API_KEY", " alias-key ")
    router = AIRouter()
    assert router.providers["google"].available is True
    monkeypatch.setenv("GROQ_API_KEY", "   ")
    assert AIRouter().providers["groq"].available is False


def test_litellm_model_name_does_not_duplicate_provider_prefix():
    from core.router import _litellm_model_name

    assert _litellm_model_name("nvidia", "nvidia/nemotron-3-ultra-550b-a55b") == (
        "nvidia/nemotron-3-ultra-550b-a55b"
    )
    assert _litellm_model_name("cerebras", "gpt-oss-120b") == "cerebras/gpt-oss-120b"


def test_cohere_v2_chat_extracts_only_text_blocks(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}

        @staticmethod
        def iter_content(chunk_size):
            assert chunk_size > 0
            yield (
                b'{"message":{"role":"assistant","content":['
                b'{"type":"thinking","thinking":"hidden"},'
                b'{"type":"text","text":"OK"}]}}'
            )

    def fake_post(url, *, json, headers, timeout, stream):
        captured.update(url=url, payload=json, headers=headers)
        return Response()

    monkeypatch.setenv("COHERE_API_KEY", "synthetic-key")
    monkeypatch.setattr("core.router.requests.post", fake_post)
    result = AIRouter()._call_cohere(
        "command-a-plus-05-2026",
        [
            {"role": "system", "content": "Synthetic instruction."},
            {"role": "user", "content": "Synthetic request."},
        ],
        32,
    )

    assert result == "OK"
    assert captured["url"] == "https://api.cohere.ai/v2/chat"
    assert "message" not in captured["payload"]
    assert len(captured["payload"]["messages"]) == 2
    assert "synthetic-key" not in repr(captured["payload"])
