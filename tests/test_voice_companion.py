"""Voice companion contract, validation, and privacy-safe UI layer tests."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.privacy_scan import collect_public_source_files

from core.voice_companion.contract import (
    ALLOWED_COMPANION_TYPES,
    ALLOWED_PRESENTATIONS,
    MAX_COMPANION_CAPTION_CHARS,
    CompanionCaption,
    CompanionState,
    companion_update_payload,
    sanitize_caption_text,
    validate_companion_type,
    validate_presentation,
)
from core.voice_companion.preferences import CompanionPreferences, load_preferences, save_preferences
from core.voice_companion.session import VoiceCompanionSession, is_valid_transition
from core.voice_companion.bridge import VoiceCompanionBridge
from core.voice_companion.status import is_voice_companion_enabled
from core.path_literals import DOT_HIKARI, EPISODES_DB

# Captured at module import (before per-test HOME monkeypatch) for isolation proof.
_AUDIT_REAL_COMPANION_PREFS = (
    Path(os.environ.get("HOME", str(Path.home()))).expanduser()
    / DOT_HIKARI
    / "companion_ui.json"
)


def _audit_real_prefs_mtime_ns() -> int | None:
    if _AUDIT_REAL_COMPANION_PREFS.is_file():
        return _AUDIT_REAL_COMPANION_PREFS.stat().st_mtime_ns
    return None


@pytest.fixture(autouse=True)
def _isolated_companion_runtime(tmp_path, monkeypatch):
    """Keep voice companion tests off the developer HOME and live preference paths."""
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    prefs_path = tmp_path / "companion_ui.json"
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("HIKARI_COMPANION_PREFS_PATH", str(prefs_path))
    monkeypatch.delenv("HIKARI_VOICE_COMPANION", raising=False)
    yield {"home": isolated_home, "prefs": prefs_path}


@pytest.fixture
def voice_companion_enabled(monkeypatch):
    monkeypatch.setenv("HIKARI_VOICE_COMPANION", "1")


def test_voice_companion_disabled_by_default():
    assert is_voice_companion_enabled() is False


def test_frontend_feature_gated_by_env_constant():
    text = _frontend_page_source()
    assert "VOICE_COMPANION_UI_ENABLED" in text
    assert 'NEXT_PUBLIC_HIKARI_VOICE_COMPANION === "1"' in text


def _frontend_page_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "hikari-frontend"
        / "src"
        / "app"
        / "page.tsx"
    ).read_text(encoding="utf-8")


def _companion_enabled_voice_block(text: str) -> str:
    """Companion UI path inside startListening (after disabled-mode early return)."""
    marker = "if (!canStartMicrophoneCapture())"
    start = text.index(marker)
    end = text.index("const getOrbGradient = () => {", start)
    return text[start:end]


def test_allowed_companion_types_only():
    assert set(ALLOWED_COMPANION_TYPES) == {"cat", "dog", "bird"}


def test_allowed_presentations_only():
    assert set(ALLOWED_PRESENTATIONS) == {"male", "female", "non-binary"}


@pytest.mark.parametrize("invalid", ["dragon", "fish", "", "Cat"])
def test_invalid_companion_type_rejected(invalid: str):
    with pytest.raises(ValueError, match="Invalid companion_type"):
        validate_companion_type(invalid)


@pytest.mark.parametrize("invalid", ["other", "nb", "Male", ""])
def test_invalid_presentation_rejected(invalid: str):
    with pytest.raises(ValueError, match="Invalid presentation"):
        validate_presentation(invalid)


@pytest.mark.parametrize("valid", list(ALLOWED_COMPANION_TYPES))
def test_valid_companion_types(valid: str):
    assert validate_companion_type(valid) == valid


@pytest.mark.parametrize("valid", list(ALLOWED_PRESENTATIONS))
def test_valid_presentations(valid: str):
    assert validate_presentation(valid) == valid


def test_state_transitions_hidden_to_idle():
    session = VoiceCompanionSession()
    assert session.state == CompanionState.HIDDEN
    assert session.transition(CompanionState.IDLE)
    assert session.state == CompanionState.IDLE


def test_state_transitions_voice_flow():
    session = VoiceCompanionSession()
    session.transition(CompanionState.IDLE)
    session.voice_turn_started()
    assert session.state == CompanionState.LISTENING
    session.user_transcript("Hello from the demo user", is_final=True)
    assert session.state == CompanionState.THINKING
    session.assistant_speaking("Demo assistant reply", is_final=True)
    assert session.state == CompanionState.SPEAKING
    session.return_idle()
    assert session.state == CompanionState.IDLE


def test_invalid_transition_rejected():
    session = VoiceCompanionSession()
    assert session.state == CompanionState.HIDDEN
    assert not session.transition(CompanionState.SPEAKING)
    assert session.state == CompanionState.HIDDEN


def test_is_valid_transition_matrix():
    assert is_valid_transition(CompanionState.IDLE, CompanionState.LISTENING)
    assert not is_valid_transition(CompanionState.HIDDEN, CompanionState.SPEAKING)


def test_caption_payload_shape():
    cap = CompanionCaption(
        role="user",
        text="Sample caption for UI only",
        is_final=True,
        timestamp="2020-01-01T00:00:00+00:00",
    )
    d = cap.to_dict()
    assert set(d.keys()) == {"role", "text", "is_final", "timestamp"}
    assert d["role"] == "user"


def test_companion_update_event_not_coupled_to_brain():
    payload = companion_update_payload(
        CompanionState.LISTENING,
        caption=CompanionCaption(
            role="assistant",
            text="Ephemeral caption",
            is_final=False,
            timestamp="2020-01-01T00:00:00+00:00",
        ),
    )
    assert payload["type"] == "companion_update"
    assert payload["companion"]["state"] == "listening"
    assert "brain" not in json.dumps(payload).lower()


def test_preferences_roundtrip(tmp_path, monkeypatch):
    prefs_file = tmp_path / "companion_ui.json"
    monkeypatch.setenv("HIKARI_COMPANION_PREFS_PATH", str(prefs_file))
    save_preferences(CompanionPreferences(companion_type="dog", presentation="female"))
    loaded = load_preferences()
    assert loaded.companion_type == "dog"
    assert loaded.presentation == "female"


def test_save_preferences_valid_writes_expected_json(tmp_path, monkeypatch):
    prefs_file = tmp_path / "companion_ui.json"
    monkeypatch.setenv("HIKARI_COMPANION_PREFS_PATH", str(prefs_file))
    save_preferences(CompanionPreferences(companion_type="bird", presentation="male"))
    data = json.loads(prefs_file.read_text(encoding="utf-8"))
    assert data == {"companion_type": "bird", "presentation": "male"}


def test_save_preferences_invalid_companion_type_raises_no_write(tmp_path, monkeypatch):
    prefs_file = tmp_path / "companion_ui.json"
    monkeypatch.setenv("HIKARI_COMPANION_PREFS_PATH", str(prefs_file))
    prefs_file.write_text(
        json.dumps({"companion_type": "cat", "presentation": "non-binary"}) + "\n",
        encoding="utf-8",
    )
    original = prefs_file.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid companion_type"):
        save_preferences(CompanionPreferences(companion_type="dragon", presentation="female"))
    assert prefs_file.read_text(encoding="utf-8") == original


def test_save_preferences_invalid_presentation_raises_no_write(tmp_path, monkeypatch):
    prefs_file = tmp_path / "companion_ui.json"
    monkeypatch.setenv("HIKARI_COMPANION_PREFS_PATH", str(prefs_file))
    prefs_file.write_text(
        json.dumps({"companion_type": "dog", "presentation": "female"}) + "\n",
        encoding="utf-8",
    )
    original = prefs_file.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid presentation"):
        save_preferences(CompanionPreferences(companion_type="dog", presentation="other"))
    assert prefs_file.read_text(encoding="utf-8") == original


def test_companion_preferences_rejects_invalid_construction():
    with pytest.raises(ValueError, match="Invalid companion_type"):
        CompanionPreferences(companion_type="unicorn", presentation="male")


def test_bridge_voice_turn_event_sequence_from_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_COMPANION_PREFS_PATH", str(tmp_path / "prefs.json"))
    sent: list = []
    bridge = VoiceCompanionBridge(send=sent.append)
    long_reply = "z" * (MAX_COMPANION_CAPTION_CHARS + 100)

    returned = bridge.run_voice_turn("hello", lambda: long_reply)
    bridge.finish_voice_turn()

    assert len(returned) == len(long_reply)
    events = VoiceCompanionBridge.summarize_events(sent)
    assert events == [
        ("listening", None, None),
        ("thinking", "user", "hello"),
        ("speaking", "assistant", "z" * MAX_COMPANION_CAPTION_CHARS),
        ("idle", "assistant", "z" * MAX_COMPANION_CAPTION_CHARS),
    ]
    assert VoiceCompanionBridge.event_types(sent) == ["companion_update"] * 4


def test_bridge_does_not_emit_on_rejected_transition():
    sent: list = []
    bridge = VoiceCompanionBridge(send=sent.append)
    assert bridge.session.state == CompanionState.HIDDEN
    assert not bridge.session.transition(CompanionState.SPEAKING)
    assert sent == []


def test_bridge_hidden_voice_turn_enters_listening_first(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_COMPANION_PREFS_PATH", str(tmp_path / "prefs.json"))
    bridge = VoiceCompanionBridge()
    assert bridge.session.state == CompanionState.HIDDEN
    bridge.run_voice_turn("demo voice line", lambda: "demo reply")
    assert bridge.session.state == CompanionState.SPEAKING


def test_second_listening_clears_stale_caption(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_COMPANION_PREFS_PATH", str(tmp_path / "prefs.json"))
    sent: list = []
    bridge = VoiceCompanionBridge(send=sent.append)
    bridge.run_voice_turn("first line", lambda: "first reply")
    bridge.finish_voice_turn()
    assert bridge.session.state == CompanionState.IDLE
    assert bridge.session.caption is not None

    bridge.on_voice_listening()
    events = VoiceCompanionBridge.summarize_events(sent)
    assert events[-1] == ("listening", None, None)
    payload = sent[-1]
    assert "caption" not in payload.get("companion", {})


def test_duplicate_listening_signal_emits_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_COMPANION_PREFS_PATH", str(tmp_path / "prefs.json"))
    sent: list = []
    bridge = VoiceCompanionBridge(send=sent.append)
    bridge.on_voice_listening()
    bridge.on_voice_listening()
    bridge.on_voice_listening()
    listening_events = [
        p
        for p in sent
        if p.get("type") == "companion_update"
        and p.get("companion", {}).get("state") == "listening"
    ]
    assert len(listening_events) == 1


def test_server_voice_plain_response_when_companion_disabled():
    from core.server import WebSocketServer

    sent: list = []

    class MockWebSocket:
        async def send(self, data: str) -> None:
            sent.append(json.loads(data))

    class MockOrchestrator:
        def process_input(self, user_input: str, source: str = "device") -> str:
            assert source == "voice_remote"
            return "plain voice reply"

    server = WebSocketServer(MockOrchestrator(), port=9999)
    asyncio.run(
        server._handle_message(
            MockWebSocket(),
            json.dumps({"type": "voice", "text": "demo voice input"}),
        )
    )

    assert sent == [{"type": "response", "text": "plain voice reply"}]


def test_server_typed_message_no_companion_events(voice_companion_enabled):
    from core.server import WebSocketServer

    sent: list = []

    class MockWebSocket:
        async def send(self, data: str) -> None:
            sent.append(json.loads(data))

    long_reply = "x" * (MAX_COMPANION_CAPTION_CHARS + 50)

    class MockOrchestrator:
        def process_input(self, user_input: str, source: str = "device") -> str:
            assert source == "device"
            return long_reply

    server = WebSocketServer(MockOrchestrator(), port=9999)
    asyncio.run(
        server._handle_message(
            MockWebSocket(),
            json.dumps({"type": "message", "text": "typed demo query"}),
        )
    )

    assert sent == [{"type": "response", "text": long_reply}]
    assert len(sent[0]["text"]) > MAX_COMPANION_CAPTION_CHARS


def test_server_voice_turn_wire_order_and_response_integrity(voice_companion_enabled):
    from core.server import WebSocketServer

    sent: list = []

    class MockWebSocket:
        async def send(self, data: str) -> None:
            sent.append(json.loads(data))

    long_reply = "r" * (MAX_COMPANION_CAPTION_CHARS + 75)

    class MockOrchestrator:
        def process_input(self, user_input: str, source: str = "device") -> str:
            assert source == "voice_remote"
            return long_reply

    server = WebSocketServer(MockOrchestrator(), port=9999)
    asyncio.run(
        server._handle_message(
            MockWebSocket(),
            json.dumps({"type": "voice", "text": "demo voice input"}),
        )
    )

    types = VoiceCompanionBridge.event_types(sent)
    states = [
        p["companion"]["state"]
        for p in sent
        if p.get("type") == "companion_update"
    ]
    assert types == [
        "companion_update",
        "companion_update",
        "companion_update",
        "response",
        "companion_update",
    ]
    assert states == ["listening", "thinking", "speaking", "idle"]
    response_idx = types.index("response")
    idle_idx = len(types) - 1
    assert response_idx == 3
    assert idle_idx == 4
    assert len(sent[response_idx]["text"]) == len(long_reply)
    speaking = sent[2]["companion"]["caption"]["text"]
    assert len(speaking) == MAX_COMPANION_CAPTION_CHARS


def test_server_voice_orchestrator_exception_terminal_lifecycle(voice_companion_enabled):
    from core.server import WebSocketServer
    from core.voice_companion.bridge import VOICE_PROCESSING_ERROR_MESSAGE

    sent: list = []
    internal_marker = "INTERNAL_VOICE_ORCHESTRATOR_FAILURE_MARKER"

    class MockWebSocket:
        async def send(self, data: str) -> None:
            sent.append(json.loads(data))

    class FailingOrchestrator:
        def process_input(self, user_input: str, source: str = "device") -> str:
            if source == "voice_remote":
                raise RuntimeError(internal_marker)
            return "typed ok"

    server = WebSocketServer(FailingOrchestrator(), port=9999)
    websocket = MockWebSocket()
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "voice", "text": "demo voice input"}),
        )
    )

    states = [
        p["companion"]["state"]
        for p in sent
        if p.get("type") == "companion_update"
    ]
    assert "thinking" not in states[-1:]
    assert states[-2:] == ["error", "idle"]
    assert server._companion_for(websocket).session.state == CompanionState.IDLE

    outbound = json.dumps(sent)
    assert internal_marker not in outbound
    assert "Traceback" not in outbound
    response_payloads = [p for p in sent if p.get("type") == "response"]
    assert len(response_payloads) == 1
    assert response_payloads[0]["text"] == VOICE_PROCESSING_ERROR_MESSAGE


def test_frontend_typed_chat_does_not_activate_companion():
    text = _frontend_page_source()
    send_start = text.index("const sendMessage = () => {")
    send_end = text.index("const startListening = () => {")
    send_block = text[send_start:send_end]
    voice_block = _companion_enabled_voice_block(text)
    assert "cancelVoiceCapture()" in send_block
    assert "setCompanionState" not in send_block
    assert "setCompanionCaption" not in send_block
    assert "voiceSessionActive" not in send_block
    assert 'type: "message"' in send_block
    assert 'type: "voice"' in voice_block
    assert "beginVoiceCapture()" in voice_block
    assert "submitVoiceRequest(" in voice_block
    assert "markVoiceRequestSubmitted" not in text
    assert "beginVoiceSession" not in voice_block


def test_frontend_voice_capture_does_not_mark_turn_submitted():
    text = _frontend_page_source()
    capture_start = text.index("const beginVoiceCapture = useCallback")
    capture_end = text.index("const submitVoiceRequest = useCallback")
    capture_block = text[capture_start:capture_end]
    assert "voiceTurnActiveRef.current = false" in capture_block
    assert "voiceTurnActiveRef.current = true" not in capture_block
    assert "setVoiceTurnActive(false)" in capture_block


def test_frontend_start_listening_blocks_reentry_while_voice_active():
    text = _frontend_page_source()
    listen_block = _companion_enabled_voice_block(text)
    assert "canStartMicrophoneCapture()" in listen_block
    assert "if (!canStartMicrophoneCapture())" in listen_block
    guard_start = text.index("const canStartMicrophoneCapture = useCallback")
    guard_end = text.index("const beginVoiceCapture = useCallback")
    guard_block = text[guard_start:guard_end]
    assert "voiceSessionActiveRef.current" in guard_block
    assert "voiceTurnActiveRef.current" in guard_block
    assert "recognitionRef.current !== null" in guard_block
    second_recognition = listen_block.count("new SpeechRecognition()")
    assert second_recognition == 1


def test_frontend_microphone_disabled_during_listening_and_awaiting_response():
    text = _frontend_page_source()
    assert "const microphoneDisabled =" in text
    assert "voiceSessionActive" in text
    assert "voiceTurnActive" in text
    assert "recognitionCaptureActive" in text
    assert "disabled={microphoneDisabled}" in text
    assert "aria-disabled={microphoneDisabled}" in text
    submit_start = text.index("const submitVoiceRequest = useCallback")
    submit_end = text.index("const syncCompanionPrefs = useCallback")
    submit_block = text[submit_start:submit_end]
    assert "setVoiceTurnActive(true)" in submit_block


def test_frontend_recognition_released_without_cancelling_submitted_turn():
    text = _frontend_page_source()
    voice_block = _companion_enabled_voice_block(text)
    onend_start = voice_block.index("recognition.onend")
    onend_end = voice_block.index("recognition.start()", onend_start)
    onend_block = voice_block[onend_start:onend_end]
    assert "releaseRecognitionInstance(recognition)" in onend_block
    assert "!voiceTurnActiveRef.current && voiceSessionActiveRef.current" in onend_block


def test_frontend_mic_reenabled_on_terminal_voice_reset():
    text = _frontend_page_source()
    reset_start = text.index("const resetVoiceCompanion = useCallback")
    reset_end = text.index("const releaseRecognitionInstance = useCallback")
    reset_block = text[reset_start:reset_end]
    assert "setVoiceSessionActive(false)" in reset_block
    assert "setVoiceTurnActive(false)" in reset_block


def test_frontend_submit_voice_request_only_after_open_send():
    text = _frontend_page_source()
    submit_start = text.index("const submitVoiceRequest = useCallback")
    submit_end = text.index("const syncCompanionPrefs = useCallback")
    submit_block = text[submit_start:submit_end]
    assert "ws.readyState !== WebSocket.OPEN" in submit_block
    assert "cancelVoiceCapture()" in submit_block
    assert "voiceTurnActiveRef.current = true" in submit_block
    send_idx = submit_block.index("ws.send(")
    turn_idx = submit_block.index("voiceTurnActiveRef.current = true")
    message_idx = submit_block.index('addMessage(trimmed, "user")')
    assert send_idx < message_idx < turn_idx
    voice_block = _companion_enabled_voice_block(text)
    onresult_start = voice_block.index("recognition.onresult")
    onresult_end = voice_block.index("recognition.onerror", onresult_start)
    onresult_block = voice_block[onresult_start:onresult_end]
    assert "markVoiceRequestSubmitted" not in onresult_block
    assert "submitVoiceRequest(transcript, captureToken)" in onresult_block
    assert "setTimeout" not in onresult_block


def test_frontend_capture_token_bound_to_recognition_instance():
    text = _frontend_page_source()
    voice_block = _companion_enabled_voice_block(text)
    voice_end = voice_block.index("recognition.start()")
    voice_block = voice_block[:voice_end]
    assert "const captureToken = voiceCaptureGenerationRef.current" in voice_block
    assert "captureToken !== voiceCaptureGenerationRef.current" in voice_block
    onresult_start = voice_block.index("recognition.onresult")
    onresult_end = voice_block.index("recognition.onerror", onresult_start)
    onresult_block = voice_block[onresult_start:onresult_end]
    assert "submitVoiceRequest(transcript, captureToken)" in onresult_block


def test_frontend_submit_rejects_cancelled_or_inactive_session():
    text = _frontend_page_source()
    submit_start = text.index("const submitVoiceRequest = useCallback")
    submit_end = text.index("const syncCompanionPrefs = useCallback")
    submit_block = text[submit_start:submit_end]
    assert "captureToken !== voiceCaptureGenerationRef.current" in submit_block
    assert "!voiceSessionActiveRef.current" in submit_block


def test_frontend_cancel_terminates_recognition_abort_else_stop():
    text = _frontend_page_source()
    assert "function terminateSpeechRecognition" in text
    assert "typeof recognition.abort === \"function\"" in text
    assert "typeof recognition.stop === \"function\"" in text
    cancel_start = text.index("const cancelVoiceCapture = useCallback")
    cancel_end = text.index("const beginVoiceCapture = useCallback")
    cancel_block = text[cancel_start:cancel_end]
    assert "terminateSpeechRecognition(recognition)" in cancel_block
    assert "voiceCaptureGenerationRef.current += 1" in cancel_block


def test_frontend_cancelled_recognition_emits_no_voice_payload():
    text = _frontend_page_source()
    send_start = text.index("const sendMessage = () => {")
    send_end = text.index("const startListening = () => {")
    assert "cancelVoiceCapture()" in text[send_start:send_end]
    submit_start = text.index("const submitVoiceRequest = useCallback")
    submit_end = text.index("const syncCompanionPrefs = useCallback")
    submit_block = text[submit_start:submit_end]
    assert "voiceSessionActiveRef.current" in submit_block
    voice_block = _companion_enabled_voice_block(text)
    onresult_start = voice_block.index("recognition.onresult")
    onresult_end = voice_block.index("recognition.onerror", onresult_start)
    onresult_block = voice_block[onresult_start:onresult_end]
    assert "captureToken !== voiceCaptureGenerationRef.current" in onresult_block
    assert "addMessage" not in onresult_block


def test_frontend_onresult_onend_no_race():
    """onresult must submit immediately; onend must not cancel a successfully sent turn."""
    text = _frontend_page_source()
    voice_block = _companion_enabled_voice_block(text)
    onresult_start = voice_block.index("recognition.onresult")
    onresult_end = voice_block.index("recognition.onerror", onresult_start)
    onresult_block = voice_block[onresult_start:onresult_end]
    onend_start = voice_block.index("recognition.onend")
    onend_end = voice_block.index("recognition.start()", onend_start)
    onend_block = voice_block[onend_start:onend_end]
    assert "setTimeout" not in onresult_block
    assert "submitVoiceRequest(transcript, captureToken)" in onresult_block
    assert "addMessage" not in onresult_block
    assert "!voiceTurnActiveRef.current && voiceSessionActiveRef.current" in onend_block


def test_frontend_failed_send_does_not_display_user_message():
    text = _frontend_page_source()
    submit_start = text.index("const submitVoiceRequest = useCallback")
    submit_end = text.index("const syncCompanionPrefs = useCallback")
    submit_block = text[submit_start:submit_end]
    send_idx = submit_block.index("ws.send(")
    message_idx = submit_block.index('addMessage(trimmed, "user")')
    assert send_idx < message_idx
    voice_block = _companion_enabled_voice_block(text)
    onresult_start = voice_block.index("recognition.onresult")
    onresult_end = voice_block.index("recognition.onerror", onresult_start)
    onresult_block = voice_block[onresult_start:onresult_end]
    assert "addMessage" not in onresult_block


def test_frontend_voice_send_failure_resets_companion():
    text = _frontend_page_source()
    submit_start = text.index("const submitVoiceRequest = useCallback")
    submit_end = text.index("const syncCompanionPrefs = useCallback")
    submit_block = text[submit_start:submit_end]
    assert "catch" in submit_block
    assert "cancelVoiceCapture()" in submit_block
    reset_start = text.index("const resetVoiceCompanion = useCallback")
    reset_end = text.index("const cancelVoiceCapture = useCallback")
    reset_block = text[reset_start:reset_end]
    assert 'setOrbState("idle")' in reset_block
    assert "setIsListening(false)" in reset_block
    assert "setIsTyping(false)" in reset_block


def test_frontend_typed_send_cancels_active_recognition():
    text = _frontend_page_source()
    send_start = text.index("const sendMessage = () => {")
    send_end = text.index("const startListening = () => {")
    send_block = text[send_start:send_end]
    assert "cancelVoiceCapture()" in send_block
    cancel_start = text.index("const cancelVoiceCapture = useCallback")
    cancel_end = text.index("const beginVoiceCapture = useCallback")
    cancel_block = text[cancel_start:cancel_end]
    assert "voiceCaptureGenerationRef.current += 1" in cancel_block
    assert "recognitionRef.current" in cancel_block
    assert "terminateSpeechRecognition(recognition)" in cancel_block


def test_frontend_late_voice_result_ignored_after_cancel():
    text = _frontend_page_source()
    submit_start = text.index("const submitVoiceRequest = useCallback")
    submit_end = text.index("const syncCompanionPrefs = useCallback")
    submit_block = text[submit_start:submit_end]
    assert "captureToken !== voiceCaptureGenerationRef.current" in submit_block
    voice_block = _companion_enabled_voice_block(text)
    onresult_start = voice_block.index("recognition.onresult")
    onresult_end = voice_block.index("recognition.onerror", onresult_start)
    onresult_block = voice_block[onresult_start:onresult_end]
    assert "voiceCaptureGenerationRef.current" in onresult_block


def test_frontend_websocket_close_resets_voice_state():
    text = _frontend_page_source()
    connect_start = text.index("const connect = useCallback")
    connect_end = text.index("}, [serverUrl", connect_start)
    connect_block = text[connect_start:connect_end]
    assert "ws.onclose" in connect_block
    assert "cancelVoiceCapture()" in connect_block


def test_frontend_terminal_idle_resets_orb():
    text = _frontend_page_source()
    reset_start = text.index("const resetVoiceCompanion = useCallback")
    reset_end = text.index("const beginVoiceCapture = useCallback")
    reset_block = text[reset_start:reset_end]
    assert 'setOrbState("idle")' in reset_block


def test_frontend_silent_recognition_end_resets_without_submission():
    text = _frontend_page_source()
    voice_block = _companion_enabled_voice_block(text)
    onend_start = voice_block.index("recognition.onend")
    onend_end = voice_block.index("recognition.start()", onend_start)
    onend_block = voice_block[onend_start:onend_end]
    assert "recognition.onend" in onend_block
    assert "!voiceTurnActiveRef.current && voiceSessionActiveRef.current" in onend_block
    assert "cancelVoiceCapture()" in onend_block


def test_frontend_overlay_uses_voice_session_not_orb_state():
    text = _frontend_page_source()
    overlay_start = text.index("<VoiceCompanionOverlay")
    overlay_block = text[overlay_start : overlay_start + 500]
    assert "voiceSessionActive" in overlay_block
    assert "orbState" not in overlay_block


def test_frontend_voice_idle_resets_companion():
    text = _frontend_page_source()
    apply_start = text.index("const applyCompanionUpdate = useCallback")
    apply_end = text.index("const connect = useCallback")
    apply_block = text[apply_start:apply_end]
    assert 'state === "idle"' in apply_block
    assert 'state === "hidden"' in apply_block
    assert "resetVoiceCompanion()" in apply_block


def test_frontend_new_voice_session_clears_caption():
    text = _frontend_page_source()
    begin_start = text.index("const beginVoiceCapture = useCallback")
    begin_end = text.index("const submitVoiceRequest = useCallback")
    begin_block = text[begin_start:begin_end]
    assert "setCompanionCaption(null)" in begin_block
    assert "setCompanionState(\"listening\")" in begin_block


def test_frontend_listening_update_clears_stale_caption():
    text = _frontend_page_source()
    apply_start = text.index("const applyCompanionUpdate = useCallback")
    apply_end = text.index("const connect = useCallback")
    apply_block = text[apply_start:apply_end]
    assert 'state === "listening"' in apply_block
    assert "setCompanionCaption(null)" in apply_block


def test_frontend_recognition_error_bounded_reset():
    text = _frontend_page_source()
    voice_block = _companion_enabled_voice_block(text)
    assert "recognition.onerror" in voice_block
    assert "Voice input error" in voice_block
    assert "setTimeout" in voice_block
    assert "cancelVoiceCapture()" in voice_block


def test_global_brain_v2_episodes_db_isolated_from_live_home():
    from core.brain_v2.db_paths import ENV_BRAIN_V2_EPISODES_DB, resolve_episodes_db_path

    explicit = os.environ.get(ENV_BRAIN_V2_EPISODES_DB)
    assert explicit
    assert "hikari-test-brain-v2" in explicit
    assert resolve_episodes_db_path().resolve() == Path(explicit).resolve()


def test_orchestrator_init_uses_isolated_brain_v2_db(tmp_path, monkeypatch):
    """Full Orchestrator() must not open the developer live episodes DB during tests."""
    from core.brain_v2.db_paths import resolve_episodes_db_path
    from core.orchestrator import HIKARI_Orchestrator

    isolated_db = tmp_path / EPISODES_DB
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(isolated_db))
    orch = HIKARI_Orchestrator()
    assert orch.brain_v2 is not None
    assert orch.brain_v2.store.db_path.resolve() == isolated_db.resolve()
    assert resolve_episodes_db_path().resolve() == isolated_db.resolve()


def test_companion_prefs_write_uses_isolated_path_only():
    before_mtime = _audit_real_prefs_mtime_ns()
    isolated_prefs = Path(os.environ["HIKARI_COMPANION_PREFS_PATH"])
    save_preferences(CompanionPreferences(companion_type="bird", presentation="male"))
    assert isolated_prefs.is_file()
    assert json.loads(isolated_prefs.read_text(encoding="utf-8")) == {
        "companion_type": "bird",
        "presentation": "male",
    }
    assert _audit_real_prefs_mtime_ns() == before_mtime


def test_voice_companion_subprocess_pref_writes_isolated_only(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    before_mtime = _audit_real_prefs_mtime_ns()

    isolated_home = tmp_path / "subhome"
    isolated_home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(isolated_home)
    env["HIKARI_COMPANION_PREFS_PATH"] = str(isolated_home / "companion_ui.json")
    env["HIKARI_VOICE_COMPANION"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_voice_companion.py",
            "-q",
            "--tb=no",
            "-k",
            "not subprocess_pref_writes_isolated_only",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _audit_real_prefs_mtime_ns() == before_mtime


def test_voice_companion_package_is_ui_boundary_only():
    """Companion package must not import or reference Brain v2 episode storage."""
    root = Path(__file__).resolve().parents[1] / "core" / "voice_companion"
    disallowed_tokens = (
        "brain_v2",
        "EpisodeStore",
        "episode_store",
        "open_episode_store",
    )
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in disallowed_tokens:
            assert token not in text, f"{path.name} must not reference {token!r}"


def test_sanitize_caption_text_truncates_long_input():
    long_text = "a" * (MAX_COMPANION_CAPTION_CHARS + 100)
    assert len(sanitize_caption_text(long_text)) == MAX_COMPANION_CAPTION_CHARS


def test_sanitize_caption_text_strips_control_chars():
    assert sanitize_caption_text("hello\x00world") == "helloworld"


def test_companion_update_payload_truncates_caption():
    long_text = "b" * (MAX_COMPANION_CAPTION_CHARS + 50)
    payload = companion_update_payload(
        CompanionState.SPEAKING,
        caption=CompanionCaption(
            role="assistant",
            text=long_text,
            is_final=True,
            timestamp="2020-01-01T00:00:00+00:00",
        ),
    )
    assert len(payload["companion"]["caption"]["text"]) == MAX_COMPANION_CAPTION_CHARS


def test_operational_hide_from_thinking():
    session = VoiceCompanionSession()
    session.transition(CompanionState.IDLE)
    session.transition(CompanionState.LISTENING)
    session.user_transcript("demo", is_final=True)
    assert session.state == CompanionState.THINKING
    assert not session.transition(CompanionState.HIDDEN)
    session.hide()
    assert session.state == CompanionState.HIDDEN


def test_matrix_transition_listening_to_thinking_without_operational():
    session = VoiceCompanionSession()
    session.transition(CompanionState.IDLE)
    session.voice_turn_started()
    session.user_transcript("demo line", is_final=True)
    assert session.state == CompanionState.THINKING


def test_frontend_companion_utils_in_privacy_scan_scope():
    repo = Path(__file__).resolve().parents[1]
    rel_paths = {p.relative_to(repo).as_posix() for p in collect_public_source_files()}
    assert "hikari-frontend/src/utils/companion/constants.ts" in rel_paths
    assert "hikari-frontend/src/utils/companion/storage.ts" in rel_paths


def test_bridge_rejects_invalid_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_COMPANION_PREFS_PATH", str(tmp_path / "prefs.json"))
    bridge = VoiceCompanionBridge()
    with pytest.raises(ValueError):
        bridge.apply_preferences("unicorn", "male")
