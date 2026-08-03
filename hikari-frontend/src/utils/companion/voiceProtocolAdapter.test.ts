import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  VOICE_PROTOCOL_VERSION,
  parseVoiceProtocolMessage,
  voiceEventSubmitIntent,
} from "./voiceProtocolAdapter";
import {
  createInitialVoiceSessionState,
  reduceVoiceSession,
} from "./voiceSession";

describe("voiceProtocolAdapter", () => {
  it("parses a versioned submit_turn and rejects unknown fields", () => {
    const ok = parseVoiceProtocolMessage({
      protocol_version: VOICE_PROTOCOL_VERSION,
      type: "submit_turn",
      session_id: "sess.alpha-1",
      event_seq: 3,
      utterance_id: "utt.1",
    });
    assert.equal(ok.ok, true);
    if (ok.ok) {
      assert.equal(ok.event.type, "submit_turn");
    }
    assert.equal(voiceEventSubmitIntent(ok), "submit_turn");

    const bad = parseVoiceProtocolMessage({
      protocol_version: VOICE_PROTOCOL_VERSION,
      type: "submit_turn",
      session_id: "sess.alpha-1",
      event_seq: 3,
      utterance_id: "utt.1",
      transcript: "secret text must not be trusted",
    });
    assert.equal(bad.ok, false);
    if (!bad.ok) {
      assert.equal(bad.noop, true);
      assert.equal(bad.code, "invalid_fields");
      assert.ok(!JSON.stringify(bad).includes("secret text"));
    }
  });

  it("fail-closes on invalid version, type, and bounds", () => {
    assert.equal(
      parseVoiceProtocolMessage({
        protocol_version: 99,
        type: "wake_detected",
        session_id: "sess.alpha-1",
        event_seq: 1,
      }).ok,
      false,
    );
    assert.equal(
      parseVoiceProtocolMessage({
        protocol_version: 1,
        type: "explode",
        session_id: "sess.alpha-1",
        event_seq: 1,
      }).ok,
      false,
    );
    assert.equal(
      parseVoiceProtocolMessage({
        protocol_version: 1,
        type: "wake_detected",
        session_id: "BAD ID",
        event_seq: 1,
      }).ok,
      false,
    );
  });

  it("partial_caption never produces submit-turn intent", () => {
    const parsed = parseVoiceProtocolMessage({
      protocol_version: 1,
      type: "partial_caption",
      session_id: "sess.alpha-1",
      event_seq: 2,
      utterance_id: "utt.1",
      caption_len: 20,
    });
    assert.equal(parsed.ok, true);
    assert.equal(voiceEventSubmitIntent(parsed), "none");

    // Reject transcript content if smuggled in.
    const smuggled = parseVoiceProtocolMessage({
      protocol_version: 1,
      type: "partial_caption",
      session_id: "sess.alpha-1",
      event_seq: 2,
      utterance_id: "utt.1",
      caption_len: 20,
      text: "should not persist",
    });
    assert.equal(smuggled.ok, false);

    let state = createInitialVoiceSessionState("sess.alpha-1");
    state = reduceVoiceSession(state, {
      type: "session_start",
      sessionId: "sess.alpha-1",
      eventSeq: 1,
    }).state;
    state = reduceVoiceSession(state, {
      type: "listening_started",
      sessionId: "sess.alpha-1",
      utteranceId: "utt.1",
      eventSeq: 2,
    }).state;
    if (parsed.ok) {
      const reduced = reduceVoiceSession(state, {
        ...parsed.event,
        eventSeq: 3,
      });
      assert.equal(reduced.accepted, true);
      assert.equal(reduced.effects.length, 0);
      assert.equal(reduced.state.name, "listening");
    }
  });

  it("does not persist response content fields", () => {
    const parsed = parseVoiceProtocolMessage({
      protocol_version: 1,
      type: "assistant_generating",
      session_id: "sess.alpha-1",
      event_seq: 1,
      utterance_id: "utt.1",
      response_id: "resp.1",
      response_text: "private answer",
    });
    assert.equal(parsed.ok, false);
    if (!parsed.ok) {
      assert.equal(parsed.code, "invalid_fields");
    }
  });

  it("parses session_reset and session_snapshot without content or submit", () => {
    const reset = parseVoiceProtocolMessage({
      protocol_version: 1,
      type: "session_reset",
      session_id: "sess.alpha-2",
      event_seq: 40,
    });
    assert.equal(reset.ok, true);
    assert.equal(voiceEventSubmitIntent(reset), "none");

    const snap = parseVoiceProtocolMessage({
      protocol_version: 1,
      type: "session_snapshot",
      session_id: "sess.alpha-1",
      event_seq: 40,
      snapshot: {
        name: "listening",
        utterance_id: "utt.9",
        response_id: "",
        playback_id: "",
        degraded_half_duplex: true,
        push_to_talk_fallback: true,
        last_error_code: "degraded_half_duplex",
      },
    });
    assert.equal(snap.ok, true);
    assert.equal(voiceEventSubmitIntent(snap), "none");
    if (snap.ok && snap.event.type === "session_snapshot") {
      assert.equal(snap.event.snapshot.name, "listening");
      assert.equal(snap.event.snapshot.utteranceId, "utt.9");
      assert.equal(snap.event.snapshot.degradedHalfDuplex, true);
    }

    const smuggled = parseVoiceProtocolMessage({
      protocol_version: 1,
      type: "session_snapshot",
      session_id: "sess.alpha-1",
      event_seq: 40,
      snapshot: {
        name: "listening",
        utterance_id: "utt.9",
        response_id: "",
        playback_id: "",
        degraded_half_duplex: false,
        push_to_talk_fallback: false,
        last_error_code: "",
        transcript: "must not persist",
      },
    });
    assert.equal(smuggled.ok, false);
    if (!smuggled.ok) {
      assert.equal(smuggled.code, "invalid_bounds");
      assert.ok(!JSON.stringify(smuggled).includes("must not persist"));
    }

    let state = createInitialVoiceSessionState("sess.alpha-1");
    state = reduceVoiceSession(state, {
      type: "session_start",
      sessionId: "sess.alpha-1",
      eventSeq: 1,
    }).state;
    if (snap.ok) {
      const reduced = reduceVoiceSession(state, snap.event);
      assert.equal(reduced.accepted, true);
      assert.equal(reduced.state.name, "listening");
      assert.equal(reduced.state.eventSeq, 40);
      assert.ok(
        reduced.effects.some((e) => e.kind === "show_push_to_talk_fallback"),
      );
      assert.ok(!reduced.effects.some((e) => e.kind === "start_or_continue_capture"));
    }
  });

  it("rejects unsafe event_seq in protocol parsing", () => {
    const base = {
      protocol_version: 1,
      type: "wake_detected",
      session_id: "sess.alpha-1",
    } as const;
    const badSeqs: unknown[] = [
      Number.MAX_SAFE_INTEGER + 1,
      Number.POSITIVE_INFINITY,
      Number.NaN,
      1.5,
      -3,
      "12",
      12n,
    ];
    for (const event_seq of badSeqs) {
      const parsed = parseVoiceProtocolMessage({ ...base, event_seq });
      assert.equal(parsed.ok, false);
      if (!parsed.ok) {
        assert.equal(parsed.noop, true);
        assert.equal(parsed.code, "invalid_bounds");
      }
    }
  });

});
