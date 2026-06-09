"""Adapter between voice lifecycle and companion websocket events."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from core.voice_companion.contract import (
    CompanionPreferencesPayload,
    CompanionState,
    companion_update_payload,
    validate_companion_type,
    validate_presentation,
)
from core.voice_companion.preferences import CompanionPreferences, load_preferences, save_preferences
from core.voice_companion.session import VoiceCompanionSession

VOICE_PROCESSING_ERROR_MESSAGE = "Voice processing failed. Please try again."

SendFn = Callable[[Dict[str, Any]], None]
AsyncSendFn = Callable[[Dict[str, Any]], Awaitable[None]]


class VoiceCompanionBridge:
    """Owns ephemeral session state and builds companion_update messages.

    Emissions are explicit only (no session change listeners). Callers must not
    emit a state when ``VoiceCompanionSession.transition`` rejected the move.
    """

    def __init__(
        self,
        send: Optional[SendFn] = None,
        async_send: Optional[AsyncSendFn] = None,
        session: Optional[VoiceCompanionSession] = None,
    ):
        self._send = send
        self._async_send = async_send
        self.session = session or VoiceCompanionSession()
        self.preferences = load_preferences()

    def set_send(self, send: SendFn) -> None:
        self._send = send

    def set_async_send(self, async_send: AsyncSendFn) -> None:
        self._async_send = async_send

    def _payload(
        self, state: CompanionState, *, error_message: Optional[str] = None
    ) -> Dict[str, Any]:
        prefs = CompanionPreferencesPayload(
            companion_type=self.preferences.companion_type,
            presentation=self.preferences.presentation,
        )
        return companion_update_payload(
            state,
            caption=self.session.caption,
            preferences=prefs,
            error_message=error_message,
        )

    def emit_state(self, state: CompanionState, *, error_message: Optional[str] = None) -> None:
        if not self._send:
            return
        self._send(self._payload(state, error_message=error_message))

    async def emit_state_async(
        self, state: CompanionState, *, error_message: Optional[str] = None
    ) -> None:
        payload = self._payload(state, error_message=error_message)
        if self._async_send is not None:
            await self._async_send(payload)
        elif self._send is not None:
            self._send(payload)

    def enter_listening(self) -> bool:
        """Move hidden/idle into listening; return True when state changed to listening."""
        if self.session.state == CompanionState.LISTENING:
            return False
        if self.session.state in (CompanionState.HIDDEN, CompanionState.IDLE):
            return self.session.transition(CompanionState.LISTENING)
        return self.session.transition(CompanionState.LISTENING)

    def _emit_listening(self) -> None:
        """Clear stale captions and emit listening when session is in listening state."""
        self.session.clear_caption()
        if self.session.state == CompanionState.LISTENING:
            self.emit_state(CompanionState.LISTENING)

    async def _emit_listening_async(self) -> None:
        self.session.clear_caption()
        if self.session.state == CompanionState.LISTENING:
            await self.emit_state_async(CompanionState.LISTENING)

    async def on_voice_listening_async(self) -> None:
        if not self.enter_listening():
            return
        await self._emit_listening_async()

    def on_voice_listening(self) -> None:
        if not self.enter_listening():
            return
        self._emit_listening()

    def run_voice_turn(self, user_text: str, get_response: Callable[[], Optional[str]]) -> str:
        """Sync voice turn for tests: listening? -> thinking -> speaking; returns full response."""
        self.session.clear_caption()
        if self.enter_listening():
            self._emit_listening()
        self.session.user_transcript(user_text, is_final=True)
        if self.session.state == CompanionState.THINKING:
            self.emit_state(CompanionState.THINKING)
        full = get_response() or "No response generated"
        self.session.assistant_speaking(full, is_final=True)
        if self.session.state == CompanionState.SPEAKING:
            self.emit_state(CompanionState.SPEAKING)
        return full

    async def run_voice_turn_async(
        self, user_text: str, get_response: Callable[[], Optional[str]]
    ) -> str:
        """Voice turn with awaited companion events (listening if needed -> thinking -> speaking)."""
        self.session.clear_caption()
        if self.enter_listening():
            await self._emit_listening_async()
        self.session.user_transcript(user_text, is_final=True)
        if self.session.state == CompanionState.THINKING:
            await self.emit_state_async(CompanionState.THINKING)
        full = get_response() or "No response generated"
        self.session.assistant_speaking(full, is_final=True)
        if self.session.state == CompanionState.SPEAKING:
            await self.emit_state_async(CompanionState.SPEAKING)
        return full

    def finish_voice_turn(self) -> None:
        if self.session.state == CompanionState.IDLE:
            return
        if self.session.transition(CompanionState.IDLE) or self.session.transition(
            CompanionState.IDLE, operational=True
        ):
            self.emit_state(CompanionState.IDLE)

    async def finish_voice_turn_async(self) -> None:
        if self.session.state == CompanionState.IDLE:
            return
        if self.session.transition(CompanionState.IDLE) or self.session.transition(
            CompanionState.IDLE, operational=True
        ):
            await self.emit_state_async(CompanionState.IDLE)

    def apply_preferences(self, companion_type: str, presentation: str) -> CompanionPreferences:
        prefs = CompanionPreferences(
            companion_type=validate_companion_type(companion_type),
            presentation=validate_presentation(presentation),
        )
        self.preferences = prefs
        save_preferences(prefs)
        self.emit_state(self.session.state)
        return prefs

    def hide(self) -> None:
        if self.session.hide():
            self.emit_state(CompanionState.HIDDEN)

    async def hide_async(self) -> None:
        if self.session.hide():
            await self.emit_state_async(CompanionState.HIDDEN)

    def on_error(self, message: str) -> None:
        self.session.set_error(message)
        if self.session.state == CompanionState.ERROR:
            self.emit_state(CompanionState.ERROR, error_message=message)

    async def emit_voice_processing_failure_async(
        self,
        message: str = VOICE_PROCESSING_ERROR_MESSAGE,
    ) -> None:
        """Terminal voice failure: companion error, then idle (deterministic order)."""
        self.session.set_error(message)
        if self.session.state == CompanionState.ERROR:
            await self.emit_state_async(CompanionState.ERROR, error_message=message)
        await self.finish_voice_turn_async()

    @staticmethod
    def summarize_events(
        payloads: List[Dict[str, Any]],
    ) -> List[tuple[str, Optional[str], Optional[str]]]:
        """(state, caption_role, caption_text) per companion_update event."""
        rows: List[tuple[str, Optional[str], Optional[str]]] = []
        for payload in payloads:
            if payload.get("type") != "companion_update":
                continue
            companion = payload.get("companion") or {}
            caption = companion.get("caption") or {}
            rows.append(
                (
                    str(companion.get("state", "")),
                    caption.get("role"),
                    caption.get("text"),
                )
            )
        return rows

    @staticmethod
    def event_types(payloads: List[Dict[str, Any]]) -> List[str]:
        return [str(p.get("type", "")) for p in payloads]
