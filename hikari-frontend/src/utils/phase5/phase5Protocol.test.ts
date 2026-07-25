import assert from "assert";
import {
  parsePhase5ServerMessage,
  PHASE5_SAFE_ERROR_MESSAGES,
} from "./phase5Protocol";

function test(name: string, fn: () => void) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    console.error(`fail - ${name}`);
    throw error;
  }
}

test("parses session update and rejects unknown fields", () => {
  const ok = parsePhase5ServerMessage({
    type: "phase5_session_update",
    request_id: "req-1",
    protocol_version: 1,
    session_id: "sess-1",
    session_type: "owner",
    state: "active",
    expires_at: 1000,
    capabilities: ["teach_me"],
  });
  assert.ok(ok);
  assert.strictEqual(ok?.type, "phase5_session_update");
  assert.ok(Object.isFrozen(ok));
  assert.strictEqual(
    parsePhase5ServerMessage({
      type: "phase5_session_update",
      request_id: "req-1",
      protocol_version: 1,
      session_id: "sess-1",
      session_type: "owner",
      state: "active",
      expires_at: 1000,
      capabilities: ["teach_me"],
      actor_id: "x",
    }),
    null,
  );
});

test("proposal never claims install/camera/contact", () => {
  const msg = parsePhase5ServerMessage({
    type: "phase5_capability_proposal",
    request_id: "req-2",
    protocol_version: 1,
    capability: "care",
    outcome: "allow",
    approval_required: true,
    summary: "Care proposal ready. Supportive assistance only.",
    items: ["You are not alone."],
    installs_skills: false,
    camera_accessed: false,
    contact_made: false,
    emergency_limitation: "HIKARI does not contact emergency services.",
  });
  assert.ok(msg && msg.type === "phase5_capability_proposal");
  if (msg && msg.type === "phase5_capability_proposal") {
    assert.strictEqual(msg.installs_skills, false);
    assert.strictEqual(msg.camera_accessed, false);
    assert.strictEqual(msg.contact_made, false);
  }
  assert.strictEqual(
    parsePhase5ServerMessage({
      type: "phase5_capability_proposal",
      request_id: "req-2",
      protocol_version: 1,
      capability: "care",
      outcome: "allow",
      approval_required: true,
      summary: "x",
      items: [],
      installs_skills: true,
      camera_accessed: false,
      contact_made: false,
    }),
    null,
  );
});

test("safe error codes only", () => {
  const msg = parsePhase5ServerMessage({
    type: "phase5_error",
    request_id: "req-3",
    protocol_version: 1,
    code: "denied",
  });
  assert.ok(msg && msg.type === "phase5_error");
  assert.ok(PHASE5_SAFE_ERROR_MESSAGES.denied.length > 0);
  assert.strictEqual(
    parsePhase5ServerMessage({
      type: "phase5_error",
      request_id: "req-3",
      protocol_version: 1,
      code: "denied",
      message: "raw exception",
    }),
    null,
  );
});
