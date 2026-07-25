"""Tests for VAD state transitions, hysteresis, false-start handling, and bounds."""

import pytest

from core.voice_streaming.vad import (
    VADConfig,
    VADEngineState,
    VADFrameMeasurement,
    VADState,
)


def test_vad_config_validation():
    """Verify threshold validations for VAD config."""
    cfg = VADConfig(
        speech_start_threshold=0.6,
        speech_stop_threshold=0.3,
        interruption_threshold=0.85,
        min_speech_frames=3,
        min_silence_frames=5,
    )
    assert cfg.speech_start_threshold == 0.6
    assert cfg.speech_stop_threshold == 0.3

    # Stop threshold higher than start threshold raises ValueError
    with pytest.raises(ValueError, match="speech_stop_threshold cannot exceed"):
        VADConfig(speech_start_threshold=0.4, speech_stop_threshold=0.7)

    # Invalid probability
    with pytest.raises(ValueError, match="out of bounds"):
        VADConfig(speech_start_threshold=1.5)

    with pytest.raises(ValueError):
        VADConfig(pre_roll_ms=float("nan"))
    with pytest.raises(ValueError):
        VADConfig(post_roll_ms=float("inf"))


def test_vad_state_lifecycle():
    """Verify SILENCE -> POSSIBLE_SPEECH -> CONFIRMED_SPEECH -> POSSIBLE_END -> CONFIRMED_END."""
    cfg = VADConfig(min_speech_frames=3, min_silence_frames=3)
    vad = VADEngineState("s1", config=cfg)
    assert vad.current_state == VADState.SILENCE

    # 1. First speech frame -> POSSIBLE_SPEECH
    m0 = VADFrameMeasurement(sequence_id=0, monotonic_ns=100, speech_probability=0.8)
    state, event = vad.process_measurement(m0)
    assert state == VADState.POSSIBLE_SPEECH
    assert event is not None
    assert event.reason == "speech_probability_above_start_threshold"

    # 2. Second speech frame -> stays POSSIBLE_SPEECH
    m1 = VADFrameMeasurement(sequence_id=1, monotonic_ns=200, speech_probability=0.8)
    state, _ = vad.process_measurement(m1)
    assert state == VADState.POSSIBLE_SPEECH

    # 3. Third speech frame -> CONFIRMED_SPEECH (min_speech_frames=3 reached)
    m2 = VADFrameMeasurement(sequence_id=2, monotonic_ns=300, speech_probability=0.8)
    state, event = vad.process_measurement(m2)
    assert state == VADState.CONFIRMED_SPEECH
    assert event is not None
    assert event.reason == "min_speech_frames_reached"

    # 4. Silence frame -> POSSIBLE_END
    m3 = VADFrameMeasurement(sequence_id=3, monotonic_ns=400, speech_probability=0.1)
    state, event = vad.process_measurement(m3)
    assert state == VADState.POSSIBLE_END

    # 5. Consecutive silence frames -> CONFIRMED_END
    vad.process_measurement(VADFrameMeasurement(sequence_id=4, monotonic_ns=500, speech_probability=0.1))
    state, event = vad.process_measurement(VADFrameMeasurement(sequence_id=5, monotonic_ns=600, speech_probability=0.1))
    assert state == VADState.CONFIRMED_END
    assert event is not None
    assert event.reason == "min_silence_frames_reached"


def test_vad_false_start_handling():
    """Verify dropping below start threshold during POSSIBLE_SPEECH returns to SILENCE."""
    cfg = VADConfig(min_speech_frames=3)
    vad = VADEngineState("s1", config=cfg)

    # Speech frame 1 -> POSSIBLE_SPEECH
    vad.process_measurement(VADFrameMeasurement(sequence_id=0, monotonic_ns=100, speech_probability=0.8))
    assert vad.current_state == VADState.POSSIBLE_SPEECH

    # Silence frame before min_speech_frames reached -> False start -> SILENCE
    state, event = vad.process_measurement(VADFrameMeasurement(sequence_id=1, monotonic_ns=200, speech_probability=0.2))
    assert state == VADState.SILENCE
    assert event is not None
    assert "false_start" in event.reason


