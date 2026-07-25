"use client";

import type { Phase5State } from "@/utils/phase5/phase5State";

export interface TeachMePanelProps {
  readonly state: Phase5State;
  readonly topic: string;
  readonly onTopicChange: (value: string) => void;
  readonly onPrepare: () => void;
  readonly disabled?: boolean;
}

export function TeachMePanel({
  state,
  topic,
  onTopicChange,
  onPrepare,
  disabled = false,
}: TeachMePanelProps) {
  return (
    <section aria-labelledby="phase5-teach-heading" className="space-y-3">
      <h3 id="phase5-teach-heading" className="text-base font-semibold text-white">
        Teach Me
      </h3>
      <p className="text-sm text-gray-400">
        Proposals only. Teach Me does not install skills.
      </p>
      <div>
        <label htmlFor="phase5-teach-topic" className="block text-sm text-gray-300 mb-1">
          Topic or request
        </label>
        <input
          id="phase5-teach-topic"
          type="text"
          value={topic}
          maxLength={1024}
          onChange={(event) => onTopicChange(event.target.value)}
          disabled={disabled || state.submitLocked}
          className="w-full bg-[#1a1a2e] border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-blue-500 min-h-[44px]"
        />
      </div>
      <button
        type="button"
        onClick={onPrepare}
        disabled={disabled || state.submitLocked || topic.trim().length === 0}
        className="min-h-[44px] px-4 py-2 rounded-lg bg-blue-700 text-white disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-blue-400"
      >
        Prepare Teach Me proposal
      </button>
      {state.capability === "teach_me" && state.proposalSummary && (
        <div
          tabIndex={-1}
          className="rounded-lg border border-gray-700 p-3 text-sm text-gray-200"
          role="region"
          aria-label="Teach Me proposal preview"
        >
          <p>{state.proposalSummary}</p>
          <ul className="list-disc pl-5 mt-2 space-y-1">
            {state.proposalItems.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p className="mt-2 text-gray-400">Approval/review status: {state.status.split("_").join(" ")}</p>
        </div>
      )}
    </section>
  );
}
