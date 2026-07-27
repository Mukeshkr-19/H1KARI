"""Tests for the live Home Assistant HTTP transport backend.

No real network requests are made; tests validate parsing, DNS rebinding
protection, redirect denial, credential redaction, and the injected connection
factory contract.
"""

from __future__ import annotations

import http.client
import math
import socket
import time

import pytest

from core.action_policy import Actor, ActorContext
from core.phase6_adapters import HomeAssistantAdapter, HomeAssistantAdapterConfig
from core.phase6_adapters.home_assistant import (
    HomeAssistantAdapterOutcome,
    HomeAssistantAdapterReason,
    HomeAssistantTransportContract,
    HomeAssistantTransportEvidence,
    HomeAssistantTransportRequest,
)
from core.phase6_ecosystem.home_assistant import (
    HomeAssistantCapabilityManifest,
    HomeAssistantConfirmation,
    HomeAssistantEntityRef,
    HomeAssistantServiceRef,
)
from core.phase6_live.home_assistant import (
    LiveHomeAssistantTransport,
    LiveHomeAssistantTransportConfig,
    LiveHomeAssistantTransportError,
)


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _FakeId:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"id{self.n}"


class _RecordingAuditor:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, **record):
        self.records.append(record)


def _manifest() -> HomeAssistantCapabilityManifest:
    return HomeAssistantCapabilityManifest(
        allowed_domains=frozenset({"light"}),
        allowed_entities=frozenset({"light.living_room"}),
        allowed_services=frozenset({"turn_on", "get_state"}),
        sensitive_domains=frozenset({"lock"}),
        sensitive_entities=frozenset({"light.living_room"}),
        read_only_services=frozenset({"get_state"}),
    )


def test_disabled_transport_has_no_state() -> None:
    transport = LiveHomeAssistantTransport()
    assert transport.is_enabled is False
    with pytest.raises(LiveHomeAssistantTransportError):
        transport.execute_request(None)  # type: ignore[arg-type]


def test_config_rejects_wildcards() -> None:
    with pytest.raises(ValueError):
        LiveHomeAssistantTransportConfig(
            base_url="https://*.*:8123", credential_provider=lambda: "token"
        )


def test_config_rejects_userinfo() -> None:
    with pytest.raises(ValueError):
        LiveHomeAssistantTransportConfig(
            base_url="https://user:pass@hass.local:8123",
            credential_provider=lambda: "token",
        )


def test_config_rejects_query_and_fragment() -> None:
    with pytest.raises(ValueError):
        LiveHomeAssistantTransportConfig(
            base_url="https://hass.local:8123?x=1", credential_provider=lambda: "token"
        )
    with pytest.raises(ValueError):
        LiveHomeAssistantTransportConfig(
            base_url="https://hass.local:8123#frag", credential_provider=lambda: "token"
        )


def test_config_requires_credential_provider_callable() -> None:
    with pytest.raises(ValueError):
        LiveHomeAssistantTransportConfig(base_url="https://hass.local:8123", credential_provider="bad")  # type: ignore[arg-type]


def test_http_loopback_allowed_when_configured() -> None:
    config = LiveHomeAssistantTransportConfig(
        base_url="http://127.0.0.1:8123",
        credential_provider=lambda: "token",
        allowed_schemes=frozenset({"http", "https"}),
        allow_loopback_http=True,
    )
    assert config.allow_loopback_http is True


def test_http_loopback_rejected_without_flag() -> None:
    with pytest.raises(ValueError):
        LiveHomeAssistantTransportConfig(
            base_url="http://127.0.0.1:8123",
            credential_provider=lambda: "token",
            allowed_schemes=frozenset({"https"}),
            allow_loopback_http=False,
        )


def test_config_hardens_numeric_bounds() -> None:
    with pytest.raises(ValueError):
        LiveHomeAssistantTransportConfig(
            base_url="https://hass.local:8123",
            credential_provider=lambda: "token",
            connect_timeout_seconds=0,
        )
    with pytest.raises(ValueError):
        LiveHomeAssistantTransportConfig(
            base_url="https://hass.local:8123",
            credential_provider=lambda: "token",
            max_response_bytes=0,
        )


def test_repr_does_not_leak_url_or_token() -> None:
    transport = LiveHomeAssistantTransport(
        config=LiveHomeAssistantTransportConfig(
            base_url="https://hass.local:8123", credential_provider=lambda: "secret"
        )
    )
    assert "hass.local" not in repr(transport)
    assert "secret" not in repr(transport)
    assert "LiveHomeAssistantTransport()" in repr(transport)


