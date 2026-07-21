/**
 * Unit tests for Phase 4 visual transfer state machine and reducer.
 */

import assert from "node:assert/strict";
import test from "node:test";
import {
  createInitialVisualTransferState,
  isVisualTransferPending,
  isVisualTransferTerminal,
  reduceVisualTransfer,
  validateImageFile,
} from "./visualTransfer.js";

function createTestFile(name: string, type: string, sizeBytes: number): File {
  const blob = new Blob(["a".repeat(sizeBytes)], { type });
  return new File([blob], name, { type });
}

test("validateImageFile validates image type and 1 MiB size bounds", () => {
  const validPng = createTestFile("test.png", "image/png", 500000);
  assert.deepEqual(validateImageFile(validPng), { valid: true, errorCode: null });

  const validJpeg = createTestFile("test.jpg", "image/jpeg", 1000000);
  assert.deepEqual(validateImageFile(validJpeg), { valid: true, errorCode: null });

  const invalidType = createTestFile("test.gif", "image/gif", 500000);
  assert.equal(validateImageFile(invalidType).valid, false);
  assert.equal(validateImageFile(invalidType).errorCode, "mime_unsupported");

  const oversized = createTestFile("large.png", "image/png", 1048577);
  assert.equal(validateImageFile(oversized).valid, false);
  assert.equal(validateImageFile(oversized).errorCode, "size_exceeded");

  const emptyFile = createTestFile("empty.png", "image/png", 0);
  assert.equal(validateImageFile(emptyFile).valid, false);
  assert.equal(validateImageFile(emptyFile).errorCode, "size_exceeded");
});

test("VisualTransfer lifecycle and file reference clearing on completion", () => {
  let state = createInitialVisualTransferState();
  assert.equal(state.status, "idle");

  const file = createTestFile("photo.jpeg", "image/jpeg", 204800);
  state = reduceVisualTransfer(state, {
    type: "SELECT_FILE",
    file,
  });

  assert.equal(state.status, "selected");
  assert.equal(state.fileRef, file);
  assert.equal(state.fileSize, 204800);

  state = reduceVisualTransfer(state, {
    type: "BEGIN_TRANSFER",
    requestId: "request-1",
  });
  assert.equal(state.status, "beginning");
  assert.equal(isVisualTransferPending(state.status), true);

  state = reduceVisualTransfer(state, {
    type: "SET_READY",
    requestId: "request-1",
    transferId: "Transfer:Opaque-1",
  });
  assert.equal(state.status, "ready");

  state = reduceVisualTransfer(state, {
    type: "START_TRANSFERRING",
    transferId: "Transfer:Opaque-1",
  });
  assert.equal(state.status, "transferring");

  state = reduceVisualTransfer(state, {
    type: "TRANSFER_COMPLETE",
    transferId: "Transfer:Opaque-1",
  });

  assert.equal(state.status, "completed");
  assert.equal(isVisualTransferTerminal(state.status), true);
  assert.equal(state.fileRef, null);
});

test("File reference is cleared on cancellation and error", () => {
  let state = createInitialVisualTransferState();
  const file = createTestFile("doc.png", "image/png", 100000);

  state = reduceVisualTransfer(state, { type: "SELECT_FILE", file });
  assert.equal(state.fileRef, file);

  const cancelState = reduceVisualTransfer(state, { type: "CANCEL" });
  assert.equal(cancelState.status, "cancelling");
  assert.equal(cancelState.fileRef, null);

  const failState = reduceVisualTransfer(state, { type: "FAIL", errorCode: "unavailable" });
  assert.equal(failState.status, "failed");
  assert.equal(failState.fileRef, null);
});
