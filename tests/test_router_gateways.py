"""Contracts for optional local OpenAI-compatible routing gateways."""

from unittest.mock import Mock

import core.router as router_module
from core.router import AIRouter, _local_gateway_base_url, _provider_model


def _clear_gateway_env(monkeypatch) -> None:
    for name in (
        "OMNIROUTE_API_KEY",
        "OMNIROUTE_BASE_URL",
        "OMNIROUTE_FAST_MODEL",
        "OMNIROUTE_BALANCED_MODEL",
        "OMNIROUTE_SMART_MODEL",
        "NINEROUTER_API_KEY",
        "NINEROUTER_BASE_URL",
        "NINEROUTER_FAST_MODEL",
        "NINEROUTER_BALANCED_MODEL",
        "NINEROUTER_SMART_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_gateways_are_disabled_without_explicit_keys(monkeypatch):
    _clear_gateway_env(monkeypatch)
    router = AIRouter()

    assert router.providers["omniroute"].available is False
    assert router.providers["9router"].available is False


def test_configured_gateway_construction_does_not_probe_network(monkeypatch):
    _clear_gateway_env(monkeypatch)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "omni-local-key")
    post = Mock()
    monkeypatch.setattr(router_module.requests, "post", post)

    assert AIRouter().providers["omniroute"].available is True
    post.assert_not_called()


def test_gateways_use_distinct_loopback_defaults(monkeypatch):
    _clear_gateway_env(monkeypatch)

    assert _local_gateway_base_url("omniroute") == "http://127.0.0.1:20128/v1"
    assert _local_gateway_base_url("9router") == "http://127.0.0.1:20129/v1"


def test_gateway_endpoint_rejects_remote_or_credentialed_urls(monkeypatch):
    for value in (
        "https://example.com/v1",
        "http://example.com:20128/v1",
        "http://localhost:20128/v1",
        "http://user:secret@127.0.0.1:20128/v1",
        "http://127.0.0.1:20128/not-v1",
        "http://127.0.0.1:20128/v1?token=secret",
    ):
        monkeypatch.setenv("OMNIROUTE_BASE_URL", value)
        assert _local_gateway_base_url("omniroute") is None


def test_gateway_with_remote_endpoint_is_not_available(monkeypatch):
    _clear_gateway_env(monkeypatch)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "omni-local-key")
    monkeypatch.setenv("OMNIROUTE_BASE_URL", "https://example.com/v1")

    assert AIRouter().providers["omniroute"].available is False


def test_gateway_model_overrides_are_bounded(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_SMART_MODEL", "custom/model-v2")
    assert _provider_model("omniroute", "smart") == "custom/model-v2"

    monkeypatch.setenv("OMNIROUTE_SMART_MODEL", "bad model\nvalue")
    assert _provider_model("omniroute", "smart") is None


def test_configured_omniroute_is_selected_before_9router(monkeypatch):
    _clear_gateway_env(monkeypatch)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "omni-local-key")
    monkeypatch.setenv("NINEROUTER_API_KEY", "nine-local-key")
    router = AIRouter()

    assert router.providers["omniroute"].available is True
    assert router.providers["9router"].available is True
    assert router._select_provider("smart") == "omniroute"


def test_gateway_permissioned_attempt_uses_one_direct_transport(monkeypatch):
    _clear_gateway_env(monkeypatch)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "omni-local-key")
    router = AIRouter()
    monkeypatch.setattr(router_module, "LITELLM_AVAILABLE", True)
    direct = Mock(return_value="answer")
    litellm = Mock(return_value="wrong transport")
    monkeypatch.setattr(router, "_call_direct_api", direct)
    monkeypatch.setattr(router, "_call_litellm", litellm)

    result = router.generate_document(
        "analyze the supplied text",
        allowed_providers=("omniroute",),
        before_provider_call=lambda provider: provider == "omniroute",
    )

    assert result.text == "answer"
    assert result.provider == "omniroute"
    assert result.model == "auto/smart"
    direct.assert_called_once()
    litellm.assert_not_called()


def test_gateway_request_is_openai_compatible_and_text_only(monkeypatch):
    _clear_gateway_env(monkeypatch)
    monkeypatch.setenv("NINEROUTER_API_KEY", "nine-local-key")
    router = AIRouter()
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [{"message": {"content": "safe response"}}]
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(router_module.requests, "post", post)

    result = router._call_direct_api(
        "9router",
        "free-forever",
        [{"role": "user", "content": "hello"}],
        120,
        0.4,
    )

    assert result == "safe response"
    call = post.call_args
    assert call.args[0] == "http://127.0.0.1:20129/v1/chat/completions"
    assert call.kwargs["headers"]["Authorization"] == "Bearer nine-local-key"
    assert set(call.kwargs["json"]) == {
        "model",
        "messages",
        "max_tokens",
        "temperature",
    }
    assert "image" not in repr(call.kwargs["json"]).casefold()


def test_gateway_error_does_not_log_response_content(monkeypatch, capsys):
    _clear_gateway_env(monkeypatch)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "omni-local-key")
    monkeypatch.setattr(router_module, "is_quiet", lambda: False)
    response = Mock(status_code=500, text="private upstream response content")
    monkeypatch.setattr(router_module.requests, "post", Mock(return_value=response))
    router = AIRouter()

    assert (
        router._call_direct_api(
            "omniroute",
            "auto",
            [{"role": "user", "content": "hello"}],
        )
        is None
    )
    assert "private upstream response content" not in capsys.readouterr().out