def test_malformed_port_rejected() -> None:
    with pytest.raises(ValueError):
        LiveHomeAssistantTransportConfig(
            base_url="https://hass.local:abc",
            credential_provider=lambda: "token",
        )


def test_request_validation_denies_mismatched_base_url() -> None:
    config = LiveHomeAssistantTransportConfig(
        base_url="https://hass.local:8123", credential_provider=lambda: "token"
    )
    transport = LiveHomeAssistantTransport(config=config)
    req = HomeAssistantTransportRequest(
        base_url="https://evil.com:8123",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "turn_on"),
        service_data={},
        proposal_id="p1",
        nonce="n1",
        idempotency_key="i1",
        deadline=time.monotonic() + 10,
        max_response_bytes=1024,
    )
    with pytest.raises(LiveHomeAssistantTransportError):
        transport.execute_request(req)


def test_request_validation_denies_unauthorized_scheme() -> None:
    config = LiveHomeAssistantTransportConfig(
        base_url="https://hass.local:8123",
        credential_provider=lambda: "token",
        allowed_schemes=frozenset({"https"}),
    )
    transport = LiveHomeAssistantTransport(config=config)
    req = HomeAssistantTransportRequest(
        base_url="http://127.0.0.1:8123",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "turn_on"),
        service_data={},
        proposal_id="p1",
        nonce="n1",
        idempotency_key="i1",
        deadline=time.monotonic() + 10,
        max_response_bytes=1024,
    )
    with pytest.raises(LiveHomeAssistantTransportError):
        transport.execute_request(req)


def test_injected_connection_factory_called_once() -> None:
    calls: list[tuple[str, int, bool, float]] = []

    class _FakeResponse:
        status = 200

        def __init__(self) -> None:
            self._body = b'{"entity_id":"light.living_room"}'
            self._consumed = False

        def read(self, n: int) -> bytes:
            if self._consumed:
                return b""
            self._consumed = True
            return self._body

    class _FakeConnection:
        def __init__(self, ip: str, port: int, use_tls: bool, timeout: float) -> None:
            self._ip = ip
            self._port = port
            self._use_tls = use_tls
            self._timeout = timeout
            self._headers: list[tuple[str, str]] = []

        def connect(self) -> None:
            pass

        def putrequest(self, method: str, path: str, *, skip_host: bool = False) -> None:
            self._method = method
            self._path = path

        def putheader(self, key: str, value: str) -> None:
            self._headers.append((key, value))

        def endheaders(self, body: bytes | None = None) -> None:
            pass

        def getresponse(self) -> _FakeResponse:
            return _FakeResponse()

        def close(self) -> None:
            pass

    def factory(ip: str, port: int, use_tls: bool, timeout: float) -> http.client.HTTPConnection:
        calls.append((ip, port, use_tls, timeout))
        return _FakeConnection(ip, port, use_tls, timeout)

    config = LiveHomeAssistantTransportConfig(
        base_url="https://127.0.0.1:8123",
        credential_provider=lambda: "token",
        connection_factory=factory,
    )
    transport = LiveHomeAssistantTransport(config=config)
    req = HomeAssistantTransportRequest(
        base_url="https://127.0.0.1:8123",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "turn_on"),
        service_data={},
        proposal_id="p1",
        nonce="n1",
        idempotency_key="i1",
        deadline=time.monotonic() + 10,
        max_response_bytes=1024,
    )
    evidence = transport.execute_request(req)
    assert len(calls) == 1
    ip, port, use_tls, timeout = calls[0]
    assert ip == "127.0.0.1"
    assert port == 8123
    assert use_tls is True
    assert 0 < timeout <= 10.0
    assert evidence.success_category == "ok"
    assert evidence.failure_category is None


def test_credential_provider_not_persisted() -> None:
    captured: list[str] = []

    def provider() -> str:
        captured.append("called")
        return "bearer-token"

    config = LiveHomeAssistantTransportConfig(
        base_url="https://hass.local:8123", credential_provider=provider
    )
    transport = LiveHomeAssistantTransport(config=config)
    # Inspecting __dict__ should not contain the token.
    assert "bearer-token" not in str(transport.__dict__)
    assert captured == []


