import React, { useReducer } from "react";
import {
  INITIAL_PHASE6_STATE,
  reducePhase6State,
} from "../../utils/phase6/phase6State";
import { buildHomeAssistantConfirmRequest } from "../../utils/phase6/phase6Protocol";
import { IntegrationStatusPanel } from "./IntegrationStatusPanel";
import { AgentRunPanel } from "./AgentRunPanel";
import { TimeSensePanel } from "./TimeSensePanel";
import { RepoIntelPanel } from "./RepoIntelPanel";
import { SkillEvolutionPanel } from "./SkillEvolutionPanel";
import { HomeAssistantPanel } from "./HomeAssistantPanel";
import { EncryptedSyncPanel } from "./EncryptedSyncPanel";
import { RemoteWorkerPanel } from "./RemoteWorkerPanel";
import { ModelEvalPanel } from "./ModelEvalPanel";

type Props = {
  onSendClientFrame?: (frame: unknown) => void;
  disabled?: boolean;
};

export const Phase6CommandCenter: React.FC<Props> = ({
  onSendClientFrame,
  disabled = false,
}) => {
  const [state, dispatch] = useReducer(reducePhase6State, INITIAL_PHASE6_STATE);

  const handleConfirmHomeAssistant = (proposalId: string, nonce: string) => {
    const requestId = `req_ha_conf_${Date.now()}`;
    dispatch({ type: "phase6/confirm_home_assistant", proposalId, nonce });
    dispatch({ type: "phase6/begin_request", requestId });
    if (onSendClientFrame) {
      try {
        const frame = buildHomeAssistantConfirmRequest(requestId, proposalId, nonce);
        onSendClientFrame(frame);
      } catch {
        dispatch({ type: "phase6/unlock_submit" });
      }
    }
  };

  const handleRejectHomeAssistant = () => {
    dispatch({ type: "phase6/clear_sensitive" });
  };

  const handleReset = () => {
    dispatch({ type: "phase6/reset" });
  };

  const isUnavailable = state.status === "unavailable";

  return (
    <main
      aria-label="H1KARI Phase 6 Command Center"
      className="max-w-7xl mx-auto p-4 sm:p-6 space-y-6 text-slate-900 dark:text-slate-100 font-sans"
    >
      {/* Live Region for Screen Reader Announcements */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
      >
        {state.errorMessage
          ? `Error: ${state.errorMessage}`
          : state.status === "approval_required"
          ? "Owner approval required for pending proposal."
          : `Command Center status: ${state.status}`}
      </div>

      {/* Header Landmark */}
      <header className="flex flex-col sm:flex-row sm:items-center justify-between pb-4 border-b border-slate-200 dark:border-slate-800 gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <span aria-hidden="true">🛡</span>
            Phase 6 Ecosystem &amp; Command Center
          </h2>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            Server-correlated capability status, bounded agent runs, and safe adapter control plane.
          </p>
        </div>

        <div className="flex items-center gap-3 self-start sm:self-auto">
          <span
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-full min-h-[36px] ${
              isUnavailable
                ? "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                : state.status === "approval_required"
                ? "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200"
                : state.status === "error"
                ? "bg-rose-100 text-rose-900 dark:bg-rose-950 dark:text-rose-200"
                : "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200"
            }`}
            aria-label={`Command Center Status: ${state.status}`}
          >
            <span aria-hidden="true">
              {isUnavailable ? "⊘" : state.status === "approval_required" ? "✋" : state.status === "error" ? "✕" : "✓"}
            </span>
            <span>[{state.status.toUpperCase()}] {state.status}</span>
          </span>

          <button
            type="button"
            onClick={handleReset}
            disabled={disabled || state.submitLocked}
            className="min-h-[44px] px-4 py-2 text-sm font-semibold rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Reset Phase 6 Command Center state"
          >
            Reset State
          </button>
        </div>
      </header>

      {/* Safe Unavailable Fallback Banner */}
      {isUnavailable && (
        <section
          role="alert"
          className="p-4 rounded-lg bg-slate-100 dark:bg-slate-900 border border-slate-300 dark:border-slate-800 text-slate-800 dark:text-slate-200 space-y-1"
        >
          <h3 className="font-semibold text-base flex items-center gap-2">
            <span aria-hidden="true">⊘</span>
            Phase 6 Command Center Unavailable
          </h3>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Backend Phase 6 services are not paired or connected. All ecosystem action proposals fail closed safely.
          </p>
        </section>
      )}

      {/* Error Banner */}
      {state.errorMessage && (
        <section
          role="alert"
          className="p-4 rounded-lg bg-rose-50 dark:bg-rose-950/60 border border-rose-200 dark:border-rose-900 text-rose-900 dark:text-rose-200 text-sm font-medium flex items-center justify-between gap-4"
        >
          <div>
            <span className="font-semibold">[Transport Error] </span>
            {state.errorMessage}
          </div>
          <button
            type="button"
            onClick={() => dispatch({ type: "phase6/dismiss_error" })}
            className="min-h-[36px] px-3 py-1 text-xs font-semibold rounded border border-rose-300 dark:border-rose-700 bg-white dark:bg-slate-900 text-rose-900 dark:text-rose-200 hover:bg-rose-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
            aria-label="Dismiss error message"
          >
            Dismiss
          </button>
        </section>
      )}

      {/* Domain Panels Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <IntegrationStatusPanel integrations={state.integrations} />
        <AgentRunPanel agentRun={state.agentRun} />
        <TimeSensePanel timeSense={state.timeSense} />
        <RepoIntelPanel repoIntel={state.repoIntel} />
        <SkillEvolutionPanel skillEvolution={state.skillEvolution} />
        <HomeAssistantPanel
          haProposal={state.haProposal}
          confirmedNonce={state.confirmedNonce}
          submitLocked={state.submitLocked}
          onConfirm={handleConfirmHomeAssistant}
          onReject={handleRejectHomeAssistant}
          disabled={disabled || isUnavailable}
        />
        <EncryptedSyncPanel encryptedSync={state.encryptedSync} />
        <RemoteWorkerPanel remoteWorker={state.remoteWorker} />
        <ModelEvalPanel modelEval={state.modelEval} />
      </div>
    </main>
  );
};
