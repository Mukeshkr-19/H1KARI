"""Deterministic cloud-vision gateway and explicit-egress tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.action_policy import Actor, ActorContext
from core.vision import VisionAnalysisService, VisionRuntime
from core.vision.cloud import (
    BoundedGatewayRequestRunner,
    CloudVisionConfigurationError,
    GatewayVisionAdapter,
    GatewayVisionConfig,
    GatewayVisionRouter,
    create_optional_gateway_vision_router_from_environment,
)
from core.vision.contracts import (
    VisionCapability,
    VisionObservation,
    VisionObservationKind,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"bounded-image"


class _Runner:
    def __init__(self, text: str | None = "A bounded cloud description.") -> None:
        self.text = text
        self.calls: list[tuple[GatewayVisionConfig, dict[str, object], float]] = []
        self.cancel_count = 0

    def __call__(self, config, payload, *, timeout):
        self.calls.append((config, payload, timeout))
        if self.text is None:
            return None
        return {"choices": [{"message": {"content": self.text}}]}

    def cancel(self) -> None:
        self.cancel_count += 1


def _config(gateway: str = "omniroute") -> GatewayVisionConfig:
    return GatewayVisionConfig(
        gateway=gateway,
        base_url=(
            "http://127.0.0.1:20128/v1"
            if gateway == "omniroute"
            else "http://127.0.0.1:20129/v1"
        ),
        api_key="private-local-gateway-key",
        model="provider/vision-model",
    )


def _actor() -> ActorContext:
    return ActorContext(
        actor_id="guest",
        actor=Actor.GUEST,
        session_id="session-1",
        source="websocket",
    )


def test_gateway_configuration_is_loopback_only_and_content_free() -> None:
    config = _config()
    assert repr(config) == "GatewayVisionConfig(gateway='omniroute')"
    assert config.api_key not in repr(config)
    assert config.model not in repr(config)

    for url in (
        "https://127.0.0.1:20128/v1",
        "http://localhost:20128/v1",
        "http://example.com:20128/v1",
        "http://127.0.0.1:20128/v1?key=secret",
        "http://user:secret@127.0.0.1:20128/v1",
    ):
        with pytest.raises(CloudVisionConfigurationError):
            GatewayVisionConfig(
                gateway="omniroute",
                base_url=url,
                api_key="key",
                model="provider/vision",
            )


def test_gateway_adapter_sends_one_bounded_explicit_vision_request() -> None:
    runner = _Runner()
    adapter = GatewayVisionAdapter(_config(), runner=runner)

    observations = adapter(
        PNG,
        mime_type="image/png",
        capability=VisionCapability.DESCRIBE,
    )

    assert observations == (
        VisionObservation(
            kind=VisionObservationKind.DESCRIPTION,
            text="A bounded cloud description.",
        ),
    )
    assert len(runner.calls) == 1
    config, payload, timeout = runner.calls[0]
    assert config.gateway == "omniroute"
    assert timeout == 30.0
    assert payload["model"] == "provider/vision-model"
    assert payload["stream"] is False
    assert payload["temperature"] == 0
    messages = payload["messages"]
    assert isinstance(messages, list)
    content = messages[0]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert repr(adapter) == "GatewayVisionAdapter()"
    assert "bounded-image" not in repr(adapter)


def test_gateway_adapter_rejects_bad_mime_size_and_unbounded_output() -> None:
    runner = _Runner("x" * 2001)
    adapter = GatewayVisionAdapter(_config(), runner=runner)

    assert adapter(PNG, mime_type="image/jpeg", capability=VisionCapability.OCR) == ()
    assert adapter(
        b"\x89PNG\r\n\x1a\n" + b"x" * 1_048_576,
        mime_type="image/png",
        capability=VisionCapability.OCR,
    ) == ()
    assert adapter(PNG, mime_type="image/png", capability=VisionCapability.OCR) == ()


def test_router_falls_back_in_declared_order_and_cancels_all_routes() -> None:
    first_runner = _Runner(None)
    second_runner = _Runner("visible text")
    first = GatewayVisionAdapter(_config("omniroute"), runner=first_runner)
    second = GatewayVisionAdapter(_config("9router"), runner=second_runner)
    router = GatewayVisionRouter((first, second))

    result = router(PNG, mime_type="image/png", capability=VisionCapability.OCR)

    assert result[0].kind is VisionObservationKind.TEXT
    assert result[0].text == "visible text"
    assert len(first_runner.calls) == 1
    assert len(second_runner.calls) == 1
    router.cancel()
    assert first_runner.cancel_count == 1
    assert second_runner.cancel_count == 1


def test_environment_factory_requires_an_explicit_vision_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "OMNIROUTE_API_KEY",
        "OMNIROUTE_VISION_MODEL",
        "NINEROUTER_API_KEY",
        "NINEROUTER_VISION_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    assert create_optional_gateway_vision_router_from_environment() is None

    monkeypatch.setenv("OMNIROUTE_API_KEY", "local-key")
    assert create_optional_gateway_vision_router_from_environment() is None

    runners: list[_Runner] = []

    def factory() -> _Runner:
        runner = _Runner()
        runners.append(runner)
        return runner

    monkeypatch.setenv("OMNIROUTE_VISION_MODEL", "provider/vision")
    router = create_optional_gateway_vision_router_from_environment(
        runner_factory=factory
    )
    assert isinstance(router, GatewayVisionRouter)
    assert len(runners) == 1


def test_runtime_requires_explicit_cloud_mode_and_preserves_local_default() -> None:
    local_calls: list[bytes] = []
    cloud_calls: list[bytes] = []

    def local(image_bytes: bytes, *, mime_type: str):
        local_calls.append(image_bytes)
        return (
            VisionObservation(
                kind=VisionObservationKind.DESCRIPTION,
                text="local result",
            ),
        )

    def cloud(image_bytes: bytes, *, mime_type: str, capability: VisionCapability):
        cloud_calls.append(image_bytes)
        return (
            VisionObservation(
                kind=VisionObservationKind.DESCRIPTION,
                text="cloud result",
            ),
        )

    ids = iter(("analysis-local", "analysis-cloud"))
    runtime = VisionRuntime(
        service=VisionAnalysisService(
            clock=lambda: 1000.0,
            analysis_id_factory=lambda: next(ids),
        ),
        description_analyzer=local,
        cloud_vision_analyzer=cloud,
        handoff_accepted=lambda session_id, handoff_id: True,
    )
    actor = _actor()

    local_ready = runtime.prepare(actor, "request-local", "handoff-1", "describe")
    runtime.attach_transfer(actor, local_ready["analysis_id"], "handoff-1", "transfer-local")
    local_result = runtime.analyze(
        actor,
        local_ready["analysis_id"],
        "handoff-1",
        "transfer-local",
        PNG,
        mime_type="image/png",
    )
    assert local_result[-1]["observations"][0]["text"] == "local result"
    assert local_calls == [PNG]
    assert cloud_calls == []

    cloud_ready = runtime.prepare(
        actor,
        "request-cloud",
        "handoff-1",
        "describe",
        "cloud",
    )
    runtime.attach_transfer(actor, cloud_ready["analysis_id"], "handoff-1", "transfer-cloud")
    cloud_result = runtime.analyze(
        actor,
        cloud_ready["analysis_id"],
        "handoff-1",
        "transfer-cloud",
        PNG,
        mime_type="image/png",
    )
    assert cloud_result[-1]["observations"][0]["text"] == "cloud result"
    assert cloud_calls == [PNG]


@dataclass
class _Response:
    body: bytes
    status: int = 200

    def getheader(self, name: str):
        return str(len(self.body)) if name == "Content-Length" else None

    def read(self, size: int) -> bytes:
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


class _Connection:
    response: _Response
    requests: list[tuple] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def request(self, *args, **kwargs) -> None:
        self.requests.append((args, kwargs))

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        return None


def test_production_runner_bounds_and_decodes_one_json_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Connection.response = _Response(
        b'{"choices":[{"message":{"content":"ok"}}]}'
    )
    _Connection.requests.clear()
    monkeypatch.setattr("core.vision.cloud.http.client.HTTPConnection", _Connection)
    runner = BoundedGatewayRequestRunner()
    result = runner(_config(), {"model": "provider/vision"}, timeout=30.0)
    assert result == {"choices": [{"message": {"content": "ok"}}]}
    assert len(_Connection.requests) == 1
    _args, kwargs = _Connection.requests[0]
    assert kwargs["headers"]["Authorization"] == "Bearer private-local-gateway-key"
