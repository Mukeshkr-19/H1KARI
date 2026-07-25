"use client";

import type { Phase5State } from "@/utils/phase5/phase5State";

export interface GuideHandsPanelProps {
  readonly state: Phase5State;
  readonly goal: string;
  readonly consequential: boolean;
  readonly onGoalChange: (value: string) => void;
  readonly onConsequentialChange: (value: boolean) => void;
  readonly onPrepare: () => void;
  readonly onConfirm: () => void;
  readonly disabled?: boolean;
}

export function GuideHandsPanel({
  state,
  goal,
  consequential,
  onGoalChange,
  onConsequentialChange,
  onPrepare,
  onConfirm,
  disabled = false,
}: GuideHandsPanelProps) {
  return (
    <section aria-labelledby="phase5-guide-heading" className="space-y-3">
      <h3 id="phase5-guide-heading" className="text-base font-semibold text-white">
        Guide My Hands
      </h3>
      <p className="text-sm text-gray-400">
        Guidance only. Camera access is never started automatically.
      </p>
      <div>
        <label htmlFor="phase5-guide-goal" className="block text-sm text-gray-300 mb-1">
          Guidance request
        </label>
        <textarea
          id="phase5-guide-goal"
          value={goal}
          maxLength={1024}
          onChange={(event) => onGoalChange(event.target.value)}
          disabled={disabled || state.submitLocked}
          className="w-full bg-[#1a1a2e] border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-blue-500 min-h-[88px]"
        />
      </div>
      <label className="flex min-h-[44px] items-center gap-3 text-sm text-gray-300">
        <input
          type="checkbox"
          checked={consequential}
          onChange={(event) => onConsequentialChange(event.target.checked)}
          disabled={disabled || state.submitLocked}
          className="h-5 w-5 rounded border-gray-600 focus:ring-2 focus:ring-blue-400"
        />
        This guidance includes a consequential step that requires my approval
      </label>
      <button
        type="button"
        onClick={onPrepare}
        disabled={disabled || state.submitLocked || goal.trim().length === 0}
        className="min-h-[44px] px-4 py-2 rounded-lg bg-blue-700 text-white disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-blue-400"
      >
        Prepare guidance proposal
      </button>
      {state.uncertaintyDisclosed && (
        <p role="status" className="text-sm text-amber-200 border border-amber-700 rounded-lg p-2">
          Uncertainty disclosed: please review the next step carefully.
        </p>
      )}
      {state.capability === "guide_my_hands" && state.proposalItems.length > 0 && (
        <ol className="list-decimal pl-5 space-y-1 text-sm text-gray-200" aria-label="Guidance steps">
          {state.proposalItems.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ol>
      )}
      {state.status === "approval_required" && state.capability === "guide_my_hands" && (
        <button
          type="button"
          onClick={onConfirm}
          disabled={disabled || state.submitLocked}
          className="min-h-[44px] px-4 py-2 rounded-lg bg-emerald-700 text-white disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-emerald-400"
        >
          Approve consequential guidance step
        </button>
      )}
    </section>
  );
}