def test_pinned_https_connects_to_ip_and_sni_hostname() -> None:
    import socket as socket_mod
    import ssl as ssl_mod
    from unittest.mock import patch

    from core.phase6_live.home_assistant import _PinnedHTTPSConnection

    calls: list[tuple[str, object, object]] = []

    class _FakeSocket:
        def settimeout(self, timeout: float) -> None:
            pass

    class _FakeSSLContext:
        def wrap_socket(self, sock, *, server_hostname: str) -> object:
            calls.append(("wrap", server_hostname, "8123"))
            return _FakeSocket()

    def fake_create_connection(addr, timeout=0.0):
        calls.append(("connect", addr[0], addr[1]))
        return _FakeSocket()

    with patch.object(socket_mod, "create_connection", fake_create_connection):
        with patch.object(ssl_mod, "create_default_context", return_value=_FakeSSLContext()):
            conn = _PinnedHTTPSConnection(
                "10.0.0.1",
                8123,
                "hass.local",
                context=_FakeSSLContext(),
                timeout=5.0,
            )
            conn.connect()

    connect = next(c for c in calls if c[0] == "connect")
    wrap = next(c for c in calls if c[0] == "wrap")
    assert connect[1] == "10.0.0.1"
    assert connect[2] == 8123
    assert wrap[1] == "hass.local"


def test_service_data_rejects_nested_control_chars() -> None:
    config = LiveHomeAssistantTransportConfig(
        base_url="https://hass.local:8123", credential_provider=lambda: "token"
    )
    transport = LiveHomeAssistantTransport(config=config)
    req = HomeAssistantTransportRequest(
        base_url="https://hass.local:8123",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "turn_on"),
        service_data={"bad\x01key": "value"},
        proposal_id="p1",
        nonce="n1",
        idempotency_key="i1",
        deadline=time.monotonic() + 10,
        max_response_bytes=1024,
    )
    with pytest.raises(LiveHomeAssistantTransportError):
        transport.execute_request(req)


def test_token_rejects_bidi_and_format_chars() -> None:
    transport = LiveHomeAssistantTransport(
        config=LiveHomeAssistantTransportConfig(
            base_url="https://hass.local:8123",
            credential_provider=lambda: "\u202etoken",
        )
    )
    req = HomeAssistantTransportRequest(
        base_url="https://hass.local:8123",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "turn_on"),
        service_data={},
        proposal_id="p1",
        nonce="n1",
        idempotency_key="i1",
        deadline=time.monotonic() + 10,
        max_response_bytes=1024,
    )
    with pytest.raises(LiveHomeAssistantTransportError):
        transport.execute_request(req)


def test_cancellation_checked_at_boundaries() -> None:
    cancelled_flag = {"value": True}

    class _FakeConnection:
        def __init__(self, ip: str, port: int, use_tls: bool, timeout: float) -> None:
            pass

        def connect(self) -> None:
            raise AssertionError("connect should not be called after cancellation")

        def putrequest(self, method: str, path: str, *, skip_host: bool = False) -> None:
            pass

        def putheader(self, key: str, value: str) -> None:
            pass

        def endheaders(self, body: bytes | None = None) -> None:
            pass

        def getresponse(self) -> object:
            raise AssertionError("getresponse should not be called after cancellation")

        def close(self) -> None:
            pass

    def factory(ip: str, port: int, use_tls: bool, timeout: float) -> http.client.HTTPConnection:
        return _FakeConnection(ip, port, use_tls, timeout)

    config = LiveHomeAssistantTransportConfig(
        base_url="https://hass.local:8123",
        credential_provider=lambda: "token",
        connection_factory=factory,
        is_cancelled=lambda: cancelled_flag["value"],
    )
    transport = LiveHomeAssistantTransport(config=config)
    req = HomeAssistantTransportRequest(
        base_url="https://hass.local:8123",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "turn_on"),
        service_data={},
        proposal_id="p1",
        nonce="n1",
        idempotency_key="i1",
        deadline=time.monotonic() + 10,
        max_response_bytes=1024,
    )
    with pytest.raises(LiveHomeAssistantTransportError) as exc:
        transport.execute_request(req)
    assert exc.value.reason == HomeAssistantAdapterReason.TRANSPORT_FAILURE


def test_pinned_https_connection_connects_to_ip_with_sni_and_hostname_check() -> None:
    import socket as socket_mod
    import ssl as ssl_mod
    from unittest.mock import patch

    from core.phase6_live.home_assistant import _PinnedHTTPSConnection

    captured: dict[str, object] = {}
    fake_sock = object()

    def fake_create_connection(addr, timeout=0.0, *args, **kwargs):
        captured["addr"] = addr
        captured["timeout"] = timeout
        return fake_sock

    class _FakeSSLContext:
        check_hostname = True
        verify_mode = ssl_mod.CERT_REQUIRED

        def wrap_socket(self, sock, *, server_hostname: str):
            captured["wrap_sock"] = sock
            captured["server_hostname"] = server_hostname
            captured["check_hostname"] = self.check_hostname
            captured["verify_mode"] = self.verify_mode
            return object()

    context = _FakeSSLContext()
    with patch.object(socket_mod, "create_connection", side_effect=fake_create_connection):
        conn = _PinnedHTTPSConnection(
            "10.0.0.1", 8123, "hass.local", context=context, timeout=5.0
        )
        conn.connect()

    assert captured["addr"] == ("10.0.0.1", 8123)
    assert captured["server_hostname"] == "hass.local"
    assert captured["wrap_sock"] is fake_sock
    assert captured["check_hostname"] is True
    assert captured["verify_mode"] == ssl_mod.CERT_REQUIRED