def test_vad_interruption_candidate():
    """Verify high confidence speech during assistant speaking triggers INTERRUPTION_CANDIDATE."""
    cfg = VADConfig(min_speech_frames=2, interruption_threshold=0.85, min_interruption_frames=2)
    vad = VADEngineState("s1", config=cfg)

    # Establish CONFIRMED_SPEECH
    vad.process_measurement(VADFrameMeasurement(sequence_id=0, monotonic_ns=100, speech_probability=0.9))
    vad.process_measurement(VADFrameMeasurement(sequence_id=1, monotonic_ns=200, speech_probability=0.9))
    assert vad.current_state == VADState.CONFIRMED_SPEECH

    # High probability speech with assistant_speaking=True -> INTERRUPTION_CANDIDATE
    state, _ = vad.process_measurement(
        VADFrameMeasurement(sequence_id=2, monotonic_ns=300, speech_probability=0.9),
        assistant_speaking=True,
    )
    assert vad.current_state == VADState.CONFIRMED_SPEECH  # Frame 1 of interruption

    state, event = vad.process_measurement(
        VADFrameMeasurement(sequence_id=3, monotonic_ns=400, speech_probability=0.9),
        assistant_speaking=True,
    )
    assert state == VADState.INTERRUPTION_CANDIDATE  # Frame 2 reached min_interruption_frames
    assert event is not None
    assert event.reason == "interruption_threshold_reached"


def test_vad_max_utterance_duration_limit():
    """Verify utterance exceeding max duration forces CONFIRMED_END."""
    cfg = VADConfig(min_speech_frames=1, max_utterance_duration_ms=50.0)
    vad = VADEngineState("s1", config=cfg)

    # Frame 1 (20ms) -> CONFIRMED_SPEECH
    vad.process_measurement(VADFrameMeasurement(sequence_id=0, monotonic_ns=100, speech_probability=0.8, frame_duration_ms=20.0))
    assert vad.current_state == VADState.CONFIRMED_SPEECH

    # Frame 2 (+20ms = 40ms) -> CONFIRMED_SPEECH
    vad.process_measurement(VADFrameMeasurement(sequence_id=1, monotonic_ns=200, speech_probability=0.8, frame_duration_ms=20.0))
    assert vad.current_state == VADState.CONFIRMED_SPEECH

    # Frame 3 (+20ms = 60ms > 50ms max) -> CONFIRMED_END
    state, event = vad.process_measurement(
        VADFrameMeasurement(sequence_id=2, monotonic_ns=300, speech_probability=0.8, frame_duration_ms=20.0)
    )
    assert state == VADState.CONFIRMED_END
    assert event is not None
    assert event.reason == "max_utterance_duration_exceeded"


def test_vad_out_of_order_frame_rejection():
    """Verify out-of-order or duplicate measurement frames are rejected."""
    vad = VADEngineState("s1")

    m0 = VADFrameMeasurement(sequence_id=5, monotonic_ns=500, speech_probability=0.8)
    vad.process_measurement(m0)

    # Older sequence ID 3 rejected
    m_old = VADFrameMeasurement(sequence_id=3, monotonic_ns=600, speech_probability=0.8)
    state, event = vad.process_measurement(m_old)
    assert event is None
    assert len(vad.get_history()) == 1


def test_vad_reset():
    """Verify resetting VAD engine returns to SILENCE and clears history."""
    vad = VADEngineState("s1")
    vad.process_measurement(VADFrameMeasurement(sequence_id=0, monotonic_ns=100, speech_probability=0.8))
    assert vad.current_state == VADState.POSSIBLE_SPEECH

    vad.reset()
    assert vad.current_state == VADState.SILENCE
    assert len(vad.get_history()) == 0


@pytest.mark.parametrize("field", ["energy_db", "frame_duration_ms"])
def test_vad_measurement_rejects_non_finite_values(field):
    kwargs = dict(sequence_id=0, monotonic_ns=100, speech_probability=0.8)
    kwargs[field] = float("nan")
    with pytest.raises(ValueError):
        VADFrameMeasurement(**kwargs)


def test_vad_history_is_bounded_and_interruption_exit_is_recorded():
    vad = VADEngineState(
        "s1",
        VADConfig(min_speech_frames=1, min_interruption_frames=1, max_history_events=2),
    )
    vad.process_measurement(VADFrameMeasurement(0, 100, 0.9))
    vad.process_measurement(
        VADFrameMeasurement(1, 200, 0.9), assistant_speaking=True
    )
    state, event = vad.process_measurement(VADFrameMeasurement(2, 300, 0.9))
    assert state == VADState.CONFIRMED_SPEECH
    assert event is not None
    assert event.reason == "interruption_candidate_continues_as_speech"
    assert len(vad.get_history()) == 2
