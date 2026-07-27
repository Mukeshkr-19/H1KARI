"""Live, optional Home Assistant HTTP transport backend.

Uses only Python standard library (``http.client``, ``ssl``, ``socket``).
Disabled by default: the class does nothing until instantiated with an explicit
configuration.  No real network call is made during import or construction.

Safety guarantees:
- HTTPS/WSS by default; HTTP only for explicitly configured loopback addresses.
- Exact configured scheme, host and port enforced.
- Redirects denied.
- Final URL and resolved host verified against configuration.
- DNS rebinding mitigated by resolving once and connecting by IP with SNI.
- Strict connect/read/total deadlines.
- Bounded response bytes.
- Credentials supplied by an injected per-call provider; no token persistence.
- No credentials in repr, logs, or evidence.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import socket
import ssl
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, FrozenSet, Mapping, Optional, Protocol, Tuple
from urllib.parse import urlparse

from core.phase6_adapters.home_assistant import (
    HomeAssistantAdapterReason,
    HomeAssistantTransportContract,
    HomeAssistantTransportEvidence,
    HomeAssistantTransportRequest,
)


class HTTPConnectionFactory(Protocol):
    """Protocol for an injected connection factory used in tests."""

    def __call__(
        self,
        ip: str,
        port: int,
        use_tls: bool,
        timeout: float,
    ) -> http.client.HTTPConnection:
        ...


class LiveHomeAssistantTransportError(Exception):
    """Fixed, content-free transport failure."""

    def __init__(self, reason: HomeAssistantAdapterReason) -> None:
        self.reason = reason
        super().__init__("LiveHomeAssistantTransportError")

    def __repr__(self) -> str:
        return "LiveHomeAssistantTransportError()"


_MAX_REQUEST_BODY_BYTES = 65_536
_MAX_DNS_RESULTS = 10
_MAX_TOKEN_LENGTH = 4_096
_MAX_KEY_LENGTH = 256
_MAX_STRING_LENGTH = 65_536
_MAX_NESTING_DEPTH = 5
_MAX_SERVICE_DATA_KEYS = 1_024
_MAX_LIST_LENGTH = 1_024


def _is_control_or_format(char: str) -> bool:
    if ord(char) < 32 or ord(char) == 127:
        return True
    if unicodedata.category(char) == "Cf":
        return True
    bidi = unicodedata.bidirectional(char)
    if bidi in {"RLE", "LRE", "RLO", "LRO", "PDF", "RLM", "LRM", "ALM", "LRI", "RLI", "FSI", "PDI"}:
        return True
    return False


def _validate_token(token: object) -> str:
    if not isinstance(token, str):
        raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.UNAUTHORIZED_ACTOR)
    if not token:
        raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.UNAUTHORIZED_ACTOR)
    if len(token) > _MAX_TOKEN_LENGTH:
        raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.UNAUTHORIZED_ACTOR)
    if any(_is_control_or_format(ch) for ch in token):
        raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.UNAUTHORIZED_ACTOR)
    return token


def _validate_service_data(data: object, *, depth: int = 0, keys_seen: Optional[set[str]] = None) -> None:
    """Recursively validate service_data: JSON-safe, bounded, no control/bidi keys."""
    if keys_seen is None:
        keys_seen = set()
    if depth > _MAX_NESTING_DEPTH:
        raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
    if isinstance(data, dict) or isinstance(data, Mapping):
        if len(data) > _MAX_SERVICE_DATA_KEYS:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
        for key, value in data.items():
            if not isinstance(key, str):
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
            if len(key) > _MAX_KEY_LENGTH:
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
            if any(_is_control_or_format(ch) for ch in key):
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
            _validate_service_data(value, depth=depth + 1, keys_seen=keys_seen)
            keys_seen.add(key)
        return
    if isinstance(data, list):
        if len(data) > _MAX_LIST_LENGTH:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
        for item in data:
            _validate_service_data(item, depth=depth + 1, keys_seen=keys_seen)
        return
    if isinstance(data, str):
        if len(data) > _MAX_STRING_LENGTH:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
        if any(_is_control_or_format(ch) for ch in data):
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
        return
    if isinstance(data, bool) or data is None or isinstance(data, (int, float)):
        if isinstance(data, float) and (not math.isfinite(data) or isinstance(data, bool)):
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
        return
    raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that connects to a pinned IP but uses the hostname for SNI."""

    def __init__(
        self,
        ip: str,
        port: int,
        hostname: str,
        *,
        context: ssl.SSLContext,
        timeout: float,
    ) -> None:
        super().__init__(ip, port, timeout=timeout, context=context)
        self._pinned_ip = ip
        self._hostname = hostname

    def connect(self) -> None:
        raw_sock = self._create_connection(
            (self._pinned_ip, self.port), self.timeout
        )
        try:
            self.sock = raw_sock
            if self._tunnel_host:
                self._tunnel()  # type: ignore[attr-defined]
            self.sock = self._context.wrap_socket(  # type: ignore[union-attr]
                self.sock, server_hostname=self._hostname
            )
        except Exception:
            raw_sock.close()
            raise