def test_pinned_https_connection_closes_raw_socket_on_wrap_failure() -> None:
    import socket as socket_mod
    from unittest.mock import patch

    from core.phase6_live.home_assistant import _PinnedHTTPSConnection

    class _ClosedSocket:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    raw = _ClosedSocket()

    def fake_create_connection(addr, timeout=0.0, *args, **kwargs):
        return raw

    class _BoomContext:
        check_hostname = True
        verify_mode = 2

        def wrap_socket(self, sock, *, server_hostname: str):
            raise RuntimeError("boom")

    with patch.object(socket_mod, "create_connection", side_effect=fake_create_connection):
        conn = _PinnedHTTPSConnection(
            "10.0.0.1", 8123, "hass.local", context=_BoomContext(), timeout=5.0
        )
        with pytest.raises(RuntimeError):
            conn.connect()
    assert raw.closed is True


def test_clock_validation_rejects_invalid_clock_outputs() -> None:
    for bad in (float("nan"), float("inf"), -1, True, "bad"):
        with pytest.raises(ValueError):
            LiveHomeAssistantTransportConfig(
                base_url="https://hass.local:8123",
                credential_provider=lambda: "token",
                clock=lambda: bad,  # type: ignore[arg-type]
            )


def test_dns_bounds_and_mixed_loopback_rejected() -> None:
    import socket as socket_mod
    from unittest.mock import patch

    transport = LiveHomeAssistantTransport(
        config=LiveHomeAssistantTransportConfig(
            base_url="https://hass.local:8123",
            credential_provider=lambda: "token",
        )
    )

    def fake_getaddrinfo(host, port, *args, **kwargs):
        # Return a bounded mix of loopback and non-loopback results.
        return [
            (socket_mod.AF_INET, socket_mod.SOCK_STREAM, 0, "", ("127.0.0.1", port)),
            (socket_mod.AF_INET, socket_mod.SOCK_STREAM, 0, "", ("10.0.0.1", port)),
        ]

    with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
        with pytest.raises(LiveHomeAssistantTransportError) as exc:
            transport._resolve_host("hass.local", 8123)
        assert exc.value.reason == HomeAssistantAdapterReason.HOST_NOT_ALLOWED


def test_dns_pinned_first_result() -> None:
    import socket as socket_mod
    from unittest.mock import patch

    transport = LiveHomeAssistantTransport(
        config=LiveHomeAssistantTransportConfig(
            base_url="https://hass.local:8123",
            credential_provider=lambda: "token",
        )
    )

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket_mod.AF_INET, socket_mod.SOCK_STREAM, 0, "", ("10.0.0.1", port)),
            (socket_mod.AF_INET, socket_mod.SOCK_STREAM, 0, "", ("10.0.0.2", port)),
        ]

    with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
        ip, is_loopback = transport._resolve_host("hass.local", 8123)
        assert ip == "10.0.0.1"
        assert is_loopback is False


