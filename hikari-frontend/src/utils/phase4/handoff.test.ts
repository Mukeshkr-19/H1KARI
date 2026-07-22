/**
 * Unit tests for Phase 4 handoff state machine and reducer.
 */

import assert from "node:assert/strict";
import test from "node:test";
import {
  createInitialHandoffState,
  isHandoffPending,
  isHandoffTerminal,
  reduceHandoff,
} from "./handoff";

test("Handoff offer creation creates a frozen preview copy", () => {
  let state = createInitialHandoffState();
  state = reduceHandoff(state, {
    type: "RECEIVE_OFFER",
    handoffId: "handoff-001",
    taskId: "task-abc.123",
    summary: "Review monthly expense summary",
  });

  assert.equal(state.status, "offered");
  assert.equal(state.handoffId, "handoff-001");
  assert.notEqual(state.preview, null);
  assert.equal(state.preview?.taskId, "task-abc.123");
  assert.equal(state.preview?.summary, "Review monthly expense summary");
  assert.equal(Object.isFrozen(state.preview), true);

  // Attempting to mutate preview throws in strict mode
  assert.throws(() => {
    (state.preview as unknown as Record<string, unknown>).summary = "Hacked summary";
  }, TypeError);
});

test("Accepting requires explicit acknowledgment checkbox", () => {
  let state = createInitialHandoffState();
  state = reduceHandoff(state, {
    type: "RECEIVE_OFFER",
    handoffId: "handoff-002",
    taskId: "task-002",
    summary: "Prepare calendar draft",
  });

  // Attempt accept without acknowledgement -> ignored!
  const unackState = reduceHandoff(state, {
    type: "ACCEPT",
    handoffId: "handoff-002",
  });
  assert.equal(unackState.status, "offered");

  // Toggle acknowledge
  state = reduceHandoff(state, {
    type: "TOGGLE_ACKNOWLEDGE",
    acknowledged: true,
  });
  assert.equal(state.acknowledged, true);

  // Now accept succeeds
  state = reduceHandoff(state, {
    type: "ACCEPT",
    handoffId: "handoff-002",
  });
  assert.equal(state.status, "accepting");
  assert.equal(isHandoffPending(state.status), true);

  state = reduceHandoff(state, {
    type: "ACCEPT_COMPLETE",
    handoffId: "handoff-002",
  });
  assert.equal(state.status, "accepted");
  assert.equal(isHandoffTerminal(state.status), true);

  // Terminal state cannot accept again
  const reaccept = reduceHandoff(state, {
    type: "ACCEPT",
    handoffId: "handoff-002",
  });
  assert.equal(reaccept.status, "accepted");
});

test("Rejection flow works as expected", () => {
  let state = createInitialHandoffState();
  state = reduceHandoff(state, {
    type: "RECEIVE_OFFER",
    handoffId: "handoff-003",
    taskId: "task-003",
    summary: "Send email draft",
  });

  state = reduceHandoff(state, {
    type: "REJECT",
    handoffId: "handoff-003",
  });
  assert.equal(state.status, "rejecting");

  state = reduceHandoff(state, {
    type: "REJECT_COMPLETE",
    handoffId: "handoff-003",
  });
  assert.equal(state.status, "rejected");
  assert.equal(isHandoffTerminal(state.status), true);
});

test("Handoff accepts opaque task IDs without exposing device identity", () => {
  const state = reduceHandoff(createInitialHandoffState(), {
    type: "RECEIVE_OFFER",
    handoffId: "handoff-004",
    taskId: "Task:Opaque-1",
    summary: "Continue this task on the desktop",
  });
  assert.equal(state.status, "offered");
  assert.deepEqual(Object.keys(state.preview ?? {}).sort(), ["summary", "taskId"]);
});
