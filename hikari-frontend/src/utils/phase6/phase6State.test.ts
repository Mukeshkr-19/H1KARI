/**
 * Test suite for Phase 6 UI state reducer.
 */

import {
  INITIAL_PHASE6_STATE,
  reducePhase6State,
} from "./phase6State";
import type {
  Phase6AgentRunUpdate,
  Phase6HomeAssistantProposal,
  Phase6IntegrationStatus,
} from "./phase6Protocol";
import assert from "node:assert/strict";
import test from "node:test";

function describe(_name: string, fn: () => void) { fn(); }
function it(name: string, fn: () => void) { test(name, fn); }
function expect(actual: unknown) {
  return {
    toBe(expected: unknown) { assert.equal(actual, expected); },
    toEqual(expected: unknown) { assert.deepEqual(actual, expected); },
    toBeNull() { assert.equal(actual, null); },
    not: { toBeNull() { assert.notEqual(actual, null); } },
  };
}

describe("Phase 6 State Reducer", () => {
  it("locks submission on begin_request", () => {
    const s1 = reducePhase6State(INITIAL_PHASE6_STATE, {
      type: "phase6/begin_request",
      requestId: "req_001",
    });
    expect(s1.submitLocked).toBe(true);
    expect(s1.requestId).toBe("req_001");
    expect(s1.status).toBe("loading");
  });

  it("updates domain state on correlated server message", () => {
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

    expect(s2.status).toBe("ready");
    expect(s2.submitLocked).toBe(false);
    expect(s2.integrations.length).toBe(1);
    expect(s2.integrations[0].integration_id).toBe("ha");
  });

  it("ignores uncorrelated server message from stale request_id", () => {
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

    expect(s2).toBe(s1);
  });

  it("clears sensitive proposals and reset data", () => {
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

    const active = reducePhase6State(INITIAL_PHASE6_STATE, {
      type: "phase6/begin_request",
      requestId: "req_001",
    });
    const s1 = reducePhase6State(active, {
      type: "phase6/apply_server",
      message: haProp,
    });
    expect(s1.haProposal).not.toBeNull();

    const sCleared = reducePhase6State(s1, { type: "phase6/clear_sensitive" });
    expect(sCleared.haProposal).toBeNull();
    expect(sCleared.confirmedNonce).toBeNull();

    const sReset = reducePhase6State(s1, { type: "phase6/reset" });
    expect(sReset).toEqual(INITIAL_PHASE6_STATE);
  });

  it("ignores unsolicited and stale error frames", () => {
    const msg = {
      type: "phase6_error" as const, request_id: "req_old", protocol_version: 1 as const,
      code: "denied" as const, message: "ignored",
    };
    expect(reducePhase6State(INITIAL_PHASE6_STATE, { type: "phase6/apply_server", message: msg })).toBe(INITIAL_PHASE6_STATE);
    const active = reducePhase6State(INITIAL_PHASE6_STATE, { type: "phase6/begin_request", requestId: "req_new" });
    expect(reducePhase6State(active, { type: "phase6/apply_server", message: msg })).toBe(active);
  });

  it("requires exact Home Assistant confirmation nonce", () => {
    const active = reducePhase6State(INITIAL_PHASE6_STATE, { type: "phase6/begin_request", requestId: "req_1" });
    const proposal: Phase6HomeAssistantProposal = {
      type: "phase6_home_assistant_proposal", request_id: "req_1", protocol_version: 1,
      proposal_id: "p1", entity_id: "light.one", domain: "light", service: "turn_on",
      risk: "medium", effect_summary: "Turn on", expires_at: 10, nonce: "correct",
    };
    const withProposal = reducePhase6State(active, { type: "phase6/apply_server", message: proposal });
    expect(reducePhase6State(withProposal, { type: "phase6/confirm_home_assistant", proposalId: "p1", nonce: "wrong" })).toBe(withProposal);
  });
});