def test_full_adapter_composition_with_fake_transport() -> None:
    """HomeAssistantAdapter.prepare -> confirm_and_execute with injected transport."""
    from core.phase6_adapters.home_assistant import HomeAssistantAdapter as Adapter

    manifest = _manifest()
    actor = ActorContext(actor=Actor.OWNER, actor_id="owner.1", session_id="s1")

    class _FakeTransport(HomeAssistantTransportContract):
        def __init__(self) -> None:
            self.request: HomeAssistantTransportRequest | None = None

        def execute_request(self, request: HomeAssistantTransportRequest) -> HomeAssistantTransportEvidence:
            self.request = request
            return HomeAssistantTransportEvidence(
                observation_id=request.idempotency_key,
                proposal_id=request.proposal_id,
                final_url=request.base_url + "/api/services/light/turn_on",
                resolved_host="hass.local",
                response_byte_count=0,
                elapsed_seconds=0.0,
                success_category="ok",
                failure_category=None,
                idempotency_contract_proven=False,
                observed_at=time.time(),
            )

    fake = _FakeTransport()
    adapter = Adapter(
        config=HomeAssistantAdapterConfig(
            base_url="https://hass.local:8123",
            manifest=manifest,
        ),
        clock=_FakeClock(100.0),
        id_factory=_FakeId(),
        auditor=_RecordingAuditor(),
        transport=fake,
    )
    prepare = adapter.prepare(
        proposal_id="p1",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "turn_on"),
        service_data={},
        actor_context=actor,
        nonce="n1",
    )
    assert prepare.outcome is HomeAssistantAdapterOutcome.REQUIRE_CONFIRMATION
    confirm = adapter.confirm_and_execute(
        proposal=prepare.proposal,
        confirmation=HomeAssistantConfirmation(
            proposal_id=prepare.proposal.proposal_id,
            nonce="n1",
            confirmed_by_actor_id="owner.1",
            confirmed_at=100.0,
        ),
        actor_context=actor,
    )
    assert confirm.outcome is HomeAssistantAdapterOutcome.ALLOW
    assert fake.request is not None
    assert fake.request.proposal_id == "p1"



def test_conflicting_entity_id_rejected() -> None:
    req = HomeAssistantTransportRequest(
        base_url="https://hass.local:8123",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "turn_on"),
        service_data={"entity_id": "light.other"},
        proposal_id="p1",
        nonce="n1",
        idempotency_key="k1",
        deadline=time.monotonic() + 10,
        max_response_bytes=1024,
    )
    with pytest.raises(LiveHomeAssistantTransportError):
        LiveHomeAssistantTransport._build_body(req)


def test_request_local_clock_does_not_couple_requests() -> None:
    """A completed request must not poison a later request with a fresh origin."""
    calls = {"n": 0}

    def clock():
        # Each "request" uses an independent synthetic origin: 0, then 0 again.
        calls["n"] += 1
        return 0.0 if calls["n"] in (1, 2, 3, 4) else 1.0

    # Directly exercise _now with separate guards
    from core.phase6_live.home_assistant import (
        LiveHomeAssistantTransport,
        LiveHomeAssistantTransportConfig,
    )
    config = LiveHomeAssistantTransportConfig(
        base_url="https://127.0.0.1:8123",
        credential_provider=lambda: "tok",
        allowed_schemes=frozenset({"https"}),
        allow_loopback_http=False,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        max_response_bytes=1024,
        clock=clock,
    )
    transport = LiveHomeAssistantTransport(config=config)
    g1: list = [None]
    assert transport._now(request_guard=g1) == 0.0
    g2: list = [None]
    # Fresh guard allows a fresh origin even if instance previously saw time.
    assert transport._now(request_guard=g2) == 0.0


def test_expired_deadline_blocks_dns_and_credential_access(monkeypatch) -> None:
    calls = {"dns": 0, "credential": 0}

    def credential_provider() -> str:
        calls["credential"] += 1
        return "token"

    def fake_getaddrinfo(*args, **kwargs):
        calls["dns"] += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 8123))]

    transport = LiveHomeAssistantTransport(
        config=LiveHomeAssistantTransportConfig(
            base_url="https://hass.local:8123",
            credential_provider=credential_provider,
            clock=lambda: 10.0,
        )
    )
    request = HomeAssistantTransportRequest(
        base_url="https://hass.local:8123",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "turn_on"),
        service_data={},
        proposal_id="p1",
        nonce="n1",
        idempotency_key="k1",
        deadline=10.0,
        max_response_bytes=1024,
    )
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(LiveHomeAssistantTransportError) as exc:
        transport.execute_request(request)

    assert exc.value.reason == HomeAssistantAdapterReason.TIMEOUT_EXCEEDED
    assert calls == {"dns": 0, "credential": 0}


def test_malformed_dns_ip_rejected(monkeypatch) -> None:
    from core.phase6_live.home_assistant import (
        LiveHomeAssistantTransport,
        LiveHomeAssistantTransportConfig,
        LiveHomeAssistantTransportError,
    )
    config = LiveHomeAssistantTransportConfig(
        base_url="https://hass.local:8123",
        credential_provider=lambda: "tok",
        allowed_schemes=frozenset({"https"}),
        allow_loopback_http=False,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        max_response_bytes=1024,
        clock=time.monotonic,
    )
    transport = LiveHomeAssistantTransport(config=config)

    def fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("not-an-ip", 8123))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(LiveHomeAssistantTransportError):
        transport._resolve_host("hass.local", 8123)
