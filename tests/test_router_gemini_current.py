"""Current Gemini routing and payload compatibility tests (no network)."""

from types import SimpleNamespace

from core.router import AIRouter, PROVIDER_CONFIGS


def test_current_gemini_defaults_are_stable_models(monkeypatch):
    for name in ("GOOGLE_FAST_MODEL", "GOOGLE_BALANCED_MODEL", "GOOGLE_SMART_MODEL"):
        monkeypatch.delenv(name, raising=False)

    models = PROVIDER_CONFIGS["google"]["models"]
    assert models == {
        "fast": "gemini-3.5-flash-lite",
        "balanced": "gemini-3.6-flash",
        "smart": "gemini-3.6-flash",
    }


def test_document_text_is_not_misclassified_as_hi_greeting(monkeypatch):
    router = AIRouter()
    assert router._classify_task("summarize this document") == "file_analysis"


def test_google_is_primary_and_gateways_are_late_fallbacks(monkeypatch):
    monkeypatch.setenv("GOOGLE_AI_STUDIO_KEY", "synthetic-key")
    monkeypatch.setenv("OMNIROUTE_API_KEY", "synthetic-key")
    router = AIRouter()

    assert router._select_provider("fast") == "google"
    assert router._select_provider("balanced") == "google"
    assert router._select_provider("smart") == "google"


def test_gemini_3_payload_omits_deprecated_temperature(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}

        @staticmethod
        def iter_content(chunk_size):
            assert chunk_size > 0
            yield b'{"candidates":[{"content":{"parts":[{"text":"OK"}]}}]}'

    def fake_post(url, *, json, timeout, stream):
        captured.update(url=url, payload=json, timeout=timeout, stream=stream)
        return Response()

    monkeypatch.setattr("core.router.requests.post", fake_post)
    answer = AIRouter()._call_google_direct(
        "gemini-3.6-flash",
        [{"role": "user", "content": "synthetic health check"}],
        16,
        0.7,
        "synthetic-key",
    )

    assert answer == "OK"
    assert captured["payload"]["generationConfig"] == {"maxOutputTokens": 16}
    assert "synthetic-key" not in repr(captured["payload"])


def test_legacy_gemini_payload_keeps_temperature(monkeypatch):
    captured = {}
    response = SimpleNamespace(status_code=500, headers={})

    def fake_post(url, *, json, timeout, stream):
        captured["payload"] = json
        return response

    monkeypatch.setattr("core.router.requests.post", fake_post)
    assert AIRouter()._call_google_direct(
        "gemini-2.5-flash",
        [{"role": "user", "content": "synthetic health check"}],
        16,
        0.4,
        "synthetic-key",
    ) is None
    assert captured["payload"]["generationConfig"]["temperature"] == 0.4
