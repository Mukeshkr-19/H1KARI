/**
 * Pure voice-session reducer foundations.
 * No browser APIs, network, storage, or filesystem access.
 */

export const VOICE_SESSION_ID_MAX = 80;
export const VOICE_CORRELATION_ID_MAX = 80;
export const VOICE_CAPTION_MAX = 240;
/**
 * Maximum accepted event_seq. Capped at Number.MAX_SAFE_INTEGER - 1 so that
 * eventSeq + 1 comparisons remain exact (no precision loss).
 */
export const VOICE_EVENT_SEQ_MAX = Number.MAX_SAFE_INTEGER - 1;

export function isValidVoiceEventSeq(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= VOICE_EVENT_SEQ_MAX
  );
}

export const VOICE_STATES = [
  "asleep",
  "wake_detected",
  "awaiting_command",
  "listening",
  "user_speaking",
  "processing_final",
  "assistant_generating",
  "assistant_speaking",
  "barge_in_pending",
  "cancelling_assistant",
  "interrupted",
  "cancelled",
  "degraded_half_duplex",
  "microphone_error",
  "aec_error",
  "idle",
] as const;

export type VoiceSessionStateName = (typeof VOICE_STATES)[number];

const VOICE_STATE_SET = new Set<string>(VOICE_STATES);

export type VoiceEffectKind =
  | "cancel_browser_tts"
  | "stop_recognition"
  | "start_or_continue_capture"
  | "show_push_to_talk_fallback"
  | "clear_ephemeral_caption";

export type VoiceEffect = Readonly<{
  kind: VoiceEffectKind;
  sessionId: string;
  responseId?: string;
  playbackId?: string;
  utteranceId?: string;
}>;

export type VoiceSessionState = Readonly<{
  name: VoiceSessionStateName;
  sessionId: string;
  utteranceId: string;
  responseId: string;
  playbackId: string;
  eventSeq: number;
  degradedHalfDuplex: boolean;
  pushToTalkFallback: boolean;
  lastErrorCode: string;
}>;

export type VoiceReduceResult = Readonly<{
  state: VoiceSessionState;
  effects: ReadonlyArray<VoiceEffect>;
  accepted: boolean;
  rejectReason: string;
}>;

export type VoiceSessionSnapshotPayload = Readonly<{
  name: VoiceSessionStateName;
  utteranceId: string;
  responseId: string;
  playbackId: string;
  degradedHalfDuplex: boolean;
  pushToTalkFallback: boolean;
  lastErrorCode: string;
}>;

export type VoiceSessionEvent =
  | Readonly<{ type: "session_start"; sessionId: string; eventSeq: number }>
  | Readonly<{ type: "session_reset"; sessionId: string; eventSeq: number }>
  | Readonly<{
      type: "session_snapshot";
      sessionId: string;
      eventSeq: number;
      snapshot: VoiceSessionSnapshotPayload;
    }>
  | Readonly<{ type: "wake_detected"; sessionId: string; eventSeq: number }>
  | Readonly<{ type: "await_command"; sessionId: string; eventSeq: number }>
  | Readonly<{
      type: "listening_started";
      sessionId: string;
      utteranceId: string;
      eventSeq: number;
    }>
  | Readonly<{
      type: "user_speech_activity";
      sessionId: string;
      utteranceId: string;
      eventSeq: number;
    }>
  | Readonly<{
      type: "final_transcript_ready";
      sessionId: string;
      utteranceId: string;
      eventSeq: number;
    }>
  | Readonly<{
      type: "submit_turn";
      sessionId: string;
      utteranceId: string;
      eventSeq: number;
    }>
  | Readonly<{
      type: "assistant_generating";
      sessionId: string;
      utteranceId: string;
      responseId: string;
      eventSeq: number;
    }>
  | Readonly<{
      type: "assistant_speaking";
      sessionId: string;
      responseId: string;
      playbackId: string;
      eventSeq: number;
    }>
  | Readonly<{
      type: "barge_in";
      sessionId: string;
      responseId: string;
      playbackId: string;
      eventSeq: number;
    }>
  | Readonly<{
      type: "cancel_assistant";
      sessionId: string;
      responseId: string;
      playbackId: string;
      eventSeq: number;
    }>
  | Readonly<{
      type: "assistant_cancelled";
      sessionId: string;
      responseId: string;
      eventSeq: number;
    }>
  | Readonly<{
      type: "interrupted_ack";
      sessionId: string;
      responseId: string;
      eventSeq: number;
    }>
  | Readonly<{ type: "enter_idle"; sessionId: string; eventSeq: number }>
  | Readonly<{ type: "enter_asleep"; sessionId: string; eventSeq: number }>
  | Readonly<{
      type: "degraded_half_duplex";
      sessionId: string;
      eventSeq: number;
    }>
  | Readonly<{
      type: "microphone_error";
      sessionId: string;
      eventSeq: number;
      code?: string;
    }>
  | Readonly<{
      type: "aec_error";
      sessionId: string;
      eventSeq: number;
      code?: string;
    }>
  | Readonly<{
      type: "partial_caption";
      sessionId: string;
      utteranceId: string;
      eventSeq: number;
      captionLen: number;
    }>
  | Readonly<{ type: "clear_caption"; sessionId: string; eventSeq: number }>;

