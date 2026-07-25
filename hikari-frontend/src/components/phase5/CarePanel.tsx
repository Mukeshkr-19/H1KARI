"use client";

import type { Phase5State } from "@/utils/phase5/phase5State";

export interface CarePanelProps {
  readonly state: Phase5State;
  readonly prompt: string;
  readonly onPromptChange: (value: string) => void;
  readonly onPrepare: () => void;
  readonly onConfirm: () => void;
  readonly disabled?: boolean;
}

export function CarePanel({
  state,
  prompt,
  onPromptChange,
  onPrepare,
  onConfirm,
  disabled = false,
}: CarePanelProps) {
  return (
    <section aria-labelledby="phase5-care-heading" className="space-y-3">
      <h3 id="phase5-care-heading" className="text-base font-semibold text-white">
        Care
      </h3>
      <p className="text-sm text-gray-400">
        Supportive assistance only. HIKARI does not provide medical diagnosis or treatment authority.
      </p>
      <p
        role="status"
        aria-live="polite"
        className="text-sm text-amber-100 border border-amber-700 rounded-lg p-3"
      >
        Emergency limitation: HIKARI does not contact emergency services and never claims that anyone was contacted.
      </p>
      <div>
        <label htmlFor="phase5-care-prompt" className="block text-sm text-gray-300 mb-1">
          Support request
        </label>
        <textarea
          id="phase5-care-prompt"
          value={prompt}
          maxLength={1024}
          onChange={(event) => onPromptChange(event.target.value)}
          disabled={disabled || state.submitLocked}
          className="w-full bg-[#1a1a2e] border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-blue-500 min-h-[88px]"
        />
      </div>
      <button
        type="button"
        onClick={onPrepare}
        disabled={disabled || state.submitLocked || prompt.trim().length === 0}
        className="min-h-[44px] px-4 py-2 rounded-lg bg-blue-700 text-white disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-blue-400"
      >
        Prepare Care proposal
      </button>
      {state.capability === "care" && state.proposalSummary && (
        <div role="region" aria-label="Care proposal" className="text-sm text-gray-200 border border-gray-700 rounded-lg p-3">
          <p>{state.proposalSummary}</p>
          {state.emergencyLimitation && <p className="mt-2 text-amber-200">{state.emergencyLimitation}</p>}
          <p className="mt-2 text-gray-400">Escalation/approval state: {state.status.split("_").join(" ")}</p>
        </div>
      )}
      {state.status === "approval_required" && state.capability === "care" && (
        <button
          type="button"
          onClick={onConfirm}
          disabled={disabled || state.submitLocked}
          className="min-h-[44px] px-4 py-2 rounded-lg bg-emerald-700 text-white disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-emerald-400"
        >
          Confirm Care review
        </button>
      )}
    </section>
  );
}
