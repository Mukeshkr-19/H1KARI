import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  VOICE_DOCUMENT_PATH_MAX,
  VOICE_DOCUMENT_PROVIDER_MAX,
  VOICE_TRANSCRIPT_MAX,
  boundVoiceTranscript,
  parseVoiceDocumentIntent,
  type VoiceDocumentContext,
} from "./voiceDocumentIntent";

const idle: VoiceDocumentContext = {
  awaitingConfirmation: false,
  documentTaskId: "",
  canCancelDocument: false,
};

const pending: VoiceDocumentContext = {
  awaitingConfirmation: true,
  documentTaskId: "task-1",
  canCancelDocument: true,
};

const activeTask: VoiceDocumentContext = {
  awaitingConfirmation: false,
  documentTaskId: "task-9",
  canCancelDocument: true,
};

describe("parseVoiceDocumentIntent", () => {
  it("parses prepare with spaces in the path", () => {
    const intent = parseVoiceDocumentIntent(
      "prepare document /workspace/My Documents/notes.txt with provider ollama",
      idle,
    );
    assert.deepEqual(intent, {
      type: "prepare",
      path: "/workspace/My Documents/notes.txt",
      provider: "ollama",
      fallbackProvider: "",
    });
  });

  it("parses prepare with fallback provider", () => {
    const intent = parseVoiceDocumentIntent(
      "review document /data/report.txt with provider ollama fallback google",
      idle,
    );
    assert.deepEqual(intent, {
      type: "prepare",
      path: "/data/report.txt",
      provider: "ollama",
      fallbackProvider: "google",
    });
  });

  it("rejects prepare when provider is missing", () => {
    const intent = parseVoiceDocumentIntent("prepare document /tmp/notes.txt", idle);
    assert.equal(intent.type, "reject");
    if (intent.type === "reject") {
      assert.match(intent.message.toLowerCase(), /provider/);
    }
  });

  it("rejects path, provider, and fallback length bounds", () => {
    const longPath = `/${"a".repeat(VOICE_DOCUMENT_PATH_MAX)}`;
    const pathIntent = parseVoiceDocumentIntent(
      `prepare document ${longPath} with provider ollama`,
      idle,
    );
    assert.equal(pathIntent.type, "reject");

    const longProvider = "p".repeat(VOICE_DOCUMENT_PROVIDER_MAX + 1);
    const providerIntent = parseVoiceDocumentIntent(
      `prepare document /tmp/notes.txt with provider ${longProvider}`,
      idle,
    );
    assert.equal(providerIntent.type, "reject");

    const longFallback = "f".repeat(VOICE_DOCUMENT_PROVIDER_MAX + 1);
    const fallbackIntent = parseVoiceDocumentIntent(
      `prepare document /tmp/notes.txt with provider ollama fallback ${longFallback}`,
      idle,
    );
    assert.equal(fallbackIntent.type, "reject");
  });

  it("confirms only with an explicit phrase while pending", () => {
    assert.deepEqual(parseVoiceDocumentIntent("confirm document", pending), {
      type: "confirm",
    });
    assert.deepEqual(parseVoiceDocumentIntent("explain this document", pending), {
      type: "confirm",
    });
  });

  it("rejects confirm when no confirmation is pending", () => {
    const intent = parseVoiceDocumentIntent("confirm document", idle);
    assert.equal(intent.type, "reject");
  });

  it("rejects bare affirmations while confirmation is pending", () => {
    for (const phrase of ["yes", "okay", "sure"]) {
      const intent = parseVoiceDocumentIntent(phrase, pending);
      assert.equal(intent.type, "reject");
      if (intent.type === "reject") {
        assert.match(intent.message.toLowerCase(), /confirm document/);
      }
    }
  });

  it("cancels only when a document task is active", () => {
    assert.deepEqual(parseVoiceDocumentIntent("cancel document", activeTask), {
      type: "cancel",
    });
    assert.equal(parseVoiceDocumentIntent("cancel document", idle).type, "reject");
  });

  it("requires a durable task id for follow-up commands", () => {
    assert.deepEqual(
      parseVoiceDocumentIntent("document follow-up: What are the risks?", activeTask),
      {
        type: "follow_up",
        taskId: "task-9",
        text: "What are the risks?",
      },
    );
    assert.equal(
      parseVoiceDocumentIntent("document follow-up: What are the risks?", idle).type,
      "reject",
    );
    assert.equal(
      parseVoiceDocumentIntent(
        "document follow-up task other-id: summarize next steps",
        activeTask,
      ).type,
      "reject",
    );
  });

  it("leaves unmatched speech as ordinary chat", () => {
    assert.deepEqual(
      parseVoiceDocumentIntent("what is the weather tomorrow", pending),
      { type: "none" },
    );
  });

  it("bounds transcript length", () => {
    const raw = `  ${"x".repeat(VOICE_TRANSCRIPT_MAX + 50)}  `;
    const bounded = boundVoiceTranscript(raw);
    assert.equal(bounded.length, VOICE_TRANSCRIPT_MAX);
    assert.equal(bounded, "x".repeat(VOICE_TRANSCRIPT_MAX));
  });
});