const CANONICAL_ID_RE = /^[a-z0-9][a-z0-9_.-]{0,79}$/;

const TERMINAL_CANCEL_STATES = new Set<VoiceSessionStateName>([
  "cancelled",
  "interrupted",
  "cancelling_assistant",
]);

const ACTIVE_RESPONSE_STATES = new Set<VoiceSessionStateName>([
  "assistant_generating",
  "assistant_speaking",
  "barge_in_pending",
]);

export const VOICE_A11Y_LABELS = Object.freeze({
  asleep: "Voice companion asleep",
  wake_detected: "Wake word detected",
  awaiting_command: "Awaiting voice command",
  listening: "Listening",
  user_speaking: "User speaking",
  processing_final: "Processing final transcript",
  assistant_generating: "Assistant generating response",
  assistant_speaking: "Assistant speaking",
  barge_in_pending: "Barge-in pending",
  cancelling_assistant: "Cancelling assistant speech",
  interrupted: "Assistant interrupted",
  cancelled: "Assistant cancelled",
  degraded_half_duplex: "Degraded half-duplex mode",
  microphone_error: "Microphone error",
  aec_error: "Echo cancellation error",
  idle: "Voice companion idle",
} as const);

export const VOICE_ERROR_DESCRIPTIONS = Object.freeze({
  microphone_error: "Microphone access failed. Use push-to-talk if available.",
  aec_error: "Echo cancellation failed. Switching to half-duplex fallback.",
  degraded_half_duplex: "Full duplex unavailable. Push-to-talk fallback is active.",
  stale_event: "Ignored stale or out-of-order voice event.",
  cross_session: "Ignored cross-session voice event.",
  duplicate_event: "Ignored duplicate voice event.",
} as const);

export function isValidVoiceCorrelationId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= VOICE_CORRELATION_ID_MAX &&
    CANONICAL_ID_RE.test(value)
  );
}

export function isValidOptionalCorrelationId(value: unknown): value is string {
  return value === "" || isValidVoiceCorrelationId(value);
}

export function isVoiceSessionStateName(
  value: unknown,
): value is VoiceSessionStateName {
  return typeof value === "string" && VOICE_STATE_SET.has(value);
}

export function createInitialVoiceSessionState(
  sessionId: string = "sess.local-1",
): VoiceSessionState {
  const id = isValidVoiceCorrelationId(sessionId) ? sessionId : "sess.local-1";
  return {
    name: "asleep",
    sessionId: id,
    utteranceId: "",
    responseId: "",
    playbackId: "",
    eventSeq: 0,
    degradedHalfDuplex: false,
    pushToTalkFallback: false,
    lastErrorCode: "",
  };
}

function reject(
  state: VoiceSessionState,
  reason: string,
): VoiceReduceResult {
  return { state, effects: [], accepted: false, rejectReason: reason };
}

function accept(
  state: VoiceSessionState,
  next: VoiceSessionState,
  effects: ReadonlyArray<VoiceEffect> = [],
): VoiceReduceResult {
  return { state: next, effects, accepted: true, rejectReason: "" };
}

function withSeq(state: VoiceSessionState, eventSeq: number): VoiceSessionState {
  return { ...state, eventSeq };
}

export function accessibleLabelFor(stateName: VoiceSessionStateName): string {
  return VOICE_A11Y_LABELS[stateName];
}

function isAuthoritativeBootstrap(
  type: VoiceSessionEvent["type"],
): boolean {
  // session_start / session_reset are the only cross-session authorities.
  // session_snapshot recovers same-session gaps (or bounded idle bootstrap).
  return type === "session_start" || type === "session_reset";
}

