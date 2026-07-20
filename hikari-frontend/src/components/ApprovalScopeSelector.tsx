"use client";

import { useId } from "react";
import {
  APPROVAL_DURATION_CHOICES,
  APPROVAL_PERSISTENT_WARNING,
  APPROVAL_SCOPE_BINDING_DESCRIPTION,
  approvalDurationLabel,
  approvalScopeLabel,
  isApprovalScopeConfirmReady,
  selectApprovalDuration,
  selectApprovalScope,
  setPersistentAcknowledgement,
  type ApprovalDurationChoice,
  type ApprovalScopeKind,
  type ApprovalScopeState,
} from "@/utils/productivity/approvalScopes";

export type ApprovalScopeSelectorProps = {
  state: ApprovalScopeState;
  onChange: (next: ApprovalScopeState) => void;
  disabled?: boolean;
};

export function ApprovalScopeSelector({
  state,
  onChange,
  disabled = false,
}: ApprovalScopeSelectorProps) {
  const instanceId = useId();
  const legendId = `${instanceId}-legend`;
  const descriptionId = `${instanceId}-description`;
  const durationLegendId = `${instanceId}-duration-legend`;
  const ackId = `${instanceId}-persistent-ack`;
  const ackWarningId = `${instanceId}-persistent-warning`;
  const groupName = `${instanceId}-scope`;
  const durationGroupName = `${instanceId}-duration`;
  const confirmReady = isApprovalScopeConfirmReady(state);

  const applyScope = (scope: ApprovalScopeKind) => {
    const next = selectApprovalScope(state, scope);
    if (next) {
      onChange(next);
    }
  };

  const applyDuration = (duration: ApprovalDurationChoice) => {
    const next = selectApprovalDuration(state, duration);
    if (next) {
      onChange(next);
    }
  };

  const applyAcknowledgement = (acknowledged: boolean) => {
    const next = setPersistentAcknowledgement(state, acknowledged);
    if (next) {
      onChange(next);
    }
  };

  return (
    <section
      className="mt-4 rounded-xl border border-gray-800 bg-[#1a1a2e] p-4"
      aria-labelledby={legendId}
    >
      <fieldset disabled={disabled} className="m-0 min-w-0 border-0 p-0">
        <legend id={legendId} className="px-0 text-lg font-semibold text-gray-100">
          Approval scope
        </legend>
        <p id={descriptionId} className="mt-2 text-sm text-gray-400">
          {APPROVAL_SCOPE_BINDING_DESCRIPTION}
        </p>

        <div
          role="radiogroup"
          aria-labelledby={legendId}
          aria-describedby={descriptionId}
          className="mt-4 space-y-2"
        >
          {state.allowedScopes.map((scope) => {
            const optionId = `${instanceId}-scope-${scope}`;
            return (
              <div key={scope} className="flex items-start gap-2">
                <input
                  id={optionId}
                  type="radio"
                  name={groupName}
                  value={scope}
                  checked={state.scope === scope}
                  disabled={disabled}
                  onChange={() => applyScope(scope)}
                  className="mt-1"
                />
                <label htmlFor={optionId} className="text-sm text-gray-100">
                  {approvalScopeLabel(scope)}
                </label>
              </div>
            );
          })}
        </div>

        {state.scope === "duration" ? (
          <fieldset
            disabled={disabled}
            className="mt-4 min-w-0 rounded-lg border border-gray-700 p-3"
          >
            <legend
              id={durationLegendId}
              className="px-1 text-sm font-medium text-gray-200"
            >
              Duration
            </legend>
            <div
              role="radiogroup"
              aria-labelledby={durationLegendId}
              className="mt-2 space-y-2"
            >
              {APPROVAL_DURATION_CHOICES.map((duration) => {
                const optionId = `${instanceId}-duration-${duration}`;
                return (
                  <div key={duration} className="flex items-start gap-2">
                    <input
                      id={optionId}
                      type="radio"
                      name={durationGroupName}
                      value={duration}
                      checked={state.duration === duration}
                      disabled={disabled}
                      onChange={() => applyDuration(duration)}
                      className="mt-1"
                    />
                    <label htmlFor={optionId} className="text-sm text-gray-100">
                      {approvalDurationLabel(duration)}
                    </label>
                  </div>
                );
              })}
            </div>
          </fieldset>
        ) : null}

        {state.scope === "precise_persistent" ? (
          <div className="mt-4 rounded-lg border border-amber-700/50 bg-amber-950/20 p-3">
            <div className="flex items-start gap-2">
              <input
                id={ackId}
                type="checkbox"
                checked={state.persistentAcknowledged}
                disabled={disabled}
                aria-describedby={ackWarningId}
                onChange={(event) => applyAcknowledgement(event.target.checked)}
                className="mt-1"
              />
              <label htmlFor={ackId} id={ackWarningId} className="text-sm text-amber-100">
                {APPROVAL_PERSISTENT_WARNING}
              </label>
            </div>
          </div>
        ) : null}
      </fieldset>

      <p className="sr-only" role="status" aria-live="polite">
        {confirmReady
          ? "Approval scope is ready to confirm."
          : "Approval scope is not ready to confirm."}
      </p>
    </section>
  );
}
