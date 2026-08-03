import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  VOICE_A11Y_LABELS,
  VOICE_ERROR_DESCRIPTIONS,
  VOICE_STATES,
  accessibleLabelFor,
  createInitialVoiceSessionState,
  reduceVoiceSession,
  type VoiceSessionEvent,
  type VoiceSessionState,
} from "./voiceSession";

const SESSION = "sess.alpha-1";
const SESSION_NEW = "sess.alpha-2";

function apply(
  state: VoiceSessionState,
  events: VoiceSessionEvent[],
): VoiceSessionState {
  let current = state;
  for (const event of events) {
    const result = reduceVoiceSession(current, event);
    assert.equal(result.accepted, true, result.rejectReason);
    current = result.state;
  }
  return current;
}

describe("voiceSession reducer", () => {
  it("models every required state and accessible label", () => {
    for (const name of VOICE_STATES) {
      assert.ok(VOICE_A11Y_LABELS[name]);
      assert.equal(accessibleLabelFor(name), VOICE_A11Y_LABELS[name]);
    }
    assert.ok(VOICE_ERROR_DESCRIPTIONS.microphone_error);
    assert.ok(VOICE_ERROR_DESCRIPTIONS.degraded_half_duplex);
  });

  it("rejects cross-session, stale, duplicate, and out-of-order events", () => {
    let state = createInitialVoiceSessionState(SESSION);
    state = apply(state, [
      { type: "session_start", sessionId: SESSION, eventSeq: 1 },
      { type: "wake_detected", sessionId: SESSION, eventSeq: 2 },
    ]);

    const cross = reduceVoiceSession(state, {
      type: "await_command",
      sessionId: "sess.other-9",
      eventSeq: 3,
    });
    assert.equal(cross.accepted, false);
    assert.equal(cross.rejectReason, "cross_session");
    assert.equal(cross.state.name, state.name);
    assert.equal(cross.state.eventSeq, state.eventSeq);

    const stale = reduceVoiceSession(state, {
      type: "await_command",
      sessionId: SESSION,
      eventSeq: 1,
    });
    assert.equal(stale.accepted, false);
    assert.equal(stale.rejectReason, "stale_event");
    assert.deepEqual(stale.state, state);

    const dup = reduceVoiceSession(state, {
      type: "await_command",
      sessionId: SESSION,
      eventSeq: 2,
    });
    assert.equal(dup.accepted, false);
    assert.equal(dup.rejectReason, "duplicate_event");

    const gap = reduceVoiceSession(state, {
      type: "await_command",
      sessionId: SESSION,
      eventSeq: 4,
    });
    assert.equal(gap.accepted, false);
    assert.equal(gap.rejectReason, "out_of_order");
    assert.deepEqual(gap.state, state);

    const replayedStart = reduceVoiceSession(state, {
      type: "session_start",
      sessionId: SESSION,
      eventSeq: 1,
    });
    assert.equal(replayedStart.accepted, false);
    assert.equal(replayedStart.rejectReason, "stale_session_start");
    assert.deepEqual(replayedStart.state, state);
  });

  it("emits cancel_browser_tts on correlated barge-in and cancel", () => {
    let state = createInitialVoiceSessionState(SESSION);
    state = apply(state, [
      { type: "session_start", sessionId: SESSION, eventSeq: 1 },
      { type: "wake_detected", sessionId: SESSION, eventSeq: 2 },
      { type: "await_command", sessionId: SESSION, eventSeq: 3 },
      {
        type: "listening_started",
        sessionId: SESSION,
        utteranceId: "utt.1",
        eventSeq: 4,
      },
      {
        type: "user_speech_activity",
        sessionId: SESSION,
        utteranceId: "utt.1",
        eventSeq: 5,
      },
      {
        type: "final_transcript_ready",
        sessionId: SESSION,
        utteranceId: "utt.1",
        eventSeq: 6,
      },
      {
        type: "submit_turn",
        sessionId: SESSION,
        utteranceId: "utt.1",
        eventSeq: 7,
      },
      {
        type: "assistant_generating",
        sessionId: SESSION,
        utteranceId: "utt.1",
        responseId: "resp.1",
        eventSeq: 8,
      },
      {
        type: "assistant_speaking",
        sessionId: SESSION,
        responseId: "resp.1",
        playbackId: "play.1",
        eventSeq: 9,
      },
    ]);

    const barge = reduceVoiceSession(state, {
      type: "barge_in",
      sessionId: SESSION,
      responseId: "resp.1",
      playbackId: "play.1",
      eventSeq: 10,
    });
    assert.equal(barge.accepted, true);
    assert.equal(barge.state.name, "barge_in_pending");
    assert.ok(
      barge.effects.some(
        (e) =>
          e.kind === "cancel_browser_tts" &&
          e.responseId === "resp.1" &&
          e.playbackId === "play.1",
      ),
    );

    state = barge.state;
    const cancel = reduceVoiceSession(state, {
      type: "cancel_assistant",
      sessionId: SESSION,
      responseId: "resp.1",
      playbackId: "play.1",
      eventSeq: 11,
    });
    assert.equal(cancel.state.name, "cancelling_assistant");
    assert.ok(
      cancel.effects.some(
        (e) => e.kind === "cancel_browser_tts" && e.responseId === "resp.1",
      ),
    );
  });

  it("does not emit submit effects for partial captions", () => {
    let state = createInitialVoiceSessionState(SESSION);
    state = apply(state, [
      { type: "session_start", sessionId: SESSION, eventSeq: 1 },
      {
        type: "listening_started",
        sessionId: SESSION,
        utteranceId: "utt.1",
        eventSeq: 2,
      },
    ]);
    const partial = reduceVoiceSession(state, {
      type: "partial_caption",
      sessionId: SESSION,
      utteranceId: "utt.1",
      eventSeq: 3,
      captionLen: 12,
    });
    assert.equal(partial.accepted, true);
    assert.equal(partial.state.name, "listening");
    assert.equal(partial.effects.length, 0);
    assert.ok(!partial.effects.some((e) => (e as { kind: string }).kind === "submit_turn"));
  });

  it("represents degraded half-duplex with push-to-talk fallback", () => {
    let state = createInitialVoiceSessionState(SESSION);
    state = apply(state, [
      { type: "session_start", sessionId: SESSION, eventSeq: 1 },
    ]);
    const degraded = reduceVoiceSession(state, {
      type: "degraded_half_duplex",
      sessionId: SESSION,
      eventSeq: 2,
    });
    assert.equal(degraded.state.name, "degraded_half_duplex");
    assert.equal(degraded.state.degradedHalfDuplex, true);
    assert.equal(degraded.state.pushToTalkFallback, true);
    assert.ok(
      degraded.effects.some((e) => e.kind === "show_push_to_talk_fallback"),
    );
  });

  it("rejects stale response cancellation without changing state", () => {
    let state = createInitialVoiceSessionState(SESSION);
    state = apply(state, [
      { type: "session_start", sessionId: SESSION, eventSeq: 1 },
      {
        type: "listening_started",
        sessionId: SESSION,
        utteranceId: "utt.1",
        eventSeq: 2,
      },
      {
        type: "assistant_generating",
        sessionId: SESSION,
        utteranceId: "utt.1",
        responseId: "resp.1",
        eventSeq: 3,
      },
      {
        type: "assistant_speaking",
        sessionId: SESSION,
        responseId: "resp.1",
        playbackId: "play.1",
        eventSeq: 4,
      },
    ]);
    const before = state;
    const staleCancel = reduceVoiceSession(state, {
      type: "cancel_assistant",
      sessionId: SESSION,
      responseId: "resp.other",
      playbackId: "play.1",
      eventSeq: 5,
    });
    assert.equal(staleCancel.accepted, false);
    assert.deepEqual(staleCancel.state, before);
    assert.equal(staleCancel.effects.length, 0);
  });

  it("recovers via session_reset to a canonical newer session", () => {
    let state = createInitialVoiceSessionState(SESSION);
    state = apply(state, [
      { type: "session_start", sessionId: SESSION, eventSeq: 1 },
      {
        type: "listening_started",
        sessionId: SESSION,
        utteranceId: "utt.1",
        eventSeq: 2,
      },
      {
        type: "assistant_generating",
        sessionId: SESSION,
        utteranceId: "utt.1",
        responseId: "resp.1",
        eventSeq: 3,
      },
    ]);

    const reset = reduceVoiceSession(state, {
      type: "session_reset",
      sessionId: SESSION_NEW,
      eventSeq: 10,
    });
    assert.equal(reset.accepted, true);
    assert.equal(reset.state.sessionId, SESSION_NEW);
    assert.equal(reset.state.name, "idle");
    assert.equal(reset.state.eventSeq, 10);
    assert.equal(reset.state.utteranceId, "");
    assert.equal(reset.state.responseId, "");
    assert.equal(reset.state.playbackId, "");
    assert.ok(reset.effects.some((e) => e.kind === "clear_ephemeral_caption"));
    assert.ok(!reset.effects.some((e) => e.kind === "start_or_continue_capture"));
  });

  it("recovers sequence gaps only with validated session_snapshot", () => {
    let state = createInitialVoiceSessionState(SESSION);
    state = apply(state, [
      { type: "session_start", sessionId: SESSION, eventSeq: 1 },
      { type: "wake_detected", sessionId: SESSION, eventSeq: 2 },
    ]);

    const gap = reduceVoiceSession(state, {
      type: "await_command",
      sessionId: SESSION,
      eventSeq: 9,
    });
    assert.equal(gap.accepted, false);
    assert.equal(gap.rejectReason, "out_of_order");

    const snap = reduceVoiceSession(state, {
      type: "session_snapshot",
      sessionId: SESSION,
      eventSeq: 9,
      snapshot: {
        name: "awaiting_command",
        utteranceId: "",
        responseId: "",
        playbackId: "",
        degradedHalfDuplex: false,
        pushToTalkFallback: false,
        lastErrorCode: "",
      },
    });
    assert.equal(snap.accepted, true);
    assert.equal(snap.state.name, "awaiting_command");
    assert.equal(snap.state.eventSeq, 9);
    assert.ok(!snap.effects.some((e) => (e as { kind: string }).kind === "submit_turn"));
  });

  it("does not revive cancelled response or playback via snapshot", () => {
    let state = createInitialVoiceSessionState(SESSION);
    state = apply(state, [
      { type: "session_start", sessionId: SESSION, eventSeq: 1 },
      {
        type: "listening_started",
        sessionId: SESSION,
        utteranceId: "utt.1",
        eventSeq: 2,
      },
      {
        type: "assistant_generating",
        sessionId: SESSION,
        utteranceId: "utt.1",
        responseId: "resp.1",
        eventSeq: 3,
      },
      {
        type: "assistant_speaking",
        sessionId: SESSION,
        responseId: "resp.1",
        playbackId: "play.1",
        eventSeq: 4,
      },
      {
        type: "cancel_assistant",
        sessionId: SESSION,
        responseId: "resp.1",
        playbackId: "play.1",
        eventSeq: 5,
      },
      {
        type: "assistant_cancelled",
        sessionId: SESSION,
        responseId: "resp.1",
        eventSeq: 6,
      },
    ]);
    assert.equal(state.name, "cancelled");
    assert.equal(state.responseId, "resp.1");
    assert.equal(state.playbackId, "");

    const revive = reduceVoiceSession(state, {
      type: "session_snapshot",
      sessionId: SESSION,
      eventSeq: 20,
      snapshot: {
        name: "assistant_speaking",
        utteranceId: "utt.1",
        responseId: "resp.1",
        playbackId: "play.1",
        degradedHalfDuplex: false,
        pushToTalkFallback: false,
        lastErrorCode: "",
      },
    });
    assert.equal(revive.accepted, false);
    assert.equal(revive.rejectReason, "revive_cancelled");

    const cancelledSnap = reduceVoiceSession(state, {
      type: "session_snapshot",
      sessionId: SESSION,
      eventSeq: 21,
      snapshot: {
        name: "cancelled",
        utteranceId: "utt.1",
        responseId: "resp.1",
        playbackId: "play.9",
        degradedHalfDuplex: false,
        pushToTalkFallback: false,
        lastErrorCode: "",
      },
    });
    assert.equal(cancelledSnap.accepted, true);
    assert.equal(cancelledSnap.state.name, "cancelled");
    assert.equal(cancelledSnap.state.responseId, "");
    assert.equal(cancelledSnap.state.playbackId, "");
  });
  it("rejects arbitrary cross-session snapshot without reset/start", () => {
    let state = createInitialVoiceSessionState(SESSION);
    state = apply(state, [
      { type: "session_start", sessionId: SESSION, eventSeq: 1 },
      { type: "wake_detected", sessionId: SESSION, eventSeq: 2 },
    ]);

    const crossSnap = reduceVoiceSession(state, {
      type: "session_snapshot",
      sessionId: SESSION_NEW,
      eventSeq: 50,
      snapshot: {
        name: "awaiting_command",
        utteranceId: "",
        responseId: "",
        playbackId: "",
        degradedHalfDuplex: false,
        pushToTalkFallback: false,
        lastErrorCode: "",
      },
    });
    assert.equal(crossSnap.accepted, false);
    assert.equal(crossSnap.rejectReason, "cross_session");
    assert.deepEqual(crossSnap.state, state);

    // Reset establishes new session authority, then same-session snapshot works.
    const reset = reduceVoiceSession(state, {
      type: "session_reset",
      sessionId: SESSION_NEW,
      eventSeq: 10,
    });
    assert.equal(reset.accepted, true);
    const afterReset = reduceVoiceSession(reset.state, {
      type: "session_snapshot",
      sessionId: SESSION_NEW,
      eventSeq: 20,
      snapshot: {
        name: "listening",
        utteranceId: "utt.9",
        responseId: "",
        playbackId: "",
        degradedHalfDuplex: false,
        pushToTalkFallback: false,
        lastErrorCode: "",
      },
    });
    assert.equal(afterReset.accepted, true);
    assert.equal(afterReset.state.sessionId, SESSION_NEW);
    assert.equal(afterReset.state.name, "listening");
  });

  it("allows bounded idle bootstrap snapshot only at seq 0", () => {
    const fresh = createInitialVoiceSessionState(SESSION);
    const boot = reduceVoiceSession(fresh, {
      type: "session_snapshot",
      sessionId: SESSION_NEW,
      eventSeq: 1,
      snapshot: {
        name: "idle",
        utteranceId: "",
        responseId: "",
        playbackId: "",
        degradedHalfDuplex: false,
        pushToTalkFallback: false,
        lastErrorCode: "",
      },
    });
    assert.equal(boot.accepted, true);
    assert.equal(boot.state.sessionId, SESSION_NEW);
  });


  it("rejects unsafe event_seq values without changing state", () => {
    let state = createInitialVoiceSessionState(SESSION);
    state = apply(state, [
      { type: "session_start", sessionId: SESSION, eventSeq: 1 },
      { type: "wake_detected", sessionId: SESSION, eventSeq: 2 },
    ]);
    const before = structuredClone
      ? structuredClone(state)
      : JSON.parse(JSON.stringify(state));

    const cases: Array<{ eventSeq: number; label: string }> = [
      { eventSeq: Number.MAX_SAFE_INTEGER + 1, label: "max_safe_plus_one" },
      { eventSeq: Number.POSITIVE_INFINITY, label: "infinity" },
      { eventSeq: Number.NaN, label: "nan" },
      { eventSeq: 2.5, label: "fractional" },
      { eventSeq: -1, label: "negative" },
    ];
    for (const c of cases) {
      const result = reduceVoiceSession(state, {
        type: "await_command",
        sessionId: SESSION,
        eventSeq: c.eventSeq,
      });
      assert.equal(result.accepted, false, c.label);
      assert.equal(result.rejectReason, "invalid_event_seq", c.label);
      assert.deepEqual(result.state, before, c.label);
      assert.equal(result.effects.length, 0, c.label);
    }
  });

});
