/**
 * Pure Phase 4 protocol parser and encoder unit tests.
 */

import assert from "node:assert/strict";
import test from "node:test";
import {
  parsePhase4ServerMessage,
  encodePairingPrepare,
  encodePairingConfirm,
  encodePairingCancel,
  encodeHandoffAccept,
  encodeHandoffReject,
  encodeHandoffCancel,
  encodeVisualTransferBegin,
  encodeVisualTransferCancel,
} from "./phase4Protocol";

test("parsePhase4ServerMessage parses valid pairing_challenge frame", () => {
  const json = JSON.stringify({
    type: "pairing_challenge",
    request_id: "req-001",
    challenge_id: "ch-001",
    expires_at: 1700000000,
  });
  const parsed = parsePhase4ServerMessage(json);
  assert.notEqual(parsed, null);
  assert.equal(parsed?.type, "pairing_challenge");
  if (parsed?.type === "pairing_challenge") {
    assert.equal(parsed.request_id, "req-001");
    assert.equal(parsed.challenge_id, "ch-001");
    assert.equal(parsed.expires_at, 1700000000);
  }
});

test("parsePhase4ServerMessage rejects unknown extra fields", () => {
  const json = JSON.stringify({
    type: "pairing_challenge",
    request_id: "req-001",
    challenge_id: "ch-001",
    expires_at: 1700000000,
    unknown_extra_field: "HACK",
  });
  assert.equal(parsePhase4ServerMessage(json), null);
});

test("parsePhase4ServerMessage rejects malformed IDs", () => {
  const json = JSON.stringify({
    type: "pairing_confirmed",
    request_id: "REQ_UPPERCASE", // Invalid canonical ID
    device_id: "dev-1",
    expires_at: 1700000000,
    protocol_version: 1,
  });
  assert.equal(parsePhase4ServerMessage(json), null);
});

test("parsePhase4ServerMessage parses handoff_offer frame with summary", () => {
  const json = JSON.stringify({
    type: "handoff_offer",
    request_id: "req-100",
    handoff_id: "hoff-100",
    task_id: "task-100",
    summary: "Review expense report",
    expires_at: 1700000000,
  });
  const parsed = parsePhase4ServerMessage(json);
  assert.notEqual(parsed, null);
  assert.equal(parsed?.type, "handoff_offer");
  if (parsed?.type === "handoff_offer") {
    assert.equal(parsed.summary, "Review expense report");
    assert.equal(Object.isFrozen(parsed), true);
  }
});

test("parsePhase4ServerMessage parses visual_transfer_complete", () => {
  const json = JSON.stringify({
    type: "visual_transfer_complete",
    request_id: "req-vt",
    transfer_id: "tx-vt",
    content_hash: `sha256.${"a".repeat(64)}`,
  });
  const parsed = parsePhase4ServerMessage(json);
  assert.notEqual(parsed, null);
  assert.equal(parsed?.type, "visual_transfer_complete");
});

test("encoders produce exact protocol frames", () => {
  assert.deepEqual(encodePairingPrepare("req-1"), {
    type: "pairing_prepare",
    request_id: "req-1",
  });

  assert.deepEqual(encodePairingConfirm("req-1", "ch-1", "ABC123"), {
    type: "pairing_confirm",
    request_id: "req-1",
    challenge_id: "ch-1",
    code: "ABC123",
  });

  assert.deepEqual(encodePairingCancel("req-1", "ch-1"), {
    type: "pairing_cancel",
    request_id: "req-1",
    challenge_id: "ch-1",
  });

  assert.deepEqual(encodeHandoffAccept("req-1", "hoff-1"), {
    type: "handoff_accept",
    request_id: "req-1",
    handoff_id: "hoff-1",
    acknowledged: true,
  });

  assert.deepEqual(encodeHandoffReject("req-1", "hoff-1"), {
    type: "handoff_reject",
    request_id: "req-1",
    handoff_id: "hoff-1",
  });

  assert.deepEqual(encodeHandoffCancel("req-1", "hoff-1"), {
    type: "handoff_cancel",
    request_id: "req-1",
    handoff_id: "hoff-1",
  });

  assert.deepEqual(encodeVisualTransferBegin("req-1", "hoff-1", "image/png", 500000, 640, 480), {
    type: "visual_transfer_begin",
    request_id: "req-1",
    handoff_id: "hoff-1",
    mime_type: "image/png",
    size_bytes: 500000,
    width: 640,
    height: 480,
    frame_count: 1,
  });

  assert.deepEqual(encodeVisualTransferCancel("req-1", "tx-1"), {
    type: "visual_transfer_cancel",
    request_id: "req-1",
    transfer_id: "tx-1",
  });
});
