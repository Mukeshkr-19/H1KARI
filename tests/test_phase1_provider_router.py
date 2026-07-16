from unittest.mock import Mock

import core.router as router_module
from core.router import AIRouter, PROVIDER_CONFIGS


def _router(*available: str) -> AIRouter:
    router = AIRouter()
    for name, status in router.providers.items():
        status.available = name in available
    return router


def test_approved_primary_returns_request_local_evidence(monkeypatch):
    router = _router("google", "groq")
    router._last_provider = "stale-global-provider"
    router._last_model = "stale-global-model"
    monkeypatch.setattr(router, "_select_provider", lambda quality: "google")
    call = Mock(return_value="answer")
    monkeypatch.setattr(router, "_try_generate_once", call)

    result = router.generate_document(
        "summarize this document",
        allowed_providers=("google",),
        before_provider_call=lambda provider: True,
    )

    assert result.text == "answer"
    assert result.provider == "google"
    assert result.model == PROVIDER_CONFIGS["google"]["models"]["smart"]
    assert result.attempted_providers == ("google",)
    call.assert_called_once()


def test_failed_approved_primary_falls_back_only_to_approved(monkeypatch):
    router = _router("google", "groq", "cerebras")
    monkeypatch.setattr(router, "_select_provider", lambda quality: "google")
    call = Mock(side_effect=[None, "fallback answer"])
    monkeypatch.setattr(router, "_try_generate_once", call)

    result = router.generate_document(
        "summarize this document",
        allowed_providers=("google", "groq"),
        before_provider_call=lambda provider: True,
    )

    assert result.provider == "groq"
    assert result.attempted_providers == ("google", "groq")
    assert [args.args[0] for args in call.call_args_list] == ["google", "groq"]


def test_unapproved_provider_is_never_checked_or_called(monkeypatch):
    router = _router("google", "groq")
    monkeypatch.setattr(router, "_select_provider", lambda quality: "google")
    checked = []
    call = Mock(return_value="answer")
    monkeypatch.setattr(router, "_try_generate_once", call)

    result = router.generate_document(
        "summarize this document",
        allowed_providers=("groq",),
        before_provider_call=lambda provider: checked.append(provider) or True,
    )

    assert result.provider == "groq"
    assert checked == ["groq"]
    assert [args.args[0] for args in call.call_args_list] == ["groq"]


def test_callback_denial_skips_provider_attempt(monkeypatch):
    router = _router("google", "groq")
    monkeypatch.setattr(router, "_select_provider", lambda quality: "google")
    call = Mock(return_value="answer")
    monkeypatch.setattr(router, "_try_generate_once", call)

    result = router.generate_document(
        "summarize this document",
        allowed_providers=("google", "groq"),
        before_provider_call=lambda provider: provider == "groq",
    )

    assert result.provider == "groq"
    assert result.attempted_providers == ("groq",)
    assert [args.args[0] for args in call.call_args_list] == ["groq"]


def test_ollama_only_is_selectable(monkeypatch):
    router = _router("ollama")
    monkeypatch.setattr(router, "_select_provider", lambda quality: None)
    call = Mock(return_value="local answer")
    monkeypatch.setattr(router, "_try_generate_once", call)

    result = router.generate_document(
        "summarize this document",
        allowed_providers=("ollama",),
        before_provider_call=lambda provider: True,
    )

    assert result.provider == "ollama"
    assert result.attempted_providers == ("ollama",)
    assert call.call_args.args[0] == "ollama"


def test_empty_allowed_set_makes_no_calls(monkeypatch):
    router = _router("google")
    approval = Mock(return_value=True)
    call = Mock(return_value="answer")
    monkeypatch.setattr(router, "_try_generate_once", call)

    result = router.generate_document(
        "summarize this document",
        allowed_providers=(),
        before_provider_call=approval,
    )

    assert result.text is None
    assert result.attempted_providers == ()
    approval.assert_not_called()
    call.assert_not_called()


def test_all_failures_return_attempt_evidence(monkeypatch):
    router = _router("google", "groq")
    monkeypatch.setattr(router, "_select_provider", lambda quality: "google")
    monkeypatch.setattr(router, "_try_generate_once", Mock(return_value=None))

    result = router.generate_document(
        "summarize this document",
        allowed_providers=("google", "groq"),
        before_provider_call=lambda provider: True,
    )

    assert result.text is None
    assert result.provider is None
    assert result.model is None
    assert result.attempted_providers == ("google", "groq")


def test_one_approval_never_falls_through_to_second_transport(monkeypatch):
    router = _router("groq")
    monkeypatch.setattr(router, "_select_provider", lambda quality: "groq")
    monkeypatch.setattr(router_module, "LITELLM_AVAILABLE", True)
    litellm_call = Mock(return_value=None)
    direct_call = Mock(return_value="must not be used")
    approval = Mock(return_value=True)
    monkeypatch.setattr(router, "_call_litellm", litellm_call)
    monkeypatch.setattr(router, "_call_direct_api", direct_call)

    result = router.generate_document(
        "summarize this document",
        allowed_providers=("groq",),
        before_provider_call=approval,
    )

    assert result.text is None
    approval.assert_called_once_with("groq")
    litellm_call.assert_called_once()
    direct_call.assert_not_called()


def test_caller_order_wins_and_duplicates_are_ignored(monkeypatch):
    router = _router("google", "ollama")
    monkeypatch.setattr(router, "_select_provider", lambda quality: "google")
    call = Mock(return_value=None)
    monkeypatch.setattr(router, "_try_generate_once", call)

    result = router.generate_document(
        "summarize this document",
        allowed_providers=("ollama", "google", "ollama"),
        before_provider_call=lambda provider: True,
    )

    assert result.attempted_providers == ("ollama", "google")
    assert [args.args[0] for args in call.call_args_list] == ["ollama", "google"]
