/**
 * Versioned voice protocol adapter — fail-closed, no content persistence.
 * Accepts generic objects; validates exact allowed fields/types/bounds.
 */

import {
  VOICE_CAPTION_MAX,
  isValidOptionalCorrelationId,
  isValidVoiceCorrelationId,
  isValidVoiceEventSeq,
  isVoiceSessionStateName,
  type VoiceSessionEvent,
  type VoiceSessionSnapshotPayload,
} from "./voiceSession";

export const VOICE_PROTOCOL_VERSION = 1 as const;

export type VoiceProtocolErrorCode =
  | "invalid_envelope"
  | "unsupported_version"
  | "unknown_type"
  | "invalid_fields"
  | "invalid_bounds";

export type VoiceProtocolParseResult =
  | Readonly<{ ok: true; event: VoiceSessionEvent }>
  | Readonly<{
      ok: false;
      code: VoiceProtocolErrorCode;
      message: string;
      noop: true;
    }>;

const EVENT_TYPES = new Set<string>([
  "session_start",
  "session_reset",
  "session_snapshot",
  "wake_detected",
  "await_command",
  "listening_started",
  "user_speech_activity",
  "final_transcript_ready",
  "submit_turn",
  "assistant_generating",
  "assistant_speaking",
  "barge_in",
  "cancel_assistant",
  "assistant_cancelled",
  "interrupted_ack",
  "enter_idle",
  "enter_asleep",
  "degraded_half_duplex",
  "microphone_error",
  "aec_error",
  "partial_caption",
  "clear_caption",
]);