function isBoundedSnapshotBootstrap(
  state: VoiceSessionState,
  eventSessionId: string,
): boolean {
  // Fresh idle/asleep at seq 0 may accept an explicit snapshot for a new session.
  if (state.eventSeq !== 0) {
    return false;
  }
  if (state.name !== "asleep" && state.name !== "idle") {
    return false;
  }
  return isValidVoiceCorrelationId(eventSessionId);
}

function sanitizeSnapshotPayload(
  snapshot: VoiceSessionSnapshotPayload,
): VoiceSessionSnapshotPayload | null {
  if (!isVoiceSessionStateName(snapshot.name)) {
    return null;
  }
  if (
    !isValidOptionalCorrelationId(snapshot.utteranceId) ||
    !isValidOptionalCorrelationId(snapshot.responseId) ||
    !isValidOptionalCorrelationId(snapshot.playbackId)
  ) {
    return null;
  }
  if (typeof snapshot.degradedHalfDuplex !== "boolean") {
    return null;
  }
  if (typeof snapshot.pushToTalkFallback !== "boolean") {
    return null;
  }
  if (
    typeof snapshot.lastErrorCode !== "string" ||
    snapshot.lastErrorCode.length > 64 ||
    (snapshot.lastErrorCode !== "" &&
      !/^[a-z0-9_.-]+$/.test(snapshot.lastErrorCode))
  ) {
    return null;
  }

  let responseId = snapshot.responseId;
  let playbackId = snapshot.playbackId;

  // Cancelled / interrupted / cancelling snapshots never revive playback.
  if (TERMINAL_CANCEL_STATES.has(snapshot.name)) {
    responseId = snapshot.name === "cancelling_assistant" ? responseId : "";
    playbackId = "";
  }

  // Active response states require a response id; speaking needs playback.
  if (ACTIVE_RESPONSE_STATES.has(snapshot.name) && !responseId) {
    return null;
  }
  if (snapshot.name === "assistant_speaking" && !playbackId) {
    return null;
  }

  return {
    name: snapshot.name,
    utteranceId: snapshot.utteranceId,
    responseId,
    playbackId,
    degradedHalfDuplex: snapshot.degradedHalfDuplex,
    pushToTalkFallback: snapshot.pushToTalkFallback,
    lastErrorCode: snapshot.lastErrorCode,
  };
}

function wouldReviveCancelled(
  state: VoiceSessionState,
  snapshot: VoiceSessionSnapshotPayload,
): boolean {
  if (!TERMINAL_CANCEL_STATES.has(state.name)) {
    return false;
  }
  if (!state.responseId) {
    return false;
  }
  if (!ACTIVE_RESPONSE_STATES.has(snapshot.name)) {
    return false;
  }
  return snapshot.responseId === state.responseId;
}

