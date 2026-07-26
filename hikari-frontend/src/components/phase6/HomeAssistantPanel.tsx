import React from "react";
import type { Phase6HomeAssistantProposal } from "../../utils/phase6/phase6Protocol";

type Props = {
  haProposal: Phase6HomeAssistantProposal | null;
  confirmedNonce: string | null;
  submitLocked: boolean;
  onConfirm: (proposalId: string, nonce: string) => void;
  onReject: (proposalId: string) => void;
  disabled?: boolean;
};

const RISK_BADGES: Record<Phase6HomeAssistantProposal["risk"], { text: string; bgClass: string }> = {
  low: { text: "[Risk: Low] Low Risk", bgClass: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200" },
  medium: { text: "[Risk: Medium] Medium Risk", bgClass: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200" },
  high: { text: "[Risk: High] High Risk", bgClass: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200" },
  critical: { text: "[Risk: Critical] Critical Risk", bgClass: "bg-rose-100 text-rose-900 dark:bg-rose-950 dark:text-rose-200" },
};

export const HomeAssistantPanel: React.FC<Props> = ({
  haProposal,
  confirmedNonce,
  submitLocked,
  onConfirm,
  onReject,
  disabled = false,
}) => {
  if (!haProposal) {
    return (
      <section
        aria-labelledby="heading-home-assistant"
        className="p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm"
      >
        <h3
          id="heading-home-assistant"
          className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2"
        >
          Home Assistant Boundaries
        </h3>
        <p className="text-sm text-slate-500 dark:text-slate-400 italic" role="status">
          No Home Assistant action proposal awaiting confirmation.
        </p>
      </section>
    );
  }

  const isConfirmed = confirmedNonce === haProposal.nonce;
  const riskInfo = RISK_BADGES[haProposal.risk] ?? { text: haProposal.risk, bgClass: "bg-slate-100 text-slate-800" };

  return (
    <section
      aria-labelledby="heading-home-assistant"
      className="p-4 rounded-lg border border-amber-300 dark:border-amber-700 bg-amber-50/50 dark:bg-amber-950/30 shadow-sm space-y-4"
    >
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <h3
          id="heading-home-assistant"
          className="text-lg font-semibold text-amber-950 dark:text-amber-100 flex items-center gap-2"
        >
          <span aria-hidden="true">🏠</span>
          Home Assistant Action Proposal
        </h3>
        <span
          className={`inline-flex items-center px-3 py-1 text-xs font-semibold rounded-full ${riskInfo.bgClass} self-start sm:self-auto min-h-[32px]`}
          aria-label={`Action risk level: ${haProposal.risk}`}
        >
          {riskInfo.text}
        </span>
      </div>

      {/* Confirmation Details Card */}
      <div className="p-4 rounded-md bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 space-y-3">
        <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100 border-b border-slate-100 dark:border-slate-800 pb-2">
          Action Proposal Details
        </h4>

        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
          <div>
            <dt className="font-medium text-slate-500 dark:text-slate-400">WHAT Service</dt>
            <dd className="font-mono text-sm text-slate-900 dark:text-slate-100">{haProposal.domain}.{haProposal.service}</dd>
          </div>
          <div>
            <dt className="font-medium text-slate-500 dark:text-slate-400">TARGET Entity</dt>
            <dd className="font-mono text-sm text-slate-900 dark:text-slate-100">{haProposal.entity_id}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="font-medium text-slate-500 dark:text-slate-400">EFFECT Summary</dt>
            <dd className="text-sm text-slate-800 dark:text-slate-200 font-medium mt-0.5">{haProposal.effect_summary}</dd>
          </div>
          <div>
            <dt className="font-medium text-slate-500 dark:text-slate-400">Proposal ID</dt>
            <dd className="font-mono text-slate-700 dark:text-slate-300">{haProposal.proposal_id}</dd>
          </div>
          <div>
            <dt className="font-medium text-slate-500 dark:text-slate-400">Expires At</dt>
            <dd className="font-mono text-slate-700 dark:text-slate-300">{new Date(haProposal.expires_at * 1000).toLocaleTimeString()}</dd>
          </div>
        </dl>
      </div>

      {/* Action Controls */}
      {isConfirmed ? (
        <div
          role="status"
          aria-live="polite"
          className="p-3 rounded-md bg-emerald-100 dark:bg-emerald-950 text-emerald-900 dark:text-emerald-200 text-sm font-semibold text-center border border-emerald-300 dark:border-emerald-800"
        >
          ✓ Confirmation submitted. Awaiting correlated server execution response...
        </div>
      ) : (
        <div className="flex flex-col sm:flex-row items-center justify-end gap-3 pt-2">
          <button
            type="button"
            disabled={disabled || submitLocked}
            onClick={() => onReject(haProposal.proposal_id)}
            className="w-full sm:w-auto min-h-[44px] min-w-[120px] px-4 py-2 text-sm font-semibold rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label={`Reject Home Assistant proposal for ${haProposal.entity_id}`}
          >
            Reject / Cancel
          </button>
          <button
            type="button"
            disabled={disabled || submitLocked}
            onClick={() => onConfirm(haProposal.proposal_id, haProposal.nonce)}
            className="w-full sm:w-auto min-h-[44px] min-w-[140px] px-4 py-2 text-sm font-semibold rounded-md bg-amber-600 dark:bg-amber-500 text-white hover:bg-amber-700 dark:hover:bg-amber-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label={`Confirm Home Assistant ${haProposal.service} action on ${haProposal.entity_id}`}
          >
            Confirm Action
          </button>
        </div>
      )}
    </section>
  );
};
