"""Private, lazy composition for the Phase 4 pairing runtime."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from pathlib import Path

from core.pairing.challenge_store import PairingChallengeStore
from core.pairing.device_store import DeviceSessionStore
from core.pairing.runtime import ChallengeDisplaySink, PairingRuntime
from core.pairing.service import PairingService
from core.runtime_paths import hikari_home


PAIRING_DB_NAME = "device-sessions.db"


class PairingBootstrapError(RuntimeError):
    """Fixed safe bootstrap failure without paths or exception details."""

    def __init__(self) -> None:
        super().__init__("pairing bootstrap failed")

    def __repr__(self) -> str:
        return "PairingBootstrapError()"


def _production_challenge_id() -> str:
    """Return a cryptographically random canonical challenge identifier."""
    return f"challenge-{secrets.token_hex(16)}"


def _production_device_id() -> str:
    """Return a cryptographically random canonical device identifier."""
    return f"device-{secrets.token_hex(16)}"


def _production_secret_code() -> str:
    """Return a cryptographically random six-character uppercase hex code."""
    return secrets.token_hex(3).upper()


def _production_digest_key() -> bytes:
    """Return a cryptographically random digest key that is never persisted."""
    return secrets.token_bytes(32)


def _default_display_sink(code: str) -> None:
    """Local-only challenge display for production terminal visibility."""
    print(f"[PAIRING] Enter this code on the connecting device: {code}", flush=True)


def pairing_db_path() -> Path:
    """Resolve the device-session database beneath private HIKARI runtime state."""
    return (hikari_home() / "pairing" / PAIRING_DB_NAME).resolve()


def create_pairing_runtime(
    *,
    db_path: Path | str | None = None,
    clock: Callable[[], float] | None = None,
    challenge_id_factory: Callable[[], str] | None = None,
    device_id_factory: Callable[[], str] | None = None,
    secret_code_factory: Callable[[], str] | None = None,
    digest_key: bytes | None = None,
    display_sink: ChallengeDisplaySink | None = None,
) -> PairingRuntime:
    """Construct challenge store, device store, service, and pairing runtime.

    Construction is explicit and creates only the device-session database when
    invoked. Importing this module has no filesystem side effects. Failures
    raise ``PairingBootstrapError`` without paths or exception text.
    """
    try:
        resolved_db = (
            pairing_db_path()
            if db_path is None
            else Path(db_path).expanduser().resolve()
        )
        clock_fn = clock or time.time
        challenge_store = PairingChallengeStore(
            clock=clock_fn,
            challenge_id_factory=challenge_id_factory or _production_challenge_id,
            secret_code_factory=secret_code_factory or _production_secret_code,
            digest_key=digest_key if digest_key is not None else _production_digest_key(),
        )
        device_store = DeviceSessionStore(
            resolved_db,
            clock=clock_fn,
            device_id_factory=device_id_factory or _production_device_id,
        )
        service = PairingService(
            challenge_store=challenge_store,
            device_store=device_store,
        )
        return PairingRuntime(
            service,
            clock_fn,
            display_sink or _default_display_sink,
        )
    except PairingBootstrapError:
        raise
    except Exception:
        raise PairingBootstrapError() from None