export function reduceVoiceSession(
  state: VoiceSessionState,
  event: VoiceSessionEvent,
): VoiceReduceResult {
  if (!isValidVoiceCorrelationId(event.sessionId)) {
    return reject(state, "invalid_session_id");
  }

  if (!isAuthoritativeBootstrap(event.type) && event.sessionId !== state.sessionId) {
    if (
      event.type === "session_snapshot" &&
      isBoundedSnapshotBootstrap(state, event.sessionId)
    ) {
      // Bounded explicit bootstrap only.
    } else {
      return reject(state, "cross_session");
    }
  }

  if (!isValidVoiceEventSeq(event.eventSeq)) {
    return reject(state, "invalid_event_seq");
  }

  if (event.type === "session_snapshot") {
    // Snapshot may close a sequence gap; reject only stale/duplicate same-session.
    if (event.sessionId === state.sessionId && event.eventSeq <= state.eventSeq) {
      return reject(state, "stale_snapshot");
    }
  } else if (!isAuthoritativeBootstrap(event.type)) {
    if (event.eventSeq < state.eventSeq) {
      return reject(state, "stale_event");
    }
    if (event.eventSeq === state.eventSeq) {
      return reject(state, "duplicate_event");
    }
    // Out-of-order gap: require strictly monotonic +1 for ordinary transitions.
    if (event.eventSeq !== state.eventSeq + 1) {
      return reject(state, "out_of_order");
    }
  }

  switch (event.type) {
    case "session_start": {
      if (event.sessionId === state.sessionId && event.eventSeq <= state.eventSeq) {
        return reject(state, "stale_session_start");
      }
      const next: VoiceSessionState = {
        ...createInitialVoiceSessionState(event.sessionId),
        name: "idle",
        eventSeq: event.eventSeq,
      };
      return accept(state, next, [
        {
          kind: "clear_ephemeral_caption",
          sessionId: event.sessionId,
        },
      ]);
    }
    case "session_reset": {
      // Canonical newer session after reconnect: complete bounded idle state.
      // Never submits a turn; never persists transcript/response content.
      if (event.sessionId === state.sessionId && event.eventSeq <= state.eventSeq) {
        return reject(state, "stale_reset");
      }
      const next: VoiceSessionState = {
        ...createInitialVoiceSessionState(event.sessionId),
        name: "idle",
        eventSeq: event.eventSeq,
      };
      return accept(state, next, [
        {
          kind: "clear_ephemeral_caption",
          sessionId: event.sessionId,
        },
        {
          kind: "stop_recognition",
          sessionId: event.sessionId,
        },
      ]);
    }
    case "session_snapshot": {
      // Recovery across reconnect / sequence gap with complete bounded state.
      // Cross-session authority requires reset/start or bounded idle bootstrap.
      const sanitized = sanitizeSnapshotPayload(event.snapshot);
      if (!sanitized) {
        return reject(state, "invalid_snapshot");
      }
      if (wouldReviveCancelled(state, sanitized)) {
        return reject(state, "revive_cancelled");
      }
      const next: VoiceSessionState = {
        name: sanitized.name,
        sessionId: event.sessionId,
        utteranceId: sanitized.utteranceId,
        responseId: sanitized.responseId,
        playbackId: sanitized.playbackId,
        eventSeq: event.eventSeq,
        degradedHalfDuplex: sanitized.degradedHalfDuplex,
        pushToTalkFallback: sanitized.pushToTalkFallback,
        lastErrorCode: sanitized.lastErrorCode,
      };
      const effects: VoiceEffect[] = [
        {
          kind: "clear_ephemeral_caption",
          sessionId: event.sessionId,
        },
      ];
      if (TERMINAL_CANCEL_STATES.has(sanitized.name) && state.playbackId) {
        effects.push({
          kind: "cancel_browser_tts",
          sessionId: event.sessionId,
          responseId: state.responseId || sanitized.responseId || undefined,
          playbackId: state.playbackId,
        });
      }
      if (sanitized.pushToTalkFallback || sanitized.degradedHalfDuplex) {
        effects.push({
          kind: "show_push_to_talk_fallback",
          sessionId: event.sessionId,
        });
      }
      return accept(state, next, effects);
    }
    case "wake_detected":
      return accept(state, {
        ...withSeq(state, event.eventSeq),
        name: "wake_detected",
      });
    case "await_command":
      return accept(state, {
        ...withSeq(state, event.eventSeq),
        name: "awaiting_command",
      });
    case "listening_started": {
      if (!isValidVoiceCorrelationId(event.utteranceId)) {
        return reject(state, "invalid_utterance_id");
      }
      return accept(
        state,
        {
          ...withSeq(state, event.eventSeq),
          name: "listening",
          utteranceId: event.utteranceId,
          responseId: "",
          playbackId: "",
        },
        [
          {
            kind: "start_or_continue_capture",
            sessionId: state.sessionId,
            utteranceId: event.utteranceId,
          },
        ],
      );
    }
    case "user_speech_activity": {
      if (event.utteranceId !== state.utteranceId) {
        return reject(state, "stale_utterance");
      }
      return accept(state, {
        ...withSeq(state, event.eventSeq),
        name: "user_speaking",
      });
    }
    case "final_transcript_ready": {
      if (event.utteranceId !== state.utteranceId) {
        return reject(state, "stale_utterance");
      }
      return accept(
        state,
        {
          ...withSeq(state, event.eventSeq),
          name: "processing_final",
        },
        [
          {
            kind: "stop_recognition",
            sessionId: state.sessionId,
            utteranceId: event.utteranceId,
          },
        ],
      );
    }
    case "submit_turn": {
      if (event.utteranceId !== state.utteranceId) {
        return reject(state, "stale_utterance");
      }
      return accept(state, {
        ...withSeq(state, event.eventSeq),
        name: "processing_final",
      });
    }
    case "partial_caption": {
      // Partial captions never submit a turn; state seq advances only to track
      // monotonicity, authoritative name unchanged unless listening/speaking.
      if (event.utteranceId !== state.utteranceId) {
        return reject(state, "stale_utterance");
      }
      if (event.captionLen < 0 || event.captionLen > VOICE_CAPTION_MAX) {
        return reject(state, "caption_bounds");
      }
      return accept(state, withSeq(state, event.eventSeq), []);
    }
    case "clear_caption":
      return accept(state, withSeq(state, event.eventSeq), [
        { kind: "clear_ephemeral_caption", sessionId: state.sessionId },
      ]);
    case "assistant_generating": {
      if (
        !isValidVoiceCorrelationId(event.responseId) ||
        event.utteranceId !== state.utteranceId
      ) {
        return reject(state, "invalid_response_correlation");
      }
      return accept(state, {
        ...withSeq(state, event.eventSeq),
        name: "assistant_generating",
        responseId: event.responseId,
      });
    }
    case "assistant_speaking": {
      if (
        event.responseId !== state.responseId ||
        !isValidVoiceCorrelationId(event.playbackId)
      ) {
        return reject(state, "stale_response");
      }
      return accept(state, {
        ...withSeq(state, event.eventSeq),
        name: "assistant_speaking",
        playbackId: event.playbackId,
      });
    }
    case "barge_in": {
      if (
        event.responseId !== state.responseId ||
        event.playbackId !== state.playbackId
      ) {
        return reject(state, "stale_response");
      }
      return accept(
        state,
        {
          ...withSeq(state, event.eventSeq),
          name: "barge_in_pending",
        },
        [
          {
            kind: "cancel_browser_tts",
            sessionId: state.sessionId,
            responseId: state.responseId,
            playbackId: state.playbackId,
          },
          {
            kind: "stop_recognition",
            sessionId: state.sessionId,
          },
        ],
      );
    }
    case "cancel_assistant": {
      if (event.responseId !== state.responseId) {
        return reject(state, "stale_response");
      }
      return accept(
        state,
        {
          ...withSeq(state, event.eventSeq),
          name: "cancelling_assistant",
        },
        [
          {
            kind: "cancel_browser_tts",
            sessionId: state.sessionId,
            responseId: state.responseId,
            playbackId: state.playbackId || event.playbackId,
          },
        ],
      );
    }
    case "assistant_cancelled": {
      if (event.responseId !== state.responseId) {
        return reject(state, "stale_response");
      }
      return accept(state, {
        ...withSeq(state, event.eventSeq),
        name: "cancelled",
        playbackId: "",
      });
    }
    case "interrupted_ack": {
      if (event.responseId !== state.responseId) {
        return reject(state, "stale_response");
      }
      return accept(state, {
        ...withSeq(state, event.eventSeq),
        name: "interrupted",
        playbackId: "",
      });
    }
    case "degraded_half_duplex":
      return accept(
        state,
        {
          ...withSeq(state, event.eventSeq),
          name: "degraded_half_duplex",
          degradedHalfDuplex: true,
          pushToTalkFallback: true,
          lastErrorCode: "degraded_half_duplex",
        },
        [
          {
            kind: "show_push_to_talk_fallback",
            sessionId: state.sessionId,
          },
        ],
      );
    case "microphone_error":
      return accept(
        state,
        {
          ...withSeq(state, event.eventSeq),
          name: "microphone_error",
          pushToTalkFallback: true,
          lastErrorCode: event.code || "microphone_error",
        },
        [
          {
            kind: "show_push_to_talk_fallback",
            sessionId: state.sessionId,
          },
          {
            kind: "stop_recognition",
            sessionId: state.sessionId,
          },
        ],
      );
    case "aec_error":
      return accept(
        state,
        {
          ...withSeq(state, event.eventSeq),
          name: "aec_error",
          degradedHalfDuplex: true,
          pushToTalkFallback: true,
          lastErrorCode: event.code || "aec_error",
        },
        [
          {
            kind: "show_push_to_talk_fallback",
            sessionId: state.sessionId,
          },
        ],
      );
    case "enter_idle":
      return accept(state, {
        ...withSeq(state, event.eventSeq),
        name: "idle",
        utteranceId: "",
        responseId: "",
        playbackId: "",
      });
    case "enter_asleep":
      return accept(state, {
        ...withSeq(state, event.eventSeq),
        name: "asleep",
        utteranceId: "",
        responseId: "",
        playbackId: "",
      });
    default:
      return reject(state, "unknown_event");
  }
}
