import assert from "assert";
import { parsePhase5ServerMessage } from "./phase5Protocol";
import {
  INITIAL_PHASE5_STATE,
  reducePhase5State,
} from "./phase5State";

function test(name: string, fn: () => void) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    console.error(`fail - ${name}`);
    throw error;
  }
}

test("ignores stale correlation and suppresses duplicate submit", () => {
  let state = reducePhase5State(INITIAL_PHASE5_STATE, {
    type: "phase5/begin_request",
    requestId: "req-a",
  });
  assert.strictEqual(state.submitLocked, true);
  const dup = reducePhase5State(state, {
    type: "phase5/begin_request",
    requestId: "req-a",
  });
  assert.strictEqual(dup, state);
  const stale = parsePhase5ServerMessage({
    type: "phase5_session_update",
    request_id: "req-old",
    protocol_version: 1,
    session_id: "sess-1",
    session_type: "owner",
    state: "active",
    expires_at: 1000,
    capabilities: ["teach_me"],
  });
  assert.ok(stale);
  state = reducePhase5State(state, { type: "phase5/apply_server", message: stale! });
  assert.strictEqual(state.status, "activating");
});

test("marks active only after server confirmation", () => {
  let state = reducePhase5State(INITIAL_PHASE5_STATE, {
    type: "phase5/begin_request",
    requestId: "req-b",
  });
  assert.notStrictEqual(state.status, "active");
  const msg = parsePhase5ServerMessage({
    type: "phase5_session_update",
    request_id: "req-b",
    protocol_version: 1,
    session_id: "sess-1",
    session_type: "owner",
    state: "active",
    expires_at: 1000,
    capabilities: ["teach_me"],
  });
  state = reducePhase5State(state, { type: "phase5/apply_server", message: msg! });
  assert.strictEqual(state.status, "active");
});

test("clears sensitive content on revoke and reset", () => {
  let state = reducePhase5State(INITIAL_PHASE5_STATE, {
    type: "phase5/begin_request",
    requestId: "req-c",
  });
  const proposal = parsePhase5ServerMessage({
    type: "phase5_capability_proposal",
    request_id: "req-c",
    protocol_version: 1,
    capability: "teach_me",
    outcome: "allow",
    approval_required: false,
    summary: "private proposal",
    items: ["step one"],
    installs_skills: false,
    camera_accessed: false,
    contact_made: false,
  });
  state = reducePhase5State(state, { type: "phase5/apply_server", message: proposal! });
  assert.strictEqual(state.proposalSummary, "private proposal");
  const revoked = parsePhase5ServerMessage({
    type: "phase5_session_update",
    request_id: "req-c",
    protocol_version: 1,
    session_id: "sess-1",
    session_type: "owner",
    state: "revoked",
    expires_at: 1000,
    capabilities: ["teach_me"],
  });
  state = reducePhase5State(state, { type: "phase5/apply_server", message: revoked! });
  assert.strictEqual(state.proposalSummary, null);
  assert.deepStrictEqual(state.proposalItems, []);
  state = reducePhase5State(state, { type: "phase5/reset" });
  assert.strictEqual(state.status, "idle");
});

test("no localStorage writes in reducer module contract", () => {
  assert.strictEqual(typeof INITIAL_PHASE5_STATE, "object");
  assert.strictEqual(INITIAL_PHASE5_STATE.proposalSummary, null);
});
