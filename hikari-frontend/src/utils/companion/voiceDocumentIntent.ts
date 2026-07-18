/** Deterministic voice → document command mapper. Transcripts are untrusted. */

export const VOICE_TRANSCRIPT_MAX = 2000;
export const VOICE_DOCUMENT_PATH_MAX = 4096;
export const VOICE_DOCUMENT_PROVIDER_MAX = 64;
export const VOICE_FOLLOW_UP_TEXT_MAX = 20000;

export type VoiceDocumentContext = {
  awaitingConfirmation: boolean;
  documentTaskId: string;
  canCancelDocument: boolean;
};

export type VoiceDocumentIntent =
  | { type: "prepare"; path: string; provider: string; fallbackProvider: string }
  | { type: "confirm" }
  | { type: "cancel" }
  | { type: "follow_up"; taskId: string; text: string }
  | { type: "reject"; message: string }
  | { type: "none" };

const CONFIRM_PATTERN = /^(confirm document|explain this document)\s*[.!]?\s*$/i;
const CANCEL_PATTERN = /^cancel document(?:\s+task)?\s*[.!]?\s*$/i;
const BARE_AFFIRM_PATTERN = /^(yes|y|ok|okay|sure)\s*[.!]?\s*$/i;
const PREPARE_STARTER_PATTERN = /^(?:prepare|review)\s+document\b/i;
const PREPARE_PATTERN =
  /^(?:prepare|review)\s+document\s+(.+?)\s+with\s+provider\s+(\S+)(?:\s+fallback\s+(\S+))?\s*$/i;
const FOLLOW_UP_PATTERN =
  /^document follow-up(?:\s+task\s+([A-Za-z0-9._-]{1,64}))?\s*:\s*(.+)$/i;

export function boundVoiceTranscript(raw: string): string {
  return raw.trim().slice(0, VOICE_TRANSCRIPT_MAX);
}

function normalizeVoiceTranscript(raw: string): string {
  return boundVoiceTranscript(raw).replace(/\s+/g, " ").trim();
}

export function parseVoiceDocumentIntent(
  transcript: string,
  context: VoiceDocumentContext,
): VoiceDocumentIntent {
  const normalized = normalizeVoiceTranscript(transcript);
  if (!normalized) {
    return { type: "none" };
  }

  if (BARE_AFFIRM_PATTERN.test(normalized)) {
    if (context.awaitingConfirmation) {
      return {
        type: "reject",
        message: 'Say: confirm document.',
      };
    }
    return { type: "none" };
  }

  if (CONFIRM_PATTERN.test(normalized)) {
    if (!context.awaitingConfirmation) {
      return {
        type: "reject",
        message: "No document is waiting for confirmation.",
      };
    }
    return { type: "confirm" };
  }

  if (CANCEL_PATTERN.test(normalized)) {
    if (!context.canCancelDocument) {
      return {
        type: "reject",
        message: "No active document task to cancel.",
      };
    }
    return { type: "cancel" };
  }

  const followUp = normalized.match(FOLLOW_UP_PATTERN);
  if (followUp) {
    const spokenTaskId = (followUp[1] ?? "").trim();
    const question = (followUp[2] ?? "").trim();
    if (!context.documentTaskId) {
      return {
        type: "reject",
        message: "No document task is available for a follow-up.",
      };
    }
    if (spokenTaskId && spokenTaskId !== context.documentTaskId) {
      return {
        type: "reject",
        message: "Follow-up task ID does not match the active document task.",
      };
    }
    if (!question) {
      return {
        type: "reject",
        message: "Document follow-up needs a question after the prefix.",
      };
    }
    if (question.length > VOICE_FOLLOW_UP_TEXT_MAX) {
      return {
        type: "reject",
        message: "Document follow-up text is too long.",
      };
    }
    return {
      type: "follow_up",
      taskId: context.documentTaskId,
      text: question.slice(0, VOICE_FOLLOW_UP_TEXT_MAX),
    };
  }

  if (PREPARE_STARTER_PATTERN.test(normalized)) {
    const prepare = normalized.match(PREPARE_PATTERN);
    if (!prepare) {
      return {
        type: "reject",
        message: "Say: prepare document <path> with provider <name>.",
      };
    }
    const path = (prepare[1] ?? "").trim();
    const provider = (prepare[2] ?? "").trim();
    const fallbackProvider = (prepare[3] ?? "").trim();
    if (!path || !provider) {
      return {
        type: "reject",
        message: "Document prepare needs both a path and a provider.",
      };
    }
    if (path.length > VOICE_DOCUMENT_PATH_MAX || provider.length > VOICE_DOCUMENT_PROVIDER_MAX) {
      return {
        type: "reject",
        message: "Document path or provider is too long.",
      };
    }
    if (fallbackProvider.length > VOICE_DOCUMENT_PROVIDER_MAX) {
      return {
        type: "reject",
        message: "Fallback provider is too long.",
      };
    }
    return { type: "prepare", path, provider, fallbackProvider };
  }

  return { type: "none" };
}
