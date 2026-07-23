"""Contracts for optional local OpenAI-compatible routing gateways."""

import json
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
    assert _provider_model("omniroute", "balanced") == "auto/chat"


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
    response.headers = {}
    response.iter_content.return_value = [
        json.dumps(
            {"choices": [{"message": {"content": "safe response"}}]}
        ).encode("utf-8")
    ]
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
        "stream",
    }
    assert call.kwargs["json"]["stream"] is True
    assert "image" not in repr(call.kwargs["json"]).casefold()
    assert call.kwargs["stream"] is True


def test_gateway_accepts_bounded_sse_and_excludes_reasoning(monkeypatch):
    _clear_gateway_env(monkeypatch)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "omni-local-key")
    router = AIRouter()
    response = Mock(status_code=200)
    response.headers = {"Content-Type": "text/event-stream; charset=utf-8"}
    response.iter_content.return_value = [
        b'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n',
        b'data: {"choices":[{"delta":{"reasoning":"private reasoning"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"safe "}}]}\n',
        b'data: {"choices":[{"delta":{"content":"response"},"finish_reason":"stop"}]}\n',
        b'data: [DONE]\n',
    ]
    monkeypatch.setattr(router_module.requests, "post", Mock(return_value=response))

    result = router._call_direct_api(
        "omniroute",
        "auto/chat",
        [{"role": "user", "content": "hello"}],
    )

    assert result == "safe response"
    assert "private reasoning" not in result


def test_gateway_rejects_malformed_or_error_sse(monkeypatch):
    _clear_gateway_env(monkeypatch)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "omni-local-key")
    router = AIRouter()

    for body in (
        b"data: not-json\n",
        b'data: {"error":{"code":"private"}}\n',
        b'data: {"choices":[{"delta":{"content":7}}]}\n',
    ):
        response = Mock(status_code=200)
        response.headers = {"Content-Type": "text/event-stream"}
        response.iter_content.return_value = [body]
        monkeypatch.setattr(
            router_module.requests,
            "post",
            Mock(return_value=response),
        )

        assert (
            router._call_direct_api(
                "omniroute",
                "auto/chat",
                [{"role": "user", "content": "hello"}],
            )
            is None
        )


def test_gateway_rejects_oversized_response_before_json_parse(monkeypatch):
    _clear_gateway_env(monkeypatch)
    monkeypatch.setenv("NINEROUTER_API_KEY", "nine-local-key")
    router = AIRouter()
    response = Mock(status_code=200)
    response.headers = {"Content-Length": str(1_048_577)}
    response.iter_content.side_effect = AssertionError("body must not be read")
    monkeypatch.setattr(router_module.requests, "post", Mock(return_value=response))

    assert (
        router._call_direct_api(
            "9router",
            "free-forever",
            [{"role": "user", "content": "hello"}],
        )
        is None
    )
    response.iter_content.assert_not_called()


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


def test_gateway_exception_does_not_log_exception_content(monkeypatch, capsys):
    _clear_gateway_env(monkeypatch)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "omni-local-key")
    monkeypatch.setattr(router_module, "is_quiet", lambda: False)
    monkeypatch.setattr(
        router_module.requests,
        "post",
        Mock(side_effect=RuntimeError("private upstream exception content")),
    )
    router = AIRouter()

    assert (
        router._call_direct_api(
            "omniroute",
            "auto",
            [{"role": "user", "content": "hello"}],
        )
        is None
    )
    output = capsys.readouterr().out
    assert "private upstream exception content" not in output
    assert "local gateway request failed" in output
