import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  GENERIC_PREVIEW_ERROR_MESSAGE,
  PREVIEW_LABEL_MAX,
  PREVIEW_VALUE_MAX,
  boundPreviewLabel,
  boundPreviewValue,
  mapPreviewErrorMessage,
  sanitizePreviewText,
  type ProductivityPreviewErrorCode,
} from "./actionPreview";

const SAFE_ERROR_CODES: ProductivityPreviewErrorCode[] = [
  "confirm_failed",
  "cancel_failed",
  "proposal_expired",
  "proposal_invalid",
  "unavailable",
];

describe("actionPreview", () => {
  it("bounds labels at the exact limit", () => {
    const exact = "a".repeat(PREVIEW_LABEL_MAX);
    assert.deepEqual(boundPreviewLabel(exact), { text: exact, truncated: false });

    const over = "b".repeat(PREVIEW_LABEL_MAX + 1);
    const result = boundPreviewLabel(over);
    assert.equal(result.truncated, true);
    assert.equal(result.text, `${"b".repeat(PREVIEW_LABEL_MAX)}…`);
    assert.equal(result.text.length, PREVIEW_LABEL_MAX + 1);
  });

  it("bounds values at the exact limit", () => {
    const exact = "c".repeat(PREVIEW_VALUE_MAX);
    assert.deepEqual(boundPreviewValue(exact), { text: exact, truncated: false });

    const over = "d".repeat(PREVIEW_VALUE_MAX + 5);
    const result = boundPreviewValue(over);
    assert.equal(result.truncated, true);
    assert.equal(result.text, `${"d".repeat(PREVIEW_VALUE_MAX)}…`);
  });

  it("strips ASCII control characters except tab and newline", () => {
    const raw = "ok\u0000\u0007\u0008\u000B\u000C\u000E\u001F\u007Fdone";
    assert.equal(sanitizePreviewText(raw), "okdone");
    assert.equal(sanitizePreviewText("line\none\ttwo"), "line\none\ttwo");
    assert.equal(boundPreviewValue("keep\n\tand\tspace").text, "keep\n\tand\tspace");
  });

  it("strips Unicode direction and formatting controls", () => {
    const raw =
      "start\u061C\u200E\u200F\u202A\u202B\u202C\u202D\u202E\u2066\u2067\u2068\u2069\uFEFFend";
    assert.equal(sanitizePreviewText(raw), "startend");
    assert.equal(
      boundPreviewLabel(`\u202E${"x".repeat(PREVIEW_LABEL_MAX)}`).text,
      "x".repeat(PREVIEW_LABEL_MAX),
    );
  });

  it("preserves newline and tab through value bounding", () => {
    const body = "Subject\n\tIndented body\nFinal";
    assert.equal(boundPreviewValue(body).text, body);
    assert.equal(boundPreviewValue(body).truncated, false);
  });

  it("maps every safe error code to a fixed generic message", () => {
    for (const code of SAFE_ERROR_CODES) {
      const message = mapPreviewErrorMessage(code);
      assert.equal(typeof message, "string");
      assert.ok(message.length > 0);
      assert.equal(message.includes("http"), false);
      assert.equal(message.includes("Error:"), false);
      assert.equal(message.includes("Exception"), false);
      assert.equal(message.includes("stack"), false);
      assert.equal(message.includes("token"), false);
    }
    assert.equal(
      mapPreviewErrorMessage("confirm_failed"),
      "The action could not be confirmed.",
    );
    assert.equal(
      mapPreviewErrorMessage("cancel_failed"),
      "The action could not be cancelled.",
    );
    assert.equal(
      mapPreviewErrorMessage("proposal_expired"),
      "This proposal has expired. Request a new preview.",
    );
    assert.equal(
      mapPreviewErrorMessage("proposal_invalid"),
      "This proposal is no longer valid.",
    );
    assert.equal(
      mapPreviewErrorMessage("unavailable"),
      "The action is temporarily unavailable.",
    );
  });

  it("falls back to a generic message for unknown or missing codes", () => {
    assert.equal(mapPreviewErrorMessage(undefined), GENERIC_PREVIEW_ERROR_MESSAGE);
    assert.equal(mapPreviewErrorMessage(null), GENERIC_PREVIEW_ERROR_MESSAGE);
    assert.equal(mapPreviewErrorMessage(""), GENERIC_PREVIEW_ERROR_MESSAGE);
    assert.equal(mapPreviewErrorMessage("toString"), GENERIC_PREVIEW_ERROR_MESSAGE);
    assert.equal(mapPreviewErrorMessage("constructor"), GENERIC_PREVIEW_ERROR_MESSAGE);
    assert.equal(mapPreviewErrorMessage("provider_timeout"), GENERIC_PREVIEW_ERROR_MESSAGE);
    assert.equal(
      mapPreviewErrorMessage("TypeError: boom at https://evil.example/x?token=abc"),
      GENERIC_PREVIEW_ERROR_MESSAGE,
    );
  });

  it("never returns raw exception or provider text", () => {
    const hostile = [
      "Error: ECONNRESET",
      "Exception: stack\n  at Object.<anonymous>",
      "https://api.example/v1/send?key=secret",
      "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
      '{"provider":"smtp","detail":"quota exceeded"}',
    ];
    for (const value of hostile) {
      const message = mapPreviewErrorMessage(value);
      assert.equal(message, GENERIC_PREVIEW_ERROR_MESSAGE);
      assert.equal(message.includes(value), false);
    }
  });
});
