/** Pure Phase 3 approval-scope selection helpers. No transport, storage, or timers. */

export const APPROVAL_SCOPES = [
  "once",
  "session",
  "duration",
  "precise_persistent",
] as const;

export type ApprovalScopeKind = (typeof APPROVAL_SCOPES)[number];

export const DEFAULT_APPROVAL_SCOPE: ApprovalScopeKind = "once";

export const APPROVAL_DURATION_CHOICES = [
  "15_minutes",
  "1_hour",
  "8_hours",
] as const;

export type ApprovalDurationChoice = (typeof APPROVAL_DURATION_CHOICES)[number];

export const APPROVAL_DURATION_SECONDS = {
  "15_minutes": 900,
  "1_hour": 3600,
  "8_hours": 28800,
} as const;

export type ApprovalDurationSeconds =
  (typeof APPROVAL_DURATION_SECONDS)[ApprovalDurationChoice];

export type ApprovalScopeState = Readonly<{
  allowedScopes: ReadonlyArray<ApprovalScopeKind>;
  scope: ApprovalScopeKind;
  duration: ApprovalDurationChoice | null;
  persistentAcknowledged: boolean;
}>;

export const APPROVAL_SCOPE_BINDING_DESCRIPTION =
  "Approval is bound to the exact action and destination shown above.";

export const APPROVAL_PERSISTENT_WARNING =
  "I understand this precise approval can remain until revoked and applies only to the exact action and destination shown.";

const SCOPE_LABELS: Record<ApprovalScopeKind, string> = {
  once: "Once",
  session: "This session",
  duration: "Limited duration",
  precise_persistent: "Precise persistent",
};

const DURATION_LABELS: Record<ApprovalDurationChoice, string> = {
  "15_minutes": "15 minutes",
  "1_hour": "1 hour",
  "8_hours": "8 hours",
};

const SCOPE_SET = new Set<string>(APPROVAL_SCOPES);
const DURATION_SET = new Set<string>(APPROVAL_DURATION_CHOICES);
const DURATION_SECONDS_SET = new Set<number>([900, 3600, 28800]);

export function isApprovalScopeKind(value: unknown): value is ApprovalScopeKind {
  return typeof value === "string" && SCOPE_SET.has(value);
}

export function isApprovalDurationChoice(
  value: unknown,
): value is ApprovalDurationChoice {
  return typeof value === "string" && DURATION_SET.has(value);
}

export function isApprovalDurationSeconds(
  value: unknown,
): value is ApprovalDurationSeconds {
  return typeof value === "number" && DURATION_SECONDS_SET.has(value);
}

export function parseAllowedApprovalScopes(
  value: unknown,
): ReadonlyArray<ApprovalScopeKind> | null {
  if (
    !Array.isArray(value) ||
    value.length < 1 ||
    value.length > APPROVAL_SCOPES.length
  ) {
    return null;
  }
  const seen = new Set<string>();
  const scopes: ApprovalScopeKind[] = [];
  for (const item of value) {
    if (!isApprovalScopeKind(item) || seen.has(item)) {
      return null;
    }
    seen.add(item);
    scopes.push(item);
  }
  return Object.freeze(scopes);
}

export function createInitialApprovalScopeState(): ApprovalScopeState {
  return Object.freeze({
    allowedScopes: Object.freeze(["once"] as ApprovalScopeKind[]),
    scope: DEFAULT_APPROVAL_SCOPE,
    duration: null,
    persistentAcknowledged: false,
  });
}

export function resetApprovalScopeState(): ApprovalScopeState {
  return createInitialApprovalScopeState();
}

export function createApprovalScopeStateFromAllowed(
  allowed: unknown,
): ApprovalScopeState | null {
  const allowedScopes = parseAllowedApprovalScopes(allowed);
  if (!allowedScopes) {
    return null;
  }
  const scope = allowedScopes.includes("once")
    ? "once"
    : allowedScopes[0];
  return Object.freeze({
    allowedScopes,
    scope,
    duration: null,
    persistentAcknowledged: false,
  });
}

export function approvalScopeLabel(scope: ApprovalScopeKind): string {
  return SCOPE_LABELS[scope];
}

export function approvalDurationLabel(duration: ApprovalDurationChoice): string {
  return DURATION_LABELS[duration];
}

export function approvalDurationSeconds(
  duration: ApprovalDurationChoice,
): ApprovalDurationSeconds {
  return APPROVAL_DURATION_SECONDS[duration];
}

export function approvalDurationChoiceFromSeconds(
  value: unknown,
): ApprovalDurationChoice | null {
  if (!isApprovalDurationSeconds(value)) {
    return null;
  }
  for (const choice of APPROVAL_DURATION_CHOICES) {
    if (APPROVAL_DURATION_SECONDS[choice] === value) {
      return choice;
    }
  }
  return null;
}

export function selectApprovalScope(
  state: ApprovalScopeState,
  scope: unknown,
): ApprovalScopeState | null {
  if (!isApprovalScopeKind(scope) || !state.allowedScopes.includes(scope)) {
    return null;
  }
  return Object.freeze({
    allowedScopes: state.allowedScopes,
    scope,
    duration: null,
    persistentAcknowledged: false,
  });
}

export function selectApprovalDuration(
  state: ApprovalScopeState,
  duration: unknown,
): ApprovalScopeState | null {
  if (state.scope !== "duration") {
    return null;
  }
  if (!state.allowedScopes.includes("duration")) {
    return null;
  }
  if (!isApprovalDurationChoice(duration)) {
    return null;
  }
  return Object.freeze({
    allowedScopes: state.allowedScopes,
    scope: "duration" as const,
    duration,
    persistentAcknowledged: false,
  });
}

export function setPersistentAcknowledgement(
  state: ApprovalScopeState,
  acknowledged: unknown,
): ApprovalScopeState | null {
  if (state.scope !== "precise_persistent") {
    return null;
  }
  if (!state.allowedScopes.includes("precise_persistent")) {
    return null;
  }
  if (typeof acknowledged !== "boolean") {
    return null;
  }
  return Object.freeze({
    allowedScopes: state.allowedScopes,
    scope: "precise_persistent" as const,
    duration: null,
    persistentAcknowledged: acknowledged,
  });
}

export function isApprovalScopeConfirmReady(state: ApprovalScopeState): boolean {
  if (!state.allowedScopes.includes(state.scope)) {
    return false;
  }
  switch (state.scope) {
    case "once":
    case "session":
      return true;
    case "duration":
      return isApprovalDurationChoice(state.duration);
    case "precise_persistent":
      return state.persistentAcknowledged === true;
    default:
      return false;
  }
}
