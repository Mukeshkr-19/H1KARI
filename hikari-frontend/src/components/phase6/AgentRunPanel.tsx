import React from "react";
import type { Phase6AgentRunUpdate } from "../../utils/phase6/phase6Protocol";

type Props = {
  agentRun: Phase6AgentRunUpdate | null;
  disabled?: boolean;
};

const STATE_BADGES: Record<Phase6AgentRunUpdate["state"], { text: string; icon: string }> = {
  preview: { text: "[Preview] Preview", icon: "🔍" },
  waiting_for_approval: { text: "[Approval] Awaiting Owner Approval", icon: "✋" },
  running: { text: "[Running] Running", icon: "▶" },
  observing: { text: "[Observing] Observing", icon: "👁" },
  correcting: { text: "[Correcting] Self-Correcting", icon: "🛠" },
  succeeded: { text: "[Succeeded] Succeeded", icon: "✓" },
  denied: { text: "[Denied] Denied", icon: "✕" },
  failed: { text: "[Failed] Failed", icon: "✕" },
  cancelled: { text: "[Cancelled] Cancelled", icon: "🚫" },
  exhausted: { text: "[Exhausted] Budget Exhausted", icon: "⌛" },
};

export const AgentRunPanel: React.FC<Props> = ({ agentRun, disabled = false }) => {
  if (!agentRun) {
    return (
      <section
        aria-labelledby="heading-agent-runs"
        className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm ${
          disabled ? "opacity-60 pointer-events-none" : ""
        }`}
      >
        <h3
          id="heading-agent-runs"
          className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2"
        >
          Bounded Agent Runs
        </h3>
        <p className="text-sm text-slate-500 dark:text-slate-400 italic" role="status">
          No agent runs active or reported.
        </p>
      </section>
    );
  }

  const badge = STATE_BADGES[agentRun.state] ?? { text: agentRun.state, icon: "ℹ" };
  const percentUsed = Math.min(
    100,
    Math.round((agentRun.step_count / Math.max(1, agentRun.budget_limit)) * 100)
  );

  return (
    <section
      aria-labelledby="heading-agent-runs"
      className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm space-y-4 ${
        disabled ? "opacity-60 pointer-events-none" : ""
      }`}
    >
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <h3
            id="heading-agent-runs"
            className="text-lg font-semibold text-slate-900 dark:text-slate-100"
          >
            Bounded Agent Run ({agentRun.run_id})
          </h3>
          <p className="text-sm text-slate-600 dark:text-slate-300 mt-1">
            {agentRun.safe_summary}
          </p>
        </div>
        <span
          className="inline-flex items-center gap-1.5 px-3 py-1 text-xs font-semibold rounded-full bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200 self-start sm:self-auto min-h-[32px]"
          aria-label={`Agent run state: ${agentRun.state}`}
        >
          <span aria-hidden="true">{badge.icon}</span>
          <span>{badge.text}</span>
        </span>
      </div>

      {/* Progress Bar & Counts */}
      <div className="space-y-2 bg-slate-50 dark:bg-slate-950 p-3 rounded-md border border-slate-100 dark:border-slate-800">
        <div className="flex justify-between text-xs font-medium text-slate-700 dark:text-slate-300">
          <span>Step Budget Usage:</span>
          <span>
            {agentRun.step_count} / {agentRun.budget_limit} steps ({agentRun.action_count} actions executed)
          </span>
        </div>
        <div
          role="progressbar"
          aria-label="Agent run step budget progress"
          aria-valuenow={agentRun.step_count}
          aria-valuemin={0}
          aria-valuemax={agentRun.budget_limit}
          className="w-full h-3 bg-slate-200 dark:bg-slate-800 rounded-full overflow-hidden"
        >
          <div
            className="h-full bg-sky-600 dark:bg-sky-500 transition-all duration-300 motion-reduce:transition-none"
            style={{ width: `${percentUsed}%` }}
          />
        </div>
      </div>
    </section>
  );
};
