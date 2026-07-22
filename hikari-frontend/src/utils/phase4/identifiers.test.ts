/**
 * Unit tests for Phase 4 identifier validation utilities.
 */

import assert from "node:assert/strict";
import test from "node:test";
import {
  isValidCanonicalId,
  isValidOpaqueId,
  isValidSummaryText,
  createCanonicalRequestId,
} from "./identifiers";

test("isValidCanonicalId accepts valid canonical IDs", () => {
  assert.equal(isValidCanonicalId("req-12345"), true);
  assert.equal(isValidCanonicalId("challenge_abc.123"), true);
  assert.equal(isValidCanonicalId("a"), true);
  assert.equal(isValidCanonicalId("a".repeat(80)), true);
});

test("isValidCanonicalId rejects invalid canonical IDs", () => {
  assert.equal(isValidCanonicalId(""), false);
  assert.equal(isValidCanonicalId("A_upper"), false); // No uppercase
  assert.equal(isValidCanonicalId("-start-hyphen"), false); // Must start with a-z0-9
  assert.equal(isValidCanonicalId("a".repeat(81)), false); // Exceeds 80
  assert.equal(isValidCanonicalId(123), false);
  assert.equal(isValidCanonicalId(null), false);
});

test("isValidOpaqueId accepts valid opaque IDs", () => {
  assert.equal(isValidOpaqueId("Device_123:abc.-_"), true);
  assert.equal(isValidOpaqueId("A".repeat(128)), true);
});

test("isValidOpaqueId rejects invalid opaque IDs", () => {
  assert.equal(isValidOpaqueId(""), false);
  assert.equal(isValidOpaqueId("invalid space"), false);
  assert.equal(isValidOpaqueId("A".repeat(129)), false);
  assert.equal(isValidOpaqueId(undefined), false);
});

test("isValidSummaryText accepts valid summary text", () => {
  assert.equal(isValidSummaryText("Process invoice #1001"), true);
  assert.equal(isValidSummaryText("🚀 Unicode task summary"), true);
});

test("isValidSummaryText rejects invalid summary text", () => {
  assert.equal(isValidSummaryText(""), false);
  assert.equal(isValidSummaryText("   "), false); // Whitespace only
  assert.equal(isValidSummaryText("Line 1\nLine 2"), false); // Control char \n
  assert.equal(isValidSummaryText("Soft\u00adHyphen"), false); // Unicode Cf
  assert.equal(isValidSummaryText("a".repeat(201)), false); // Exceeds 200 code points
  assert.equal(isValidSummaryText(null), false);
});

test("createCanonicalRequestId generates valid canonical IDs", () => {
  const reqId = createCanonicalRequestId("req");
  assert.equal(isValidCanonicalId(reqId), true);
  const pairId = createCanonicalRequestId("pair");
  assert.equal(isValidCanonicalId(pairId), true);
});
