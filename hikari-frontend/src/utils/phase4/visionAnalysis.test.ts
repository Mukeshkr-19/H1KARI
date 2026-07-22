/**
 * Pure Phase 4 vision analysis primitives and reducer unit tests.
 */

import assert from "node:assert/strict";
import test from "node:test";
import {
  createInitialVisionAnalysisState,
  isVisionAnalysisPending,
  isVisionAnalysisTerminal,
  isValidCanonicalId,
  isValidObservationText,
  reduceVisionAnalysis,
  validateObservation,
  validateObservations,
} from "./visionAnalysis";

test("createInitialVisionAnalysisState returns a frozen initial state", () => {
  const init = createInitialVisionAnalysisState();
  assert.equal(init.status, "idle");
  assert.equal(init.capability, "ocr");
  assert.equal(init.requestId, null);
  assert.equal(init.analysisId, null);
  assert.equal(init.cancelPending, false);
  assert.equal(Object.isFrozen(init), true);
  assert.equal(Object.isFrozen(init.observations), true);
});

test("isValidCanonicalId validates canonical lowercase IDs", () => {
  assert.equal(isValidCanonicalId("req-12345"), true);
  assert.equal(isValidCanonicalId("analysis-001"), true);
  assert.equal(isValidCanonicalId("REQ-12345"), false);
  assert.equal(isValidCanonicalId("bad id!"), false);
  assert.equal(isValidCanonicalId(""), false);
});

test("isValidObservationText enforces code points, controls, Cf, and white space", () => {
  assert.equal(isValidObservationText("Hello world!\nLine 2\tTabbed"), true);
  // Preserves exact text without lowercasing or trimming
  assert.equal(isValidObservationText("  Exact Whitespace Preserved  "), true);

  // Rejects ASCII control \x07 (bell)
  assert.equal(isValidObservationText("Bad \x07 Control"), false);

  // Rejects Unicode Cf character \u200B (zero width space)
  assert.equal(isValidObservationText("Bad \u200B Format"), false);

  // OCR may preserve whitespace; descriptions must contain visible content.
  assert.equal(isValidObservationText("   \n\t  ", "text"), true);
  assert.equal(isValidObservationText("   \n\t  ", "description"), false);
  assert.equal(isValidObservationText("line one\nline two", "description"), false);

  // Rejects empty string
  assert.equal(isValidObservationText(""), false);

  // Rejects > 2000 code points
  const overlong = "a".repeat(2001);
  assert.equal(isValidObservationText(overlong), false);
});

test("validateObservation rejects malformed and unknown properties", () => {
  const valid = validateObservation({
    kind: "text",
    text: "Sample OCR text",
    confidenceMilli: 850,
  });
  assert.notEqual(valid, null);
  assert.equal(valid?.kind, "text");
  assert.equal(valid?.text, "Sample OCR text");
  assert.equal(valid?.confidenceMilli, 850);
  assert.equal(Object.isFrozen(valid), true);

  const withoutConfidence = validateObservation({
    kind: "text",
    text: "No measured confidence",
  });
  assert.notEqual(withoutConfidence, null);
  assert.equal(withoutConfidence?.confidenceMilli, null);

  // Rejects extra unknown fields
  assert.equal(
    validateObservation({
      kind: "text",
      text: "Sample",
      confidenceMilli: 800,
      extraField: "hack",
    }),
    null,
  );

  // Rejects invalid confidence (> 1000)
  assert.equal(
    validateObservation({
      kind: "text",
      text: "Sample",
      confidenceMilli: 1001,
    }),
    null,
  );
});

test("validateObservations bounds count to 1..16", () => {
  const item = { kind: "text", text: "Line", confidenceMilli: 900 };

  assert.equal(validateObservations([]), null);

  const sixteen = Array.from({ length: 16 }, () => item);
  const val16 = validateObservations(sixteen);
  assert.notEqual(val16, null);
  assert.equal(val16?.length, 16);
  assert.equal(Object.isFrozen(val16), true);

  const seventeen = Array.from({ length: 17 }, () => item);
  assert.equal(validateObservations(seventeen), null);
});

