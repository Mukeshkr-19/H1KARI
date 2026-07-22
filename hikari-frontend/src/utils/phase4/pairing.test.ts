/**
 * Unit tests for Phase 4 pairing state machine and reducer.
 */

import assert from "node:assert/strict";
import test from "node:test";
import {
  createInitialPairingState,
  isPairingPending,
  isPairingTerminal,
  reducePairing,
} from "./pairing";

test("Pairing lifecycle happy path", () => {
  let state = createInitialPairingState();
  assert.equal(state.status, "idle");

  state = reducePairing(state, {
    type: "START_PREPARING",
    requestId: "req-001",
    deviceLabel: "Living Room Tablet",
  });
  assert.equal(state.status, "preparing");
  assert.equal(state.requestId, "req-001");
  assert.equal(isPairingPending(state.status), true);

  state = reducePairing(state, {
    type: "RECEIVE_CHALLENGE",
    requestId: "req-001",
    challengeId: "ch-999",
  });
  assert.equal(state.status, "challenge");
  assert.equal(state.challengeId, "ch-999");
  assert.equal(state.deviceId, null);

  state = reducePairing(state, {
    type: "SUBMIT_CONFIRM",
    challengeId: "ch-999",
  });
  assert.equal(state.status, "confirming");

  state = reducePairing(state, {
    type: "CONFIRM_SUCCESS",
    challengeId: "ch-999",
    deviceId: "device-xyz",
  });
  assert.equal(state.status, "paired");
  assert.equal(state.deviceId, "device-xyz");
  assert.equal(isPairingTerminal(state.status), true);

  state = reducePairing(state, { type: "REVOKE" });
  assert.equal(state.status, "revoked");
});

test("Pairing rejects invalid labels without truncating or rewriting", () => {
  const initial = createInitialPairingState();
  assert.equal(
    reducePairing(initial, {
      type: "START_PREPARING",
      requestId: "req-1",
      deviceLabel: "x".repeat(65),
    }),
    initial
  );
  assert.equal(
    reducePairing(initial, {
      type: "START_PREPARING",
      requestId: "req-1",
      deviceLabel: "unsafe\u200bname",
    }),
    initial
  );
});

test("Stale challenge and request IDs are ignored", () => {
  let state = createInitialPairingState();
  state = reducePairing(state, {
    type: "START_PREPARING",
    requestId: "req-correct",
  });

  // Stale request ID
  const staleChallengeState = reducePairing(state, {
    type: "RECEIVE_CHALLENGE",
    requestId: "req-WRONG",
    challengeId: "ch-001",
  });
  assert.equal(staleChallengeState.status, "preparing"); // Unchanged!

  state = reducePairing(state, {
    type: "RECEIVE_CHALLENGE",
    requestId: "req-correct",
    challengeId: "ch-correct",
  });

  // Stale challenge ID on confirm
  const staleConfirmState = reducePairing(state, {
    type: "SUBMIT_CONFIRM",
    challengeId: "ch-WRONG",
  });
  assert.equal(staleConfirmState.status, "challenge"); // Unchanged!
});

test("Duplicate action calls are suppressed", () => {
  let state = createInitialPairingState();
  state = reducePairing(state, {
    type: "START_PREPARING",
    requestId: "req-1",
  });

  // Duplicate START_PREPARING while preparing
  const dupState = reducePairing(state, {
    type: "START_PREPARING",
    requestId: "req-2",
  });
  assert.equal(dupState.requestId, "req-1");

  state = reducePairing(state, { type: "CANCEL" });
  assert.equal(state.status, "cancelling");

  // Duplicate CANCEL
  const dupCancel = reducePairing(state, { type: "CANCEL" });
  assert.equal(dupCancel.status, "cancelling");
});
