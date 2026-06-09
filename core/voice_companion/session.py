"""In-memory companion session state machine (not persisted to Brain v2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set

from core.voice_companion.contract import (
    CompanionCaption,
    CompanionState,
    CaptionRole,
    sanitize_caption_text,
)

# Normal UI state transitions (enforced unless ``operational=True``).
_VALID_TRANSITIONS: Dict[CompanionState, Set[CompanionState]] = {
    CompanionState.HIDDEN: {
        CompanionState.IDLE,
        CompanionState.LISTENING,
        CompanionState.ERROR,
    },
    CompanionState.IDLE: {
        CompanionState.HIDDEN,
        CompanionState.LISTENING,
        CompanionState.THINKING,
        CompanionState.SPEAKING,
        CompanionState.ERROR,
    },
    CompanionState.LISTENING: {
        CompanionState.IDLE,
        CompanionState.THINKING,
        CompanionState.HIDDEN,
        CompanionState.ERROR,
    },
    CompanionState.THINKING: {
        CompanionState.SPEAKING,
        CompanionState.IDLE,
        CompanionState.LISTENING,
        CompanionState.ERROR,
    },
    CompanionState.SPEAKING: {
        CompanionState.IDLE,
        CompanionState.LISTENING,
        CompanionState.THINKING,
        CompanionState.HIDDEN,
        CompanionState.ERROR,
    },
    CompanionState.ERROR: {
        CompanionState.IDLE,
        CompanionState.HIDDEN,
        CompanionState.LISTENING,
    },
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class VoiceCompanionSession:
    """Ephemeral companion state for one voice/text UI session."""

    state: CompanionState = CompanionState.HIDDEN
    caption: Optional[CompanionCaption] = None
    _listeners: List[Callable[[CompanionState, Optional[CompanionCaption]], None]] = field(
        default_factory=list, repr=False
    )

    def transition(
        self,
        new_state: CompanionState,
        *,
        caption: Optional[CompanionCaption] = None,
        operational: bool = False,
    ) -> bool:
        """Move to ``new_state``.

        ``operational=True`` bypasses the transition matrix for lifecycle resets
        (e.g. hide overlay from thinking). Normal turn flow uses the matrix only.
        """
        if new_state == self.state and caption is None:
            return True
        if not operational and new_state != self.state:
            allowed = _VALID_TRANSITIONS.get(self.state, set())
            if new_state not in allowed:
                return False
        self.state = new_state
        if caption is not None:
            self.caption = caption
        self._notify()
        return True

    def clear_caption(self) -> None:
        self.caption = None

    def set_caption(
        self,
        role: CaptionRole,
        text: str,
        *,
        is_final: bool = True,
    ) -> None:
        self.caption = CompanionCaption(
            role=role,
            text=sanitize_caption_text(text),
            is_final=is_final,
            timestamp=_utc_iso(),
        )
        self._notify()

    def on_change(
        self, listener: Callable[[CompanionState, Optional[CompanionCaption]], None]
    ) -> None:
        self._listeners.append(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener(self.state, self.caption)

    def voice_turn_started(self) -> None:
        self.transition(CompanionState.LISTENING)

    def user_transcript(self, text: str, *, is_final: bool = False) -> None:
        self.set_caption("user", text, is_final=is_final)
        if is_final:
            self.transition(CompanionState.THINKING)

    def assistant_thinking(self) -> None:
        self.transition(CompanionState.THINKING)

    def assistant_speaking(self, text: str, *, is_final: bool = True) -> None:
        self.set_caption("assistant", text, is_final=is_final)
        self.transition(CompanionState.SPEAKING)

    def return_idle(self) -> None:
        self.transition(CompanionState.IDLE)

    def set_error(self, message: str = "Voice session error") -> None:
        self.set_caption("system", message, is_final=True)
        self.transition(CompanionState.ERROR)

    def hide(self) -> bool:
        """Reset overlay to hidden from any state (operational lifecycle)."""
        return self.transition(CompanionState.HIDDEN, operational=True)


def is_valid_transition(
    from_state: CompanionState, to_state: CompanionState
) -> bool:
    if from_state == to_state:
        return True
    return to_state in _VALID_TRANSITIONS.get(from_state, set())
