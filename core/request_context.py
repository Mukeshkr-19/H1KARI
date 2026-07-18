"""Immutable request-scoped actor context for WebSocket and voice turns.

Actor identity is derived only from server-observed state:
- transport peer (loopback vs remote)
- server-created connection/session token
- server-owned pairing state

Client JSON must never select owner/guest/system role, actor ID, scope, or
session ID.  This module enforces that derivation.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Optional

from core.action_policy import Actor, ActorContext


class ActorSource:
    """Canonical source strings for actor derivation."""

    LOCAL = "local"
    WEBSOCKET = "websocket"
    VOICE_REMOTE = "voice_remote"
    DEVICE = "device"


@dataclass(frozen=True)
class RequestContext:
    """Immutable request-scoped context for a single turn.

    Attributes:
        actor_context: server-derived actor identity
        connection_token: opaque server-created connection token
        session_id: opaque server-created session identifier
        is_loopback: whether the transport peer is loopback
        is_paired: whether the connection completed the pairing handshake
    """

    actor_context: ActorContext
    connection_token: str
    session_id: str
    is_loopback: bool
    is_paired: bool


def _new_token() -> str:
    return secrets.token_hex(16)


def derive_owner_context(*, source: str, session_id: Optional[str] = None) -> RequestContext:
    """Return a local-owner request context for trusted local entrypoints."""
    resolved_session_id = session_id or _new_token()
    return RequestContext(
        actor_context=ActorContext(
            actor_id="local-owner",
            actor=Actor.OWNER,
            session_id=resolved_session_id,
            source=source,
        ),
        connection_token=_new_token(),
        session_id=resolved_session_id,
        is_loopback=True,
        is_paired=True,
    )


def derive_actor_from_transport(
    *,
    source: str,
    connection_token: str,
    is_loopback: bool,
    is_paired: bool,
    session_id: Optional[str] = None,
) -> RequestContext:
    """Derive a request context from server-observed transport state only.

    Loopback connections may receive owner scope; non-loopback connections are
    always guest/session scoped, even after pairing.  This is the single entry
    point used by the WebSocket server.  It never consults client JSON.
    """
    if is_loopback and is_paired:
        actor = Actor.OWNER
        actor_id = "local-owner"
    else:
        actor = Actor.GUEST
        actor_id = "guest"

    resolved_session_id = session_id or _new_token()
    return RequestContext(
        actor_context=ActorContext(
            actor_id=actor_id,
            actor=actor,
            session_id=resolved_session_id,
            source=source,
        ),
        connection_token=connection_token,
        session_id=resolved_session_id,
        is_loopback=is_loopback,
        is_paired=is_paired,
    )
