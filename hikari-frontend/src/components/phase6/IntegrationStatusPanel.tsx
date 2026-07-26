import React from "react";
import type { Phase6IntegrationStatus } from "../../utils/phase6/phase6Protocol";

type Props = {
  integrations: readonly Phase6IntegrationStatus[];
  disabled?: boolean;
};

const STATUS_TEXT_AND_ICON: Record<Phase6IntegrationStatus["status"], { text: string; icon: string; bgClass: string }> = {
  unavailable: { text: "[Unavailable] Unavailable", icon: "⊘", bgClass: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300" },
  disabled: { text: "[Disabled] Disabled", icon: "⊝", bgClass: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300" },
  configuring: { text: "[Configuring] Configuring", icon: "⚙", bgClass: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200" },
  ready: { text: "[Ready] Ready", icon: "✓", bgClass: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200" },
  degraded: { text: "[Degraded] Degraded", icon: "⚠", bgClass: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200" },
  approval_required: { text: "[Approval] Approval Required", icon: "✋", bgClass: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200" },
  active: { text: "[Active] Active", icon: "▶", bgClass: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-200" },
  cancelling: { text: "[Cancelling] Cancelling", icon: "⏳", bgClass: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200" },
  failed: { text: "[Failed] Failed", icon: "✕", bgClass: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-200" },
  revoked: { text: "[Revoked] Revoked", icon: "🚫", bgClass: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-200" },
};

export const IntegrationStatusPanel: React.FC<Props> = ({ integrations, disabled = false }) => {
  return (
    <section
      aria-labelledby="heading-integration-status"
      className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm ${
        disabled ? "opacity-60 pointer-events-none" : ""
      }`}
    >
      <h3
        id="heading-integration-status"
        className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3"
      >
        Ecosystem Capability &amp; Integration Status
      </h3>

      {integrations.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400 italic" role="status">
          No integrations registered or reported.
        </p>
      ) : (
        <ul className="space-y-3" role="list">
          {integrations.map((item) => {
            const statusInfo = STATUS_TEXT_AND_ICON[item.status] ?? {
              text: item.status,
              icon: "ℹ",
              bgClass: "bg-slate-100 text-slate-800",
            };
            return (
              <li
                key={item.integration_id}
                className="flex flex-col sm:flex-row sm:items-center justify-between p-3 rounded-md border border-slate-100 dark:border-slate-800 bg-slate-50 dark:bg-slate-950/50 gap-2 min-h-[44px]"
              >
                <div>
                  <span className="font-medium text-slate-900 dark:text-slate-100">
                    {item.name}
                  </span>
                  <span className="text-xs text-slate-500 dark:text-slate-400 ml-2">
                    ({item.integration_id})
                  </span>
                  {item.details_summary && (
                    <p className="text-xs text-slate-600 dark:text-slate-400 mt-1">
                      {item.details_summary}
                    </p>
                  )}
                </div>
                <span
                  className={`inline-flex items-center gap-1.5 px-3 py-1 text-xs font-semibold rounded-full ${statusInfo.bgClass}`}
                  aria-label={`Integration status: ${item.status}`}
                >
                  <span aria-hidden="true">{statusInfo.icon}</span>
                  <span>{statusInfo.text}</span>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};
