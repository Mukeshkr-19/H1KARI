import assert from "assert";
import {
  parsePhase6ServerMessage,
  buildHomeAssistantConfirmRequest,
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

test("parses valid phase6_integration_status frame", () => {
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
  assert.ok(parsed !== null);
  assert.strictEqual(parsed?.type, "phase6_integration_status");
  if (parsed?.type === "phase6_integration_status") {
    assert.strictEqual(parsed.integration_id, "home_assistant");
    assert.strictEqual(parsed.status, "ready");
  }
});

test("rejects unknown message type or invalid protocol version", () => {
  const frame = {
    type: "phase6_unknown_type",
    request_id: "req_001",
    protocol_version: 1,
  };
  assert.strictEqual(parsePhase6ServerMessage(frame), null);

  const badVersion = {
    type: "phase6_integration_status",
    request_id: "req_001",
    protocol_version: 2,
    integration_id: "ha",
    name: "HA",
    status: "ready",
  };
  assert.strictEqual(parsePhase6ServerMessage(badVersion), null);
});

test("rejects wildcards in Home Assistant proposal entity/domain/service", () => {
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
  assert.strictEqual(parsePhase6ServerMessage(frame), null);
});

test("rejects Unicode format characters and control characters in safe text", () => {
  const frame = {
    type: "phase6_integration_status",
    request_id: "req_001",
    protocol_version: 1,
    integration_id: "ha",
    name: "Home Assistant\u200e", // Directional mark
    status: "ready",
  };
  assert.strictEqual(parsePhase6ServerMessage(frame), null);
});

test("builds valid Home Assistant confirm client request", () => {
  const req = buildHomeAssistantConfirmRequest("req_001", "prop_01", "nonce_123");
  assert.strictEqual(req.type, "phase6_home_assistant_confirm_request");
  assert.strictEqual(req.proposal_id, "prop_01");
  assert.strictEqual(req.nonce, "nonce_123");
});

test("rejects coerced authority flags and malformed model evidence", () => {
  // allows_auto_install must be strictly false
  const badSkillFrame = {
    type: "phase6_skill_evolution_update",
    request_id: "req_001",
    protocol_version: 1,
    package_id: "pkg_1",
    version: "1.0.0",
    state: "review",
    permissions_summary: [],
    rollback_ready: true,
    allows_auto_install: true,
  };
  assert.strictEqual(parsePhase6ServerMessage(badSkillFrame), null);

  // verified_local_authority must be strictly false
  const badWorkerFrame = {
    type: "phase6_remote_worker_update",
    request_id: "req_001",
    protocol_version: 1,
    job_id: "job_1",
    worker_id: "w_1",
    state: "quarantined",
    has_evidence: true,
    quarantined: true,
    verified_local_authority: true,
  };
  assert.strictEqual(parsePhase6ServerMessage(badWorkerFrame), null);

  // exposes_plaintext must be strictly false
  const badSyncFrame = {
    type: "phase6_encrypted_sync_update",
    request_id: "req_001",
    protocol_version: 1,
    enabled: true,
    configured: true,
    status: "synced",
    conflict_count: 0,
    exposes_plaintext: true,
  };
  assert.strictEqual(parsePhase6ServerMessage(badSyncFrame), null);

  // NaN or non-finite scores rejected
  const badModelFrame = {
    type: "phase6_model_eval_update",
    request_id: "req_001",
    protocol_version: 1,
    candidate_id: "cand_1",
    privacy_class: "local_only",
    capabilities: ["text_gen"],
    quality_score: NaN,
    safety_score: 0.9,
    latency_ms: 100,
    recommendation: "Rec",
  };
  assert.strictEqual(parsePhase6ServerMessage(badModelFrame), null);
});

test("rejects repo count mismatch and unknown nested fields", () => {
  // hit_count mismatch
  const badRepoHitCount = {
    type: "phase6_repo_intel_update",
    request_id: "req_001",
    protocol_version: 1,
    scan_state: "scanned",
    query_summary: "query",
    hit_count: 10,
    results: [{ path: "file.py", line: 1, score: 0.9, provenance: "repo" }],
  };
  assert.strictEqual(parsePhase6ServerMessage(badRepoHitCount), null);

  // Unknown field inside repo hit
  const unknownFieldHit = {
    type: "phase6_repo_intel_update",
    request_id: "req_001",
    protocol_version: 1,
    scan_state: "scanned",
    query_summary: "query",
    hit_count: 1,
    results: [{ path: "file.py", line: 1, score: 0.9, provenance: "repo", unknown_key: "bad" }],
  };
  assert.strictEqual(parsePhase6ServerMessage(unknownFieldHit), null);
});
