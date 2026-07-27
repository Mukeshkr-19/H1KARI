import assert from "assert";
import {
  INITIAL_PHASE6_STATE,
  reducePhase6State,
} from "./phase6State";
import type {
  Phase6AgentRunUpdate,
  Phase6ErrorMessage,
  Phase6HomeAssistantProposal,
  Phase6IntegrationStatus,
} from "./phase6Protocol";

function test(name: string, fn: () => void) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    console.error(`fail - ${name}`);
    throw error;
  }
}

test("locks submission on begin_request", () => {
  const s1 = reducePhase6State(INITIAL_PHASE6_STATE, {
    type: "phase6/begin_request",
    requestId: "req_001",
  });
  assert.strictEqual(s1.submitLocked, true);
  assert.strictEqual(s1.requestId, "req_001");
  assert.strictEqual(s1.status, "loading");
});

test("updates domain state on correlated server message", () => {
  const s1 = reducePhase6State(INITIAL_PHASE6_STATE, {
    type: "phase6/begin_request",
    requestId: "req_001",
  });

  const msg: Phase6IntegrationStatus = {
    type: "phase6_integration_status",
    request_id: "req_001",
    protocol_version: 1,
    integration_id: "ha",
    name: "Home Assistant",
    status: "ready",
    details_summary: "Connected",
  };

  const s2 = reducePhase6State(s1, {
    type: "phase6/apply_server",
    message: msg,
  });

  assert.strictEqual(s2.status, "ready");
  assert.strictEqual(s2.submitLocked, false);
  assert.strictEqual(s2.integrations.length, 1);
  assert.strictEqual(s2.integrations[0].integration_id, "ha");
});

test("ignores uncorrelated server message from stale request_id", () => {
  const s1 = reducePhase6State(INITIAL_PHASE6_STATE, {
    type: "phase6/begin_request",
    requestId: "req_CURRENT",
  });

  const staleMsg: Phase6AgentRunUpdate = {
    type: "phase6_agent_run_update",
    request_id: "req_OLD_STALE",
    protocol_version: 1,
    run_id: "run_01",
    state: "running",
    step_count: 1,
    action_count: 2,
    budget_limit: 10,
    safe_summary: "Running",
  };

  const s2 = reducePhase6State(s1, {
    type: "phase6/apply_server",
    message: staleMsg,
  });

  assert.strictEqual(s2, s1);
});

test("ignores unsolicited and stale error frames", () => {
  const s1 = reducePhase6State(INITIAL_PHASE6_STATE, {
    type: "phase6/begin_request",
    requestId: "req_ACTIVE",
  });

  const staleErr: Phase6ErrorMessage = {
    type: "phase6_error",
    request_id: "req_STALE",
    protocol_version: 1,
    code: "denied",
    message: "The Phase 6 request was denied.",
  };

  const s2 = reducePhase6State(s1, {
    type: "phase6/apply_server",
    message: staleErr,
  });

  assert.strictEqual(s2, s1);
});

test("requires exact Home Assistant confirmation nonce", () => {
  const sInit = reducePhase6State(INITIAL_PHASE6_STATE, {
    type: "phase6/begin_request",
    requestId: "req_001",
  });

  const haProp: Phase6HomeAssistantProposal = {
    type: "phase6_home_assistant_proposal",
    request_id: "req_001",
    protocol_version: 1,
    proposal_id: "prop_01",
    entity_id: "light.living_room",
    domain: "light",
    service: "turn_on",
    risk: "medium",
    effect_summary: "Turn on light",
    expires_at: 1000,
    nonce: "nonce_EXACT_123",
  };

  const s1 = reducePhase6State(sInit, {
    type: "phase6/apply_server",
    message: haProp,
  });

  const sBadId = reducePhase6State(s1, {
    type: "phase6/confirm_home_assistant",
    proposalId: "WRONG_ID",
    nonce: "nonce_EXACT_123",
  });
  assert.strictEqual(sBadId, s1);

  const sGood = reducePhase6State(s1, {
    type: "phase6/confirm_home_assistant",
    proposalId: "prop_01",
    nonce: "nonce_EXACT_123",
  });
  assert.strictEqual(sGood.confirmedNonce, "nonce_EXACT_123");
  assert.strictEqual(sGood.submitLocked, true);
});

test("clears sensitive proposals and reset data", () => {
  const sInit = reducePhase6State(INITIAL_PHASE6_STATE, {
    type: "phase6/begin_request",
    requestId: "req_001",
  });

  const haProp: Phase6HomeAssistantProposal = {
    type: "phase6_home_assistant_proposal",
    request_id: "req_001",
    protocol_version: 1,
    proposal_id: "prop_01",
    entity_id: "light.living_room",
    domain: "light",
    service: "turn_on",
    risk: "medium",
    effect_summary: "Turn on light",
    expires_at: 1000,
    nonce: "nonce_123",
  };

  const s1 = reducePhase6State(sInit, {
    type: "phase6/apply_server",
    message: haProp,
  });
  assert.ok(s1.haProposal !== null);

  const sCleared = reducePhase6State(s1, { type: "phase6/clear_sensitive" });
  assert.strictEqual(sCleared.haProposal, null);
  assert.strictEqual(sCleared.confirmedNonce, null);

  const sReset = reducePhase6State(s1, { type: "phase6/reset" });
  assert.deepStrictEqual(sReset, INITIAL_PHASE6_STATE);
});

test("controlled state clearing on disconnect and cancel", () => {
  const sInit = reducePhase6State(INITIAL_PHASE6_STATE, {
    type: "phase6/begin_request",
    requestId: "req_001",
  });
  const haProp: Phase6HomeAssistantProposal = {
    type: "phase6_home_assistant_proposal",
    request_id: "req_001",
    protocol_version: 1,
    proposal_id: "prop_01",
    entity_id: "switch.pump",
    domain: "switch",
    service: "turn_on",
    risk: "high",
    effect_summary: "Turn on pump",
    expires_at: 1000,
    nonce: "nonce_456",
  };
  const s1 = reducePhase6State(sInit, { type: "phase6/apply_server", message: haProp });
  assert.notStrictEqual(s1.haProposal, null);

  // Disconnect / cancel clears proposal and resets submission lock
  const sDisc = reducePhase6State(s1, { type: "phase6/clear_sensitive" });
  assert.strictEqual(sDisc.haProposal, null);
  assert.strictEqual(sDisc.confirmedNonce, null);
  assert.strictEqual(sDisc.submitLocked, false);
});
