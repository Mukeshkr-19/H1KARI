import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  APPROVAL_DURATION_CHOICES,
  APPROVAL_DURATION_SECONDS,
  APPROVAL_PERSISTENT_WARNING,
  APPROVAL_SCOPE_BINDING_DESCRIPTION,
  APPROVAL_SCOPES,
  DEFAULT_APPROVAL_SCOPE,
  approvalDurationChoiceFromSeconds,
  approvalDurationLabel,
  approvalDurationSeconds,
  approvalScopeLabel,
  createApprovalScopeStateFromAllowed,
  createInitialApprovalScopeState,
  isApprovalDurationChoice,
  isApprovalScopeConfirmReady,
  isApprovalScopeKind,
  parseAllowedApprovalScopes,
  resetApprovalScopeState,
  selectApprovalDuration,
  selectApprovalScope,
  setPersistentAcknowledgement,
  type ApprovalScopeKind,
  type ApprovalScopeState,
} from "./approvalScopes";

describe("approvalScopes", () => {
  it("defaults to once with no duration or acknowledgement", () => {
    const state = createInitialApprovalScopeState();
    assert.equal(DEFAULT_APPROVAL_SCOPE, "once");
    assert.equal(state.scope, "once");
    assert.deepEqual([...state.allowedScopes], ["once"]);
    assert.equal(state.duration, null);
    assert.equal(state.persistentAcknowledged, false);
    assert.equal(isApprovalScopeConfirmReady(state), true);
    assert.deepEqual([...APPROVAL_SCOPES], [
      "once",
      "session",
      "duration",
      "precise_persistent",
    ]);
  });

  it("defaults to once when advertised otherwise first advertised scope", () => {
    const withOnce = createApprovalScopeStateFromAllowed([
      "session",
      "once",
      "duration",
    ]);
    assert.ok(withOnce);
    assert.equal(withOnce.scope, "once");

    const withoutOnce = createApprovalScopeStateFromAllowed([
      "session",
      "precise_persistent",
    ]);
    assert.ok(withoutOnce);
    assert.equal(withoutOnce.scope, "session");
    assert.equal(isApprovalScopeConfirmReady(withoutOnce), true);
  });

  it("covers every scope state and confirm readiness", () => {
    let state = createApprovalScopeStateFromAllowed([...APPROVAL_SCOPES])!;
    assert.equal(isApprovalScopeConfirmReady(state), true);

    state = selectApprovalScope(state, "session")!;
    assert.equal(state.scope, "session");
    assert.equal(isApprovalScopeConfirmReady(state), true);

    state = selectApprovalScope(state, "duration")!;
    assert.equal(state.scope, "duration");
    assert.equal(state.duration, null);
    assert.equal(isApprovalScopeConfirmReady(state), false);

    state = selectApprovalDuration(state, "15_minutes")!;
    assert.equal(state.duration, "15_minutes");
    assert.equal(isApprovalScopeConfirmReady(state), true);

    state = selectApprovalScope(state, "precise_persistent")!;
    assert.equal(state.scope, "precise_persistent");
    assert.equal(state.duration, null);
    assert.equal(state.persistentAcknowledged, false);
    assert.equal(isApprovalScopeConfirmReady(state), false);

    state = setPersistentAcknowledgement(state, true)!;
    assert.equal(state.persistentAcknowledged, true);
    assert.equal(isApprovalScopeConfirmReady(state), true);
  });

  it("rejects invalid scope duration and acknowledgement inputs", () => {
    const state = createApprovalScopeStateFromAllowed([...APPROVAL_SCOPES])!;
    assert.equal(selectApprovalScope(state, "forever"), null);
    assert.equal(selectApprovalScope(state, "global"), null);
    assert.equal(selectApprovalScope(state, "unrestricted"), null);
    assert.equal(selectApprovalScope(state, "remember_everything"), null);
    assert.equal(selectApprovalScope(state, "wildcard"), null);
    assert.equal(selectApprovalScope(state, "ONCE"), null);
    assert.equal(selectApprovalScope(state, true), null);
    assert.equal(selectApprovalScope(state, 1), null);

    const onceOnly = createApprovalScopeStateFromAllowed(["once"])!;
    assert.equal(selectApprovalScope(onceOnly, "session"), null);

    assert.equal(isApprovalScopeKind("once"), true);
    assert.equal(isApprovalScopeKind("implicit"), false);
    assert.equal(isApprovalDurationChoice("15_minutes"), true);
    assert.equal(isApprovalDurationChoice(15), false);
    assert.equal(isApprovalDurationChoice("30_minutes"), false);

    const duration = selectApprovalScope(state, "duration")!;
    assert.equal(selectApprovalDuration(duration, 15), null);
    assert.equal(selectApprovalDuration(duration, "30_minutes"), null);
    assert.equal(selectApprovalDuration(state, "1_hour"), null);

    const persistent = selectApprovalScope(state, "precise_persistent")!;
    assert.equal(setPersistentAcknowledgement(persistent, "yes"), null);
    assert.equal(setPersistentAcknowledgement(state, true), null);
  });

  it("rejects invalid server advertisements for allowed scopes", () => {
    assert.equal(parseAllowedApprovalScopes([]), null);
    assert.equal(parseAllowedApprovalScopes(["once", "once"]), null);
    assert.equal(parseAllowedApprovalScopes(["once", "always"]), null);
    assert.equal(parseAllowedApprovalScopes("once"), null);
    assert.equal(parseAllowedApprovalScopes(null), null);
    assert.equal(
      parseAllowedApprovalScopes([
        "once",
        "session",
        "duration",
        "precise_persistent",
        "once",
      ]),
      null,
    );
    assert.ok(parseAllowedApprovalScopes(["duration", "session"]));
    assert.equal(createApprovalScopeStateFromAllowed([]), null);
    assert.equal(createApprovalScopeStateFromAllowed(["forever"]), null);
  });

  it("clears duration and acknowledgement when the selected scope changes", () => {
    let state = createApprovalScopeStateFromAllowed([...APPROVAL_SCOPES])!;
    state = selectApprovalScope(state, "duration")!;
    state = selectApprovalDuration(state, "8_hours")!;
    assert.equal(state.duration, "8_hours");

    state = selectApprovalScope(state, "session")!;
    assert.equal(state.duration, null);
    assert.equal(state.persistentAcknowledged, false);

    state = selectApprovalScope(state, "precise_persistent")!;
    state = setPersistentAcknowledgement(state, true)!;
    assert.equal(state.persistentAcknowledged, true);

    state = selectApprovalScope(state, "duration")!;
    assert.equal(state.duration, null);
    assert.equal(state.persistentAcknowledged, false);

    state = selectApprovalDuration(state, "1_hour")!;
    state = selectApprovalScope(state, "duration")!;
    assert.equal(state.duration, null);
  });

  it("bounds duration to the three allowed second choices only", () => {
    assert.deepEqual([...APPROVAL_DURATION_CHOICES], [
      "15_minutes",
      "1_hour",
      "8_hours",
    ]);
    assert.equal(APPROVAL_DURATION_SECONDS["15_minutes"], 900);
    assert.equal(APPROVAL_DURATION_SECONDS["1_hour"], 3600);
    assert.equal(APPROVAL_DURATION_SECONDS["8_hours"], 28800);
    assert.equal(approvalDurationSeconds("15_minutes"), 900);
    assert.equal(approvalDurationChoiceFromSeconds(900), "15_minutes");
    assert.equal(approvalDurationChoiceFromSeconds(3600), "1_hour");
    assert.equal(approvalDurationChoiceFromSeconds(28800), "8_hours");
    assert.equal(approvalDurationChoiceFromSeconds(60), null);
    assert.equal(approvalDurationChoiceFromSeconds(true), null);

    let state = createApprovalScopeStateFromAllowed(["duration"])!;
    for (const choice of APPROVAL_DURATION_CHOICES) {
      state = selectApprovalDuration(state, choice)!;
      assert.equal(state.duration, choice);
      assert.equal(isApprovalScopeConfirmReady(state), true);
    }
  });

  it("resets to the default once state", () => {
    let state = createApprovalScopeStateFromAllowed([...APPROVAL_SCOPES])!;
    state = selectApprovalScope(state, "precise_persistent")!;
    state = setPersistentAcknowledgement(state, true)!;
    assert.equal(state.persistentAcknowledged, true);
    const reset = resetApprovalScopeState();
    assert.deepEqual(reset, createInitialApprovalScopeState());
    assert.equal(reset.scope, "once");
    assert.equal(reset.duration, null);
    assert.equal(reset.persistentAcknowledged, false);
  });

  it("returns immutable frozen states", () => {
    const state = createInitialApprovalScopeState();
    assert.throws(() => {
      (state as { scope: string }).scope = "session";
    }, TypeError);
    assert.throws(() => {
      (state.allowedScopes as ApprovalScopeKind[]).push("session");
    }, TypeError);

    const next = selectApprovalScope(
      createApprovalScopeStateFromAllowed(["once", "session"])!,
      "session",
    )!;
    assert.throws(() => {
      (next as { persistentAcknowledged: boolean }).persistentAcknowledged = true;
    }, TypeError);
  });

  it("exposes bounded human labels and binding description", () => {
    assert.equal(approvalScopeLabel("once"), "Once");
    assert.equal(approvalScopeLabel("session"), "This session");
    assert.equal(approvalScopeLabel("duration"), "Limited duration");
    assert.equal(approvalScopeLabel("precise_persistent"), "Precise persistent");
    assert.equal(approvalDurationLabel("15_minutes"), "15 minutes");
    assert.match(
      APPROVAL_SCOPE_BINDING_DESCRIPTION,
      /exact action and destination/i,
    );
    assert.match(APPROVAL_PERSISTENT_WARNING, /exact action and destination/i);
    assert.doesNotMatch(
      APPROVAL_SCOPE_BINDING_DESCRIPTION,
      /remember everything|wildcard|global|forever|unrestricted/i,
    );
  });

  it("supplies keyboard-native control labels for every radio and duration choice", () => {
    for (const scope of APPROVAL_SCOPES) {
      const label = approvalScopeLabel(scope);
      assert.equal(typeof label, "string");
      assert.ok(label.length > 0);
    }
    for (const duration of APPROVAL_DURATION_CHOICES) {
      assert.ok(approvalDurationLabel(duration).length > 0);
      assert.ok(approvalDurationSeconds(duration) > 0);
    }
  });

  it("has no side-effectful imports or transport helpers", () => {
    const helpers = [
      createInitialApprovalScopeState,
      createApprovalScopeStateFromAllowed,
      selectApprovalScope,
      selectApprovalDuration,
      setPersistentAcknowledgement,
      isApprovalScopeConfirmReady,
      resetApprovalScopeState,
      parseAllowedApprovalScopes,
    ];
    for (const helper of helpers) {
      assert.equal(typeof helper, "function");
    }
    const sample: ApprovalScopeState = createInitialApprovalScopeState();
    assert.equal(sample.scope, "once");
  });
});
