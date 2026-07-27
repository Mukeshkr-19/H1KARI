import React, { useReducer } from "react";
import {
  INITIAL_PHASE6_STATE,
  reducePhase6State,
  type Phase6State,
} from "../../utils/phase6/phase6State";
import {
  buildHomeAssistantConfirmRequest,
  generatePhase6RequestId,
} from "../../utils/phase6/phase6Protocol";
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
  state?: Phase6State;
  onSendClientFrame?: (frame: unknown) => void;
  onConfirmHomeAssistant?: (proposalId: string, nonce: string) => void;
  onDismissError?: () => void;
  disabled?: boolean;
};

export const Phase6CommandCenter: React.FC<Props> = ({
  state: externalState,
  onSendClientFrame,
  onConfirmHomeAssistant: externalConfirmHA,
  onDismissError: externalDismissError,
  disabled = false,
}) => {
  const [internalState, dispatch] = useReducer(reducePhase6State, INITIAL_PHASE6_STATE);
  const state = externalState ?? internalState;

  const handleConfirmHomeAssistant = (proposalId: string, nonce: string) => {
    if (externalConfirmHA) {
      externalConfirmHA(proposalId, nonce);
      return;
    }
    const requestId = generatePhase6RequestId();
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
    if (externalDismissError) {
      externalDismissError();
    }
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
        {state.errorMessage ? `Error: ${state.errorMessage}` : `Command Center status: ${state.status}`}
      </div>

      {/* Header Landmark */}
      <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-200 dark:border-slate-800 pb-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Phase 6 Command Center</h2>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Ecosystem status, agent telemetry, Home Assistant control, and local routing views
          </p>
        </div>

        <div className="flex items-center gap-3">
          <span
            className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ${
              isUnavailable
                ? "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                : "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isUnavailable ? "bg-slate-400" : "bg-emerald-500 animate-pulse"
              }`}
              aria-hidden="true"
            />
            Status: {state.status.toUpperCase()}
          </span>

          <button
            type="button"
            onClick={handleReset}
            disabled={disabled || state.submitLocked}
            className="min-h-[44px] min-w-[44px] px-3 py-2 text-xs font-medium rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 hover:bg-slate-50 dark:hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 disabled:opacity-50 transition-colors"
            aria-label="Reset Command Center state"
          >
            Reset
          </button>
        </div>
      </header>

      {/* Error Alert */}
      {state.errorMessage && (
        <div
          role="alert"
          className="p-4 rounded-xl bg-rose-50 border border-rose-200 dark:bg-rose-950/30 dark:border-rose-900 text-rose-900 dark:text-rose-200 text-sm flex items-start justify-between gap-3"
        >
          <div>
            <strong className="font-semibold block">Phase 6 Request Error</strong>
            <span>{state.errorMessage}</span>
          </div>
          <button
            type="button"
            onClick={() => {
              if (externalDismissError) externalDismissError();
              dispatch({ type: "phase6/dismiss_error" });
            }}
            className="min-h-[44px] min-w-[44px] px-3 py-1 text-xs font-semibold rounded bg-rose-200 dark:bg-rose-900 hover:bg-rose-300 dark:hover:bg-rose-800 text-rose-900 dark:text-rose-100 transition-colors"
            aria-label="Dismiss error message"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Primary Panels Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <IntegrationStatusPanel integrations={state.integrations} />
        <AgentRunPanel agentRun={state.agentRun} />
        <TimeSensePanel timeSense={state.timeSense} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <HomeAssistantPanel
          haProposal={state.haProposal}
          confirmedNonce={state.confirmedNonce}
          submitLocked={state.submitLocked || disabled}
          onConfirm={handleConfirmHomeAssistant}
          onReject={handleRejectHomeAssistant}
        />
        <RepoIntelPanel repoIntel={state.repoIntel} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <SkillEvolutionPanel skillEvolution={state.skillEvolution} />
        <EncryptedSyncPanel encryptedSync={state.encryptedSync} />
        <RemoteWorkerPanel remoteWorker={state.remoteWorker} />
      </div>

      <div className="grid grid-cols-1 gap-6">
        <ModelEvalPanel modelEval={state.modelEval} />
      </div>
    </main>
  );
};