@dataclass(frozen=True)
class LiveHomeAssistantTransportConfig:
    """Explicit configuration enabling the live HA transport.

    - base_url: exact trusted base endpoint
    - credential_provider: callable returning the current auth token/header value
    - allowed_schemes: allowed URL schemes (only http/https for REST)
    - allow_loopback_http: if True, permit http:// on exact loopback addresses
    - connect_timeout_seconds: TCP connect timeout
    - read_timeout_seconds: response read timeout
    - max_response_bytes: hard cap on response body bytes
    - clock: monotonic clock for deadline timing (default time.monotonic)
    - is_cancelled: optional callable checked before each network boundary
    """

    base_url: str
    credential_provider: Callable[[], str]
    allowed_schemes: FrozenSet[str] = frozenset({"https"})
    allow_loopback_http: bool = False
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 10.0
    max_response_bytes: int = 1_048_576
    connection_factory: Optional[HTTPConnectionFactory] = None
    clock: Callable[[], float] = time.monotonic
    is_cancelled: Optional[Callable[[], bool]] = None

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str) or not self.base_url:
            raise ValueError("base_url required")
        if "*" in self.base_url or "@" in self.base_url:
            raise ValueError("wildcards or userinfo not allowed in base_url")
        parsed = urlparse(self.base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("base_url must have scheme and host")
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("only http/https are supported for REST")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("userinfo not allowed")
        if parsed.fragment:
            raise ValueError("fragment not allowed")
        if parsed.query:
            raise ValueError("query not allowed in base_url")
        if not isinstance(self.allowed_schemes, frozenset) or not self.allowed_schemes:
            raise ValueError("allowed_schemes must be non-empty frozenset")
        for scheme in self.allowed_schemes:
            if scheme not in {"http", "https"}:
                raise ValueError("disallowed scheme")
        if parsed.scheme not in self.allowed_schemes:
            raise ValueError("scheme not allowed")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("malformed port") from exc
        if port is not None and (port <= 0 or port > 65535):
            raise ValueError("invalid port")
        if not callable(self.credential_provider):
            raise ValueError("credential_provider must be callable")
        if not isinstance(self.allow_loopback_http, bool):
            raise ValueError("allow_loopback_http must be boolean")
        for name, value in (
            ("connect_timeout_seconds", self.connect_timeout_seconds),
            ("read_timeout_seconds", self.read_timeout_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 300:
                raise ValueError(f"invalid {name}")
        if (
            not isinstance(self.max_response_bytes, int)
            or isinstance(self.max_response_bytes, bool)
            or not 1 <= self.max_response_bytes <= 100_000_000
        ):
            raise ValueError("invalid max_response_bytes")
        if self.connection_factory is not None and not callable(self.connection_factory):
            raise ValueError("connection_factory must be callable")
        if not callable(self.clock):
            raise ValueError("clock must be callable")
        sample = self.clock()
        if isinstance(sample, bool) or not isinstance(sample, (int, float)) or not math.isfinite(float(sample)) or float(sample) < 0.0:
            raise ValueError("clock must return a finite non-negative number")
        if self.is_cancelled is not None and not callable(self.is_cancelled):
            raise ValueError("is_cancelled must be callable")

    def __repr__(self) -> str:
        return "LiveHomeAssistantTransportConfig()"


class LiveHomeAssistantTransport(HomeAssistantTransportContract):
    """Production-grade, disabled-by-default Home Assistant HTTP transport."""

    def __init__(self, *, config: Optional[LiveHomeAssistantTransportConfig] = None) -> None:
        self._config = config
        self._disabled = config is None

    @property
    def is_enabled(self) -> bool:
        return not self._disabled

    def _require_enabled(self) -> LiveHomeAssistantTransportConfig:
        if self._disabled or self._config is None:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.DISABLED)
        return self._config

    def _check_cancelled(self) -> None:
        config = self._config
        if config is not None and config.is_cancelled is not None and config.is_cancelled():
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TRANSPORT_FAILURE)

    @staticmethod
    def _is_loopback(host: str) -> bool:
        """Return True if host resolves only to loopback addresses."""
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        for _family, _socktype, _proto, _canonname, sockaddr in infos:
            ip = sockaddr[0]
            if not ip.startswith("127.") and ip != "::1" and ip != "0:0:0:0:0:0:0:1":
                return False
        return bool(infos)

    def _validate_request(self, request: HomeAssistantTransportRequest) -> Tuple[bool, HomeAssistantAdapterReason]:
        """Validate that the prepared request matches the configured base URL."""
        config = self._config
        assert config is not None
        if not isinstance(request, HomeAssistantTransportRequest):
            return False, HomeAssistantAdapterReason.INVALID_URL
        if not request.base_url == config.base_url:
            return False, HomeAssistantAdapterReason.HOST_NOT_ALLOWED
        try:
            parsed = urlparse(request.base_url)
        except Exception:
            return False, HomeAssistantAdapterReason.INVALID_URL
        if parsed.scheme not in config.allowed_schemes:
            if parsed.scheme == "http" and config.allow_loopback_http:
                host = parsed.hostname or ""
                if not self._is_loopback(host):
                    return False, HomeAssistantAdapterReason.HOST_NOT_ALLOWED
            else:
                return False, HomeAssistantAdapterReason.SCHEME_NOT_ALLOWED
        if parsed.username is not None or parsed.password is not None:
            return False, HomeAssistantAdapterReason.USERINFO_IN_URL
        if parsed.fragment:
            return False, HomeAssistantAdapterReason.FRAGMENT_IN_URL
        try:
            port = parsed.port
        except ValueError:
            return False, HomeAssistantAdapterReason.MALFORMED_PORT
        if port is not None and (port <= 0 or port > 65535):
            return False, HomeAssistantAdapterReason.MALFORMED_PORT
        if "*" in request.base_url:
            return False, HomeAssistantAdapterReason.INVALID_URL
        return True, HomeAssistantAdapterReason.OK

    def _now(self, *, request_guard: Optional[list] = None) -> float:
        """Return a validated, finite, non-negative timestamp from the injected clock.

        Backward movement is enforced per-request via ``request_guard`` so a
        completed request cannot poison a later request that starts from a
        fresh synthetic origin. Production monotonic clocks remain valid.
        """
        if self._config is None:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
        value = self._config.clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
        value = float(value)
        if not math.isfinite(value) or value < 0.0:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
        if request_guard is not None:
            last = request_guard[0] if request_guard else None
            if last is not None and value < last:
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
            if request_guard:
                request_guard[0] = value
        return value

    def _resolve_host(self, hostname: str, port: int) -> Tuple[str, bool]:
        """Resolve hostname to a single IP for connection.

        Returns (ip, is_loopback).  Raises LiveHomeAssistantTransportError on
        resolution failure or ambiguous results.

        DNS results are bounded and validated: no more than ``_MAX_DNS_RESULTS``
        are considered, all returned addresses must agree on the loopback
        classification, and the first result is pinned deterministically.
        """
        self._check_cancelled()
        try:
            infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.DNS_HOST_MISMATCH) from exc
        if not infos:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.DNS_HOST_MISMATCH)
        self._check_cancelled()

        # Reject if the resolver returned more results than the hard maximum.
        if len(infos) > _MAX_DNS_RESULTS:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.DNS_HOST_MISMATCH)

        # Bound the result set and validate every address with ipaddress.
        results: list[tuple[int, str, bool]] = []
        for family, _socktype, _proto, _canonname, sockaddr in infos:
            if not sockaddr or not isinstance(sockaddr[0], str):
                continue
            raw = sockaddr[0]
            if not raw:
                continue
            try:
                addr = ipaddress.ip_address(raw)
            except ValueError:
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.DNS_HOST_MISMATCH)
            if addr.is_unspecified or addr.is_multicast:
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.HOST_NOT_ALLOWED)
            if family not in (socket.AF_INET, socket.AF_INET6):
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.DNS_HOST_MISMATCH)
            results.append((family, str(addr), bool(addr.is_loopback)))

        if not results:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.DNS_HOST_MISMATCH)

        loopback_flags = {is_loop for _family, _ip, is_loop in results}
        if len(loopback_flags) > 1:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.HOST_NOT_ALLOWED)

        # Deterministically pin the first validated result for the entire request.
        _family, ip, is_loopback = results[0]
        self._check_cancelled()
        return ip, is_loopback

    @staticmethod
    def _make_ssl_context() -> ssl.SSLContext:
        """Return a secure SSL context; no custom trust-all."""
        return ssl.create_default_context()

    @staticmethod
    def _build_path(request: HomeAssistantTransportRequest) -> str:
        """Build a canonical Home Assistant service path."""
        domain = request.service_ref.domain
        service = request.service_ref.service
        return f"/api/services/{domain}/{service}"

    @staticmethod
    def _build_body(request: HomeAssistantTransportRequest) -> bytes:
        """Build a bounded JSON body from the authorized request.

        The authorized entity_id always wins. A conflicting caller-supplied
        entity_id in service_data is rejected; it cannot override authorization.
        """
        if request.service_data is not None:
            _validate_service_data(request.service_data)
        data: dict[str, object] = dict(request.service_data) if request.service_data else {}
        authorized = request.entity_ref.entity_id
        if "entity_id" in data and data["entity_id"] != authorized:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_CONFIGURATION)
        data["entity_id"] = authorized
        body = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(body) > _MAX_REQUEST_BODY_BYTES:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.RESPONSE_TOO_LARGE)
        return body

    @staticmethod
    def _set_socket_timeout(conn: http.client.HTTPConnection, timeout: float) -> None:
        if timeout <= 0:
            return
        sock = getattr(conn, "sock", None)
        if sock is not None:
            sock.settimeout(timeout)

    def execute_request(self, request: HomeAssistantTransportRequest) -> HomeAssistantTransportEvidence:
        if self._disabled:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.DISABLED)
        ok, reason = self._validate_request(request)
        if not ok:
            raise LiveHomeAssistantTransportError(reason)

        config = self._require_enabled()
        parsed = urlparse(request.base_url)
        hostname = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        use_tls = parsed.scheme == "https"

        request_guard: list = [None]
        started = self._now(request_guard=request_guard)
        deadline = request.deadline
        if not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or not math.isfinite(deadline):
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.INVALID_URL)
        if started >= deadline:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)

        self._check_cancelled()
        ip, is_loopback = self._resolve_host(hostname, port)
        if self._now(request_guard=request_guard) >= deadline:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)
        service_path = self._build_path(request)

        self._check_cancelled()
        if self._now(request_guard=request_guard) >= deadline:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)
        token = _validate_token(config.credential_provider())
        try:
            headers: dict[str, str] = {
                "Host": hostname,
                "Idempotency-Key": request.idempotency_key,
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "HIKARI-Phase6Live/1.0",
            }
        finally:
            del token

        body = self._build_body(request)
        max_response_bytes = min(config.max_response_bytes, request.max_response_bytes)
        if max_response_bytes <= 0:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.RESPONSE_TOO_LARGE)

        if self._now(request_guard=request_guard) >= deadline:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)
        self._check_cancelled()

        conn: Optional[http.client.HTTPConnection] = None
        try:
            remaining_connect = min(config.connect_timeout_seconds, deadline - self._now(request_guard=request_guard))
            if remaining_connect <= 0:
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)

            self._check_cancelled()
            if config.connection_factory is not None:
                conn = config.connection_factory(ip, port, use_tls, remaining_connect)
            else:
                if use_tls:
                    context = self._make_ssl_context()
                    conn = _PinnedHTTPSConnection(
                        ip,
                        port,
                        hostname,
                        context=context,
                        timeout=remaining_connect,
                    )
                else:
                    if not (config.allow_loopback_http and is_loopback):
                        raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.SCHEME_NOT_ALLOWED)
                    conn = http.client.HTTPConnection(ip, port, timeout=remaining_connect)

            if conn is None:
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TRANSPORT_FAILURE)

            self._check_cancelled()
            conn.connect()

            read_timeout = min(config.read_timeout_seconds, deadline - self._now(request_guard=request_guard))
            if read_timeout <= 0:
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)
            self._set_socket_timeout(conn, read_timeout)

            self._check_cancelled()
            conn.putrequest("POST", service_path, skip_host=True)
            conn.putheader("Host", hostname)
            conn.putheader("Idempotency-Key", request.idempotency_key)
            conn.putheader("Authorization", headers["Authorization"])
            conn.putheader("Content-Type", headers["Content-Type"])
            conn.putheader("Accept", headers["Accept"])
            conn.putheader("User-Agent", headers["User-Agent"])
            conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)

            self._check_cancelled()
            if self._now(request_guard=request_guard) >= deadline:
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)

            # Refresh read timeout before getresponse.
            read_timeout = min(config.read_timeout_seconds, deadline - self._now(request_guard=request_guard))
            if read_timeout <= 0:
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)
            self._set_socket_timeout(conn, read_timeout)

            response = conn.getresponse()

            # Reject redirects explicitly; http.client does not follow them, but a 3xx status is not OK.
            if 300 <= response.status < 400:
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.REDIRECT_TARGET_REJECTED)

            # Stream bounded read, up to cap+1 to detect overflow.
            chunk_size = 8192
            read_buffer = bytearray()
            while len(read_buffer) <= max_response_bytes:
                self._check_cancelled()
                read_timeout = min(config.read_timeout_seconds, deadline - self._now(request_guard=request_guard))
                if read_timeout <= 0:
                    raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)
                self._set_socket_timeout(conn, read_timeout)
                chunk = response.read(min(chunk_size, max_response_bytes - len(read_buffer) + 1))
                if not chunk:
                    break
                read_buffer.extend(chunk)
                if self._now(request_guard=request_guard) >= deadline:
                    raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)

            response_byte_count = len(read_buffer)
            if response_byte_count > max_response_bytes:
                raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.RESPONSE_TOO_LARGE)

            elapsed = self._now(request_guard=request_guard) - started
            final_url = f"{parsed.scheme}://{hostname}:{port}{service_path}"
            success = 200 <= response.status < 300
            success_category = "ok" if success else None
            failure_category: Optional[str] = None
            if not success:
                if response.status in (401, 403):
                    failure_category = "rejected"
                elif response.status >= 500:
                    failure_category = "unavailable"
                else:
                    failure_category = "rejected"

            return HomeAssistantTransportEvidence(
                observation_id=request.idempotency_key,
                proposal_id=request.proposal_id,
                final_url=final_url,
                resolved_host=hostname,
                response_byte_count=response_byte_count,
                elapsed_seconds=elapsed,
                success_category=success_category,
                failure_category=failure_category,
                idempotency_contract_proven=False,
                observed_at=self._now(request_guard=request_guard),
            )
        except TimeoutError as exc:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TIMEOUT_EXCEEDED) from exc
        except (socket.timeout, socket.error) as exc:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TRANSPORT_FAILURE) from exc
        except LiveHomeAssistantTransportError:
            raise
        except Exception as exc:
            raise LiveHomeAssistantTransportError(HomeAssistantAdapterReason.TRANSPORT_FAILURE) from exc
        finally:
            headers.clear()
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def __repr__(self) -> str:
        return "LiveHomeAssistantTransport()"
