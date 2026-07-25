"use client";

import type { Phase5State } from "@/utils/phase5/phase5State";

export interface ChildModePanelProps {
  readonly state: Phase5State;
  readonly childActorId: string;
  readonly onChildActorIdChange: (value: string) => void;
  readonly onActivate: () => void;
  readonly disabled?: boolean;
}

export function ChildModePanel({
  state,
  childActorId,
  onChildActorIdChange,
  onActivate,
  disabled = false,
}: ChildModePanelProps) {
  const activeChild = state.sessionType === "child" && state.status === "active";
  return (
    <section aria-labelledby="phase5-child-heading" className="space-y-3">
      <h3 id="phase5-child-heading" className="text-base font-semibold text-white">
        Child Mode
      </h3>
      <p className="text-sm text-gray-400" role="status" aria-live="polite">
        Child mode status: {activeChild ? "active" : state.sessionType === "child" ? state.status : "inactive"}.
        Child mode is owner-controlled and cannot weaken policy.
      </p>
      <p className="text-sm text-gray-400">
        Restricted capabilities: no purchases, no owner memory access, no helper grants, no audit bypass.
      </p>
      <div>
        <label htmlFor="phase5-child-actor" className="block text-sm text-gray-300 mb-1">
          Child actor ID
        </label>
        <input
          id="phase5-child-actor"
          type="text"
          value={childActorId}
          maxLength={128}
          onChange={(event) => onChildActorIdChange(event.target.value)}
          disabled={disabled || state.submitLocked}
          className="w-full bg-[#1a1a2e] border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-blue-500 min-h-[44px]"
        />
      </div>
      <button
        type="button"
        onClick={onActivate}
        disabled={disabled || state.submitLocked || childActorId.trim().length === 0}
        className="min-h-[44px] px-4 py-2 rounded-lg bg-blue-700 text-white disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-blue-400"
      >
        Activate child mode session
      </button>
    </section>
  );
}
