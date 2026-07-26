/**
 * Test suite for Phase 6 protocol parsing and validation.
 */

import { parsePhase6ServerMessage, buildHomeAssistantConfirmRequest } from "./phase6Protocol";
import assert from "node:assert/strict";
import test from "node:test";

function describe(_name: string, fn: () => void) { fn(); }
function it(name: string, fn: () => void) { test(name, fn); }
function expect(actual: unknown) {
  return {
    toBe(expected: unknown) { assert.equal(actual, expected); },
    toBeNull() { assert.equal(actual, null); },
    not: { toBeNull() { assert.notEqual(actual, null); } },
  };
}

describe("Phase 6 Protocol Parser", () => {
  it("parses valid phase6_integration_status frame", () => {
    const frame = {
      type: "phase6_integration_status",
      request_id: "req_001",
      protocol_version: 1,
      integration_id: "home_assistant",
      name: "Home Assistant",
      status: "ready",
      details_summary: "Connected",
    };
    const parsed = parsePhase6ServerMessage(frame);
    expect(parsed).not.toBeNull();
    expect(parsed?.type).toBe("phase6_integration_status");
    if (parsed?.type === "phase6_integration_status") {
      expect(parsed.integration_id).toBe("home_assistant");
      expect(parsed.status).toBe("ready");
    }
  });

  it("rejects unknown message type or invalid protocol version", () => {
    const frame = {
      type: "phase6_unknown_type",
      request_id: "req_001",
      protocol_version: 1,
    };
    expect(parsePhase6ServerMessage(frame)).toBeNull();

    const badVersion = {
      type: "phase6_integration_status",
      request_id: "req_001",
      protocol_version: 2,
      integration_id: "ha",
      name: "HA",
      status: "ready",
    };
    expect(parsePhase6ServerMessage(badVersion)).toBeNull();
  });

  it("rejects wildcards in Home Assistant proposal entity/domain/service", () => {
    const frame = {
      type: "phase6_home_assistant_proposal",
      request_id: "req_ha_1",
      protocol_version: 1,
      proposal_id: "prop_01",
      entity_id: "light.*",
      domain: "light",
      service: "turn_on",
      risk: "high",
      effect_summary: "Turn on lights",
      expires_at: 1000,
      nonce: "nonce_123",
    };
    expect(parsePhase6ServerMessage(frame)).toBeNull();
  });

  it("rejects Unicode format characters and control characters in safe text", () => {
    const frame = {
      type: "phase6_integration_status",
      request_id: "req_001",
      protocol_version: 1,
      integration_id: "ha",
      name: "Home Assistant\u200e", // Directional mark
      status: "ready",
    };
    expect(parsePhase6ServerMessage(frame)).toBeNull();
  });

  it("builds valid Home Assistant confirm client request", () => {
    const req = buildHomeAssistantConfirmRequest("req_001", "prop_01", "nonce_123");
    expect(req.type).toBe("phase6_home_assistant_confirm_request");
    expect(req.proposal_id).toBe("prop_01");
    expect(req.nonce).toBe("nonce_123");
  });

  it("rejects coerced authority flags and malformed model evidence", () => {
    expect(parsePhase6ServerMessage({
      type: "phase6_encrypted_sync_update", request_id: "req_1", protocol_version: 1,
      enabled: "false", configured: false, status: "disabled", conflict_count: 0,
      exposes_plaintext: false,
    })).toBeNull();
    expect(parsePhase6ServerMessage({
      type: "phase6_model_eval_update", request_id: "req_1", protocol_version: 1,
      candidate_id: "m1", privacy_class: "local_only", capabilities: [{}],
      quality_score: 2, safety_score: 1, latency_ms: -1, recommendation: "use",
    })).toBeNull();
  });

  it("rejects repo count mismatch and unknown nested fields", () => {
    expect(parsePhase6ServerMessage({
      type: "phase6_repo_intel_update", request_id: "req_1", protocol_version: 1,
      scan_state: "done", query_summary: "query", hit_count: 2,
      results: [{ path: "a.py", line: 1, score: 0.5, provenance: "local", secret: "x" }],
    })).toBeNull();
  });
});