function fail(
  code: VoiceProtocolErrorCode,
  message: string,
): VoiceProtocolParseResult {
  return { ok: false, code, message, noop: true };
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(
  obj: Record<string, unknown>,
  allowed: ReadonlySet<string>,
): boolean {
  for (const key of Object.keys(obj)) {
    if (!allowed.has(key)) {
      return false;
    }
  }
  return true;
}

function isNonNegInt(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function requireId(value: unknown): string | null {
  return isValidVoiceCorrelationId(value) ? value : null;
}

function parseOptionalId(value: unknown): string | null {
  if (value === undefined) {
    return "";
  }
  return isValidOptionalCorrelationId(value) ? value : null;
}

function parseSnapshotPayload(
  raw: unknown,
): VoiceSessionSnapshotPayload | null {
  if (!isPlainObject(raw)) {
    return null;
  }
  const allowed = new Set([
    "name",
    "utterance_id",
    "response_id",
    "playback_id",
    "degraded_half_duplex",
    "push_to_talk_fallback",
    "last_error_code",
  ]);
  if (!hasOnlyKeys(raw, allowed)) {
    return null;
  }
  if (!isVoiceSessionStateName(raw.name)) {
    return null;
  }
  const utteranceId = parseOptionalId(raw.utterance_id);
  const responseId = parseOptionalId(raw.response_id);
  const playbackId = parseOptionalId(raw.playback_id);
  if (utteranceId === null || responseId === null || playbackId === null) {
    return null;
  }
  if (typeof raw.degraded_half_duplex !== "boolean") {
    return null;
  }
  if (typeof raw.push_to_talk_fallback !== "boolean") {
    return null;
  }
  const lastErrorCode =
    raw.last_error_code === undefined ? "" : raw.last_error_code;
  if (
    typeof lastErrorCode !== "string" ||
    lastErrorCode.length > 64 ||
    (lastErrorCode !== "" && !/^[a-z0-9_.-]+$/.test(lastErrorCode))
  ) {
    return null;
  }
  return {
    name: raw.name,
    utteranceId,
    responseId,
    playbackId,
    degradedHalfDuplex: raw.degraded_half_duplex,
    pushToTalkFallback: raw.push_to_talk_fallback,
    lastErrorCode,
  };
}

/**
 * Parse a versioned generic object into a VoiceSessionEvent.
 * Never returns or stores transcript/response content fields.
 */
export function parseVoiceProtocolMessage(
  input: unknown,
): VoiceProtocolParseResult {
  if (!isPlainObject(input)) {
    return fail("invalid_envelope", "Voice event must be an object.");
  }

  const allowedEnvelope = new Set([
    "protocol_version",
    "type",
    "session_id",
    "event_seq",
    "utterance_id",
    "response_id",
    "playback_id",
    "caption_len",
    "code",
    "snapshot",
  ]);
  if (!hasOnlyKeys(input, allowedEnvelope)) {
    return fail("invalid_fields", "Voice event contains unknown fields.");
  }

  if (input.protocol_version !== VOICE_PROTOCOL_VERSION) {
    return fail("unsupported_version", "Unsupported voice protocol version.");
  }

  if (typeof input.type !== "string" || !EVENT_TYPES.has(input.type)) {
    return fail("unknown_type", "Unknown or invalid voice event type.");
  }

  const sessionId = requireId(input.session_id);
  if (!sessionId) {
    return fail("invalid_bounds", "Invalid session_id.");
  }
  if (!isValidVoiceEventSeq(input.event_seq)) {
    return fail("invalid_bounds", "Invalid event_seq.");
  }

  const type = input.type;
  const eventSeq = input.event_seq;

  switch (type) {
    case "session_start":
    case "session_reset":
    case "wake_detected":
    case "await_command":
    case "enter_idle":
    case "enter_asleep":
    case "degraded_half_duplex":
    case "clear_caption": {
      const keys = new Set(["protocol_version", "type", "session_id", "event_seq"]);
      if (!hasOnlyKeys(input, keys)) {
        return fail("invalid_fields", "Unexpected fields for event type.");
      }
      return {
        ok: true,
        event: { type, sessionId, eventSeq } as VoiceSessionEvent,
      };
    }
    case "session_snapshot": {
      const keys = new Set([
        "protocol_version",
        "type",
        "session_id",
        "event_seq",
        "snapshot",
      ]);
      if (!hasOnlyKeys(input, keys)) {
        return fail("invalid_fields", "Unexpected fields for event type.");
      }
      const snapshot = parseSnapshotPayload(input.snapshot);
      if (!snapshot) {
        return fail("invalid_bounds", "Invalid session snapshot.");
      }
      return {
        ok: true,
        event: {
          type: "session_snapshot",
          sessionId,
          eventSeq,
          snapshot,
        },
      };
    }
    case "microphone_error":
    case "aec_error": {
      const keys = new Set([
        "protocol_version",
        "type",
        "session_id",
        "event_seq",
        "code",
      ]);
      if (!hasOnlyKeys(input, keys)) {
        return fail("invalid_fields", "Unexpected fields for event type.");
      }
      if (input.code !== undefined) {
        if (typeof input.code !== "string" || input.code.length > 64) {
          return fail("invalid_bounds", "Invalid error code.");
        }
        if (!/^[a-z0-9_.-]+$/.test(input.code)) {
          return fail("invalid_bounds", "Invalid error code.");
        }
      }
      return {
        ok: true,
        event: {
          type,
          sessionId,
          eventSeq,
          ...(input.code ? { code: input.code } : {}),
        } as VoiceSessionEvent,
      };
    }
    case "listening_started":
    case "user_speech_activity":
    case "final_transcript_ready":
    case "submit_turn": {
      const keys = new Set([
        "protocol_version",
        "type",
        "session_id",
        "event_seq",
        "utterance_id",
      ]);
      if (!hasOnlyKeys(input, keys)) {
        return fail("invalid_fields", "Unexpected fields for event type.");
      }
      const utteranceId = requireId(input.utterance_id);
      if (!utteranceId) {
        return fail("invalid_bounds", "Invalid utterance_id.");
      }
      return {
        ok: true,
        event: { type, sessionId, utteranceId, eventSeq } as VoiceSessionEvent,
      };
    }
    case "partial_caption": {
      const keys = new Set([
        "protocol_version",
        "type",
        "session_id",
        "event_seq",
        "utterance_id",
        "caption_len",
      ]);
      if (!hasOnlyKeys(input, keys)) {
        return fail("invalid_fields", "Unexpected fields for event type.");
      }
      const utteranceId = requireId(input.utterance_id);
      if (!utteranceId) {
        return fail("invalid_bounds", "Invalid utterance_id.");
      }
      if (
        !isNonNegInt(input.caption_len) ||
        input.caption_len > VOICE_CAPTION_MAX
      ) {
        return fail("invalid_bounds", "Invalid caption_len.");
      }
      // Intentionally no transcript text field — content is never accepted.
      return {
        ok: true,
        event: {
          type: "partial_caption",
          sessionId,
          utteranceId,
          eventSeq,
          captionLen: input.caption_len,
        },
      };
    }
    case "assistant_generating": {
      const keys = new Set([
        "protocol_version",
        "type",
        "session_id",
        "event_seq",
        "utterance_id",
        "response_id",
      ]);
      if (!hasOnlyKeys(input, keys)) {
        return fail("invalid_fields", "Unexpected fields for event type.");
      }
      const utteranceId = requireId(input.utterance_id);
      const responseId = requireId(input.response_id);
      if (!utteranceId || !responseId) {
        return fail("invalid_bounds", "Invalid correlation ids.");
      }
      return {
        ok: true,
        event: {
          type: "assistant_generating",
          sessionId,
          utteranceId,
          responseId,
          eventSeq,
        },
      };
    }
    case "assistant_speaking":
    case "barge_in":
    case "cancel_assistant": {
      const keys = new Set([
        "protocol_version",
        "type",
        "session_id",
        "event_seq",
        "response_id",
        "playback_id",
      ]);
      if (!hasOnlyKeys(input, keys)) {
        return fail("invalid_fields", "Unexpected fields for event type.");
      }
      const responseId = requireId(input.response_id);
      const playbackId = requireId(input.playback_id);
      if (!responseId || !playbackId) {
        return fail("invalid_bounds", "Invalid correlation ids.");
      }
      return {
        ok: true,
        event: {
          type,
          sessionId,
          responseId,
          playbackId,
          eventSeq,
        } as VoiceSessionEvent,
      };
    }
    case "assistant_cancelled":
    case "interrupted_ack": {
      const keys = new Set([
        "protocol_version",
        "type",
        "session_id",
        "event_seq",
        "response_id",
      ]);
      if (!hasOnlyKeys(input, keys)) {
        return fail("invalid_fields", "Unexpected fields for event type.");
      }
      const responseId = requireId(input.response_id);
      if (!responseId) {
        return fail("invalid_bounds", "Invalid response_id.");
      }
      return {
        ok: true,
        event: { type, sessionId, responseId, eventSeq } as VoiceSessionEvent,
      };
    }
    default:
      return fail("unknown_type", "Unknown or invalid voice event type.");
  }
}

/**
 * Adapter helper: parse then indicate whether the event can submit a turn.
 * Partial captions and reconnect recovery never submit.
 */
export function voiceEventSubmitIntent(
  parsed: VoiceProtocolParseResult,
): "submit_turn" | "none" | "noop" {
  if (!parsed.ok) {
    return "noop";
  }
  if (parsed.event.type === "submit_turn") {
    return "submit_turn";
  }
  if (
    parsed.event.type === "partial_caption" ||
    parsed.event.type === "session_snapshot" ||
    parsed.event.type === "session_reset"
  ) {
    return "none";
  }
  return "none";
}
