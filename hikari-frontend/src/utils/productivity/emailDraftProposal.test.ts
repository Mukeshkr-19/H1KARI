import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  EMAIL_DRAFT_BODY_MAX,
  EMAIL_DRAFT_RECIPIENT_MAX,
  EMAIL_DRAFT_SUBJECT_MAX,
  createEmailDraftRequestId,
  createEmptyEmailDraftFields,
  createInitialEmailDraftClientState,
  emailDraftResponseMatchesRequest,
  hasEmailDraftUnicodeFormatChars,
  isValidEmailDraftRequestId,
  mapEmailDraftValidationMessage,
  reduceEmailDraftClientState,
  validateEmailDraftFields,
} from "./emailDraftProposal";
import { encodeProductivityEmailDraftPrepare } from "./productivityProtocol";

function sample(overrides: Record<string, unknown> = {}) {
  return {
    recipient: "alice@example.com",
    subject: "Hello",
    body: "Line one\nLine two",
    ...overrides,
  };
}

describe("emailDraftProposal", () => {
  it("validates and freezes bounded draft fields", () => {
    const result = validateEmailDraftFields(sample());
    assert.equal(result.ok, true);
    if (!result.ok) {
      return;
    }
    assert.equal(result.fields.recipient, "alice@example.com");
    assert.equal(result.fields.subject, "Hello");
    assert.equal(result.fields.body, "Line one\nLine two");
    assert.throws(() => {
      (result.fields as { recipient: string }).recipient = "nope";
    }, TypeError);
  });

  it("rejects empty oversized and control-bearing recipient without truncation", () => {
    assert.equal(validateEmailDraftFields(sample({ recipient: "" })).ok, false);
    assert.equal(validateEmailDraftFields(sample({ recipient: "   " })).ok, false);
    const tooLong = validateEmailDraftFields(
      sample({ recipient: "a".repeat(EMAIL_DRAFT_RECIPIENT_MAX + 1) }),
    );
    assert.equal(tooLong.ok, false);
    if (!tooLong.ok) {
      assert.equal(tooLong.code, "recipient_too_long");
      assert.equal(tooLong.field, "recipient");
    }
    const controls = validateEmailDraftFields(
      sample({ recipient: "alice\u0000@example.com" }),
    );
    assert.equal(controls.ok, false);
    if (!controls.ok) {
      assert.equal(controls.code, "recipient_invalid_controls");
    }
  });

  it("rejects incomplete recipient addresses", () => {
    const result = validateEmailDraftFields(
      sample({ recipient: "not-an-address" }),
    );
    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.equal(result.code, "recipient_invalid_format");
      assert.equal(result.field, "recipient");
    }
  });

  it("rejects oversized subject and body with explicit validation codes", () => {
    const subject = validateEmailDraftFields(
      sample({ subject: "s".repeat(EMAIL_DRAFT_SUBJECT_MAX + 1) }),
    );
    assert.equal(subject.ok, false);
    if (!subject.ok) {
      assert.equal(subject.code, "subject_too_long");
      assert.equal(subject.field, "subject");
    }
    const body = validateEmailDraftFields(
      sample({ body: "b".repeat(EMAIL_DRAFT_BODY_MAX + 1) }),
    );
    assert.equal(body.ok, false);
    if (!body.ok) {
      assert.equal(body.code, "body_too_long");
      assert.equal(body.field, "body");
    }
    assert.equal(
      validateEmailDraftFields(
        sample({ subject: "s".repeat(EMAIL_DRAFT_SUBJECT_MAX) }),
      ).ok,
      true,
    );
    assert.equal(
      validateEmailDraftFields(sample({ body: "b".repeat(EMAIL_DRAFT_BODY_MAX) }))
        .ok,
      true,
    );
  });

  it("allows newline and tab only in body and rejects Unicode Cf without rewriting", () => {
    assert.equal(
      validateEmailDraftFields(sample({ subject: "Hi\nthere" })).ok,
      false,
    );
    assert.equal(
      validateEmailDraftFields(sample({ body: "Hi\n\tthere" })).ok,
      true,
    );
    assert.equal(
      validateEmailDraftFields(sample({ body: "Hi\u0007there" })).ok,
      false,
    );

    const zwsp = "\u200B";
    assert.equal(hasEmailDraftUnicodeFormatChars(`a${zwsp}b`), true);
    const zwspRecipient = validateEmailDraftFields(
      sample({ recipient: `alice${zwsp}@example.com` }),
    );
    assert.equal(zwspRecipient.ok, false);
    if (!zwspRecipient.ok) {
      assert.equal(zwspRecipient.code, "recipient_invalid_controls");
    }

    const cfCases = [
      { field: "recipient", value: `alice\u200C@example.com`, code: "recipient_invalid_controls" },
      { field: "recipient", value: `alice\u200D@example.com`, code: "recipient_invalid_controls" },
      { field: "subject", value: "Sub\uFEFFject", code: "subject_invalid_controls" },
      { field: "subject", value: "Sub\u202Eject", code: "subject_invalid_controls" },
      { field: "body", value: "Body\u2066 text", code: "body_invalid_controls" },
      { field: "body", value: "Body\u061C text", code: "body_invalid_controls" },
      { field: "body", value: "Body\u200B text", code: "body_invalid_controls" },
    ] as const;
    for (const entry of cfCases) {
      const result = validateEmailDraftFields(
        sample({ [entry.field]: entry.value }),
      );
      assert.equal(result.ok, false, entry.value);
      if (!result.ok) {
        assert.equal(result.code, entry.code);
        assert.equal(result.field, entry.field);
      }
    }
  });

  it("rejects unknown fields and non-string values", () => {
    assert.equal(
      validateEmailDraftFields(sample({ extra: true })).ok,
      false,
    );
    assert.equal(
      validateEmailDraftFields(sample({ recipient: 1 })).ok,
      false,
    );
    assert.equal(validateEmailDraftFields(null).ok, false);
    assert.equal(validateEmailDraftFields([]).ok, false);
  });

  it("maps only safe validation messages", () => {
    assert.match(
      mapEmailDraftValidationMessage("recipient_too_long"),
      /320/,
    );
    assert.match(mapEmailDraftValidationMessage("body_too_long"), /20,000/);
    assert.equal(
      mapEmailDraftValidationMessage("provider_timeout").includes("provider"),
      false,
    );
    assert.deepEqual(createEmptyEmailDraftFields(), {
      recipient: "",
      subject: "",
      body: "",
    });
  });

  it("encodes exact prepare messages and rejects malformed input", () => {
    const encoded = encodeProductivityEmailDraftPrepare({
      type: "productivity_email_draft_prepare",
      request_id: "email-req-1",
      recipient: " alice@example.com ",
      subject: "Hello",
      body: "Body\ntext",
    });
    assert.deepEqual(encoded, {
      type: "productivity_email_draft_prepare",
      request_id: "email-req-1",
      recipient: "alice@example.com",
      subject: "Hello",
      body: "Body\ntext",
    });
    assert.throws(() => {
      (encoded as { recipient: string }).recipient = "x";
    }, TypeError);

    assert.equal(
      encodeProductivityEmailDraftPrepare({
        type: "productivity_email_draft_prepare",
        request_id: "email-req-1",
        recipient: "alice@example.com",
        subject: "Hello",
        body: "Body",
        extra: true,
      }),
      null,
    );
    assert.equal(
      encodeProductivityEmailDraftPrepare({
        type: "productivity_email_draft_prepare",
        recipient: "alice@example.com",
        subject: "Hello",
        body: "Body",
      }),
      null,
    );
    assert.equal(
      encodeProductivityEmailDraftPrepare({
        type: "productivity_email_draft_prepare",
        request_id: "Bad ID",
        recipient: "alice@example.com",
        subject: "Hello",
        body: "Body",
      }),
      null,
    );
    assert.equal(
      encodeProductivityEmailDraftPrepare({
        type: "productivity_confirm",
        request_id: "email-req-1",
        recipient: "alice@example.com",
        subject: "Hello",
        body: "Body",
      }),
      null,
    );
    assert.equal(
      encodeProductivityEmailDraftPrepare({
        type: "productivity_email_draft_prepare",
        request_id: "email-req-1",
        recipient: "",
        subject: "Hello",
        body: "Body",
      }),
      null,
    );
    assert.equal(
      encodeProductivityEmailDraftPrepare({
        type: "productivity_email_draft_prepare",
        request_id: "email-req-1",
        recipient: "alice@example.com",
        subject: "s".repeat(EMAIL_DRAFT_SUBJECT_MAX + 1),
        body: "Body",
      }),
      null,
    );
    const raw = JSON.stringify(encoded);
    assert.equal(raw.includes("actor"), false);
    assert.equal(raw.includes("session"), false);
    assert.equal(raw.includes("provider"), false);
  });

  it("creates and validates canonical request ids", () => {
    const id = createEmailDraftRequestId();
    assert.equal(isValidEmailDraftRequestId(id), true);
    assert.equal(isValidEmailDraftRequestId("Bad ID"), false);
    assert.equal(isValidEmailDraftRequestId(""), false);
    assert.equal(isValidEmailDraftRequestId(null), false);
  });

  it("matches confirmation and error only for the active request id", () => {
    assert.equal(
      emailDraftResponseMatchesRequest("email-req-1", "email-req-1"),
      true,
    );
    assert.equal(
      emailDraftResponseMatchesRequest("email-req-1", "email-req-2"),
      false,
    );
    assert.equal(emailDraftResponseMatchesRequest("email-req-1", undefined), false);
    assert.equal(emailDraftResponseMatchesRequest("email-req-1", null), false);
    assert.equal(emailDraftResponseMatchesRequest(null, "email-req-1"), false);
    assert.equal(
      emailDraftResponseMatchesRequest("Bad ID", "Bad ID"),
      false,
    );
  });

  it("reducer clears pending on protocol rejection while preserving draft fields", () => {
    const fields = Object.freeze({
      recipient: "alice@example.com",
      subject: "Keep me",
      body: "Private body",
    });
    let state = reduceEmailDraftClientState(createInitialEmailDraftClientState(), {
      type: "submit_started",
      requestId: "email-req-1",
      fields,
    });
    assert.equal(state.pending, true);
    state = reduceEmailDraftClientState(state, { type: "protocol_rejection" });
    assert.equal(state.pending, false);
    assert.equal(state.requestId, null);
    assert.equal(state.prepareError, "unavailable");
    assert.deepEqual(state.fields, fields);
  });

  it("reducer ignores stale and mismatched response ids", () => {
    const fields = Object.freeze({
      recipient: "alice@example.com",
      subject: "Hello",
      body: "Body",
    });
    const state = reduceEmailDraftClientState(createInitialEmailDraftClientState(), {
      type: "submit_started",
      requestId: "email-req-1",
      fields,
    });
    const staleConfirm = reduceEmailDraftClientState(state, {
      type: "matched_confirmation",
      requestId: "email-req-stale",
    });
    assert.equal(staleConfirm, state);
    assert.equal(staleConfirm.pending, true);

    const missing = reduceEmailDraftClientState(state, {
      type: "matched_error",
      requestId: "email-req-other",
      code: "unavailable",
    });
    assert.equal(missing, state);
    assert.equal(missing.pending, true);
  });

  it("reducer blocks duplicate submit and accepts matching success and error", () => {
    const fields = Object.freeze({
      recipient: "alice@example.com",
      subject: "Hello",
      body: "Body",
    });
    let state = reduceEmailDraftClientState(createInitialEmailDraftClientState(), {
      type: "submit_started",
      requestId: "email-req-1",
      fields,
    });
    const duplicate = reduceEmailDraftClientState(state, {
      type: "submit_blocked_duplicate",
    });
    assert.equal(duplicate, state);
    const secondStart = reduceEmailDraftClientState(state, {
      type: "submit_started",
      requestId: "email-req-2",
      fields,
    });
    assert.equal(secondStart, state);

    state = reduceEmailDraftClientState(state, {
      type: "matched_confirmation",
      requestId: "email-req-1",
    });
    assert.equal(state.pending, false);
    assert.equal(state.requestId, null);
    assert.equal(state.prepareError, undefined);
    assert.deepEqual(state.fields, fields);

    state = reduceEmailDraftClientState(createInitialEmailDraftClientState(), {
      type: "submit_started",
      requestId: "email-req-3",
      fields,
    });
    state = reduceEmailDraftClientState(state, {
      type: "matched_error",
      requestId: "email-req-3",
      code: "proposal_invalid",
    });
    assert.equal(state.pending, false);
    assert.equal(state.prepareError, "proposal_invalid");
    assert.deepEqual(state.fields, fields);
  });

  it("maps specific accessible validation descriptions per field code", () => {
    assert.equal(
      mapEmailDraftValidationMessage("recipient_invalid_controls"),
      "Recipient contains characters that are not allowed.",
    );
    assert.equal(
      mapEmailDraftValidationMessage("subject_invalid_controls"),
      "Subject contains characters that are not allowed.",
    );
    assert.equal(
      mapEmailDraftValidationMessage("body_invalid_controls"),
      "Body contains characters that are not allowed.",
    );
    assert.equal(
      mapEmailDraftValidationMessage("recipient_required"),
      "Enter a recipient.",
    );
  });
});