test("reduceVisionAnalysis happy path lifecycle", () => {
  let state = createInitialVisionAnalysisState();

  // 1. PREPARE_REQUESTED
  state = reduceVisionAnalysis(state, {
    type: "PREPARE_REQUESTED",
    requestId: "req-001",
    capability: "ocr",
  });
  assert.equal(state.status, "preparing");
  assert.equal(state.requestId, "req-001");
  assert.equal(isVisionAnalysisPending(state.status), true);

  // 2. READY_RECEIVED
  state = reduceVisionAnalysis(state, {
    type: "READY_RECEIVED",
    requestId: "req-001",
    analysisId: "an-001",
  });
  assert.equal(state.status, "awaiting_image");
  assert.equal(state.analysisId, "an-001");

  // 3. IMAGE_ATTACHED
  state = reduceVisionAnalysis(state, {
    type: "IMAGE_ATTACHED",
    requestId: "req-001",
    analysisId: "an-001",
  });
  assert.equal(state.status, "analyzing");

  // 4. OBSERVATION_RECEIVED
  const observations = [
    { kind: "text" as const, text: "Extracted Invoice Total: $120.00", confidenceMilli: 950 },
  ];
  state = reduceVisionAnalysis(state, {
    type: "OBSERVATION_RECEIVED",
    requestId: "req-001",
    analysisId: "an-001",
    observations,
  });
  assert.equal(state.status, "completed");
  assert.equal(state.observations.length, 1);
  assert.equal(state.observations[0].text, "Extracted Invoice Total: $120.00");
  assert.equal(isVisionAnalysisTerminal(state.status), true);
});

test("stale or missing correlation is ignored with identical previous state reference", () => {
  let state = createInitialVisionAnalysisState();
  state = reduceVisionAnalysis(state, {
    type: "PREPARE_REQUESTED",
    requestId: "req-001",
    capability: "ocr",
  });

  // Stale READY_RECEIVED with wrong requestId
  const next = reduceVisionAnalysis(state, {
    type: "READY_RECEIVED",
    requestId: "req-WRONG",
    analysisId: "an-001",
  });
  assert.strictEqual(next, state);
});

test("cancel flow requires correlated server acknowledgement", () => {
  let state = createInitialVisionAnalysisState();
  state = reduceVisionAnalysis(state, {
    type: "PREPARE_REQUESTED",
    requestId: "req-001",
    capability: "describe",
  });

  // Request cancel
  state = reduceVisionAnalysis(state, { type: "CANCEL_REQUESTED" });
  assert.equal(state.status, "preparing");
  assert.equal(state.cancelPending, true);

  // Mismatched CANCEL_CONFIRMED ignored
  const stale = reduceVisionAnalysis(state, {
    type: "CANCEL_CONFIRMED",
    requestId: "req-WRONG",
  });
  assert.strictEqual(stale, state);

  // Correlated CANCEL_CONFIRMED transitions to cancelled
  state = reduceVisionAnalysis(state, {
    type: "CANCEL_CONFIRMED",
    requestId: "req-001",
  });
  assert.equal(state.status, "cancelled");
  assert.equal(state.cancelPending, false);
});

test("terminal states reject later mutations except RESET", () => {
  let state = createInitialVisionAnalysisState();
  state = reduceVisionAnalysis(state, {
    type: "PREPARE_REQUESTED",
    requestId: "req-001",
    capability: "ocr",
  });
  state = reduceVisionAnalysis(state, {
    type: "SAFE_ERROR",
    requestId: "req-001",
    errorCode: "analysis_failed",
  });
  assert.equal(state.status, "failed");
  assert.equal(state.errorCode, "analysis_failed");

  // Attempt mutation on failed state
  const mutated = reduceVisionAnalysis(state, {
    type: "PREPARE_REQUESTED",
    requestId: "req-002",
    capability: "describe",
  });
  assert.strictEqual(mutated, state);

  // RESET restores initial state
  state = reduceVisionAnalysis(state, { type: "RESET" });
  assert.equal(state.status, "idle");
  assert.equal(state.requestId, null);
});
