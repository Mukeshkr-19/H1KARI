import React from "react";
import type { Phase6SkillEvolutionUpdate } from "../../utils/phase6/phase6Protocol";

type Props = {
  skillEvolution: Phase6SkillEvolutionUpdate | null;
  disabled?: boolean;
};

const STATE_BADGES: Record<Phase6SkillEvolutionUpdate["state"], { text: string; icon: string }> = {
  proposal: { text: "[Proposed] Proposed", icon: "📝" },
  validation: { text: "[Validated] Validated", icon: "🔍" },
  review: { text: "[Review] Awaiting Owner Review", icon: "✋" },
  rejection: { text: "[Rejected] Rejected", icon: "✕" },
  revocation: { text: "[Revoked] Revoked", icon: "🚫" },
  install_plan_ready: { text: "[Install Plan Ready] Install Plan Ready", icon: "📦" },
};

export const SkillEvolutionPanel: React.FC<Props> = ({ skillEvolution, disabled = false }) => {
  if (!skillEvolution) {
    return (
      <section
        aria-labelledby="heading-skill-evolution"
        className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm ${
          disabled ? "opacity-60 pointer-events-none" : ""
        }`}
      >
        <h3
          id="heading-skill-evolution"
          className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2"
        >
          Reviewed Skill Evolution
        </h3>
        <p className="text-sm text-slate-500 dark:text-slate-400 italic" role="status">
          No skill evolution package active or pending review.
        </p>
      </section>
    );
  }

  const badge = STATE_BADGES[skillEvolution.state] ?? { text: skillEvolution.state, icon: "ℹ" };

  return (
    <section
      aria-labelledby="heading-skill-evolution"
      className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm space-y-3 ${
        disabled ? "opacity-60 pointer-events-none" : ""
      }`}
    >
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <h3
            id="heading-skill-evolution"
            className="text-lg font-semibold text-slate-900 dark:text-slate-100"
          >
            Skill Package ({skillEvolution.package_id} v{skillEvolution.version})
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Rollback Ready: {skillEvolution.rollback_ready ? "Yes" : "No"} | Auto-Install: <span className="font-semibold text-rose-600 dark:text-rose-400">Disabled by Policy</span>
          </p>
        </div>
        <span
          className="inline-flex items-center gap-1.5 px-3 py-1 text-xs font-semibold rounded-full bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-slate-100 self-start sm:self-auto min-h-[32px]"
          aria-label={`Skill evolution state: ${skillEvolution.state}`}
        >
          <span aria-hidden="true">{badge.icon}</span>
          <span>{badge.text}</span>
        </span>
      </div>

      {/* Permissions List */}
      <div className="space-y-1.5 bg-slate-50 dark:bg-slate-950 p-3 rounded-md border border-slate-100 dark:border-slate-800">
        <h4 className="text-xs font-semibold text-slate-700 dark:text-slate-300">
          Declared Permissions ({skillEvolution.permissions_summary.length})
        </h4>
        {skillEvolution.permissions_summary.length === 0 ? (
          <p className="text-xs text-slate-500 italic">No permissions declared.</p>
        ) : (
          <ul className="list-disc list-inside space-y-1 text-xs font-mono text-slate-800 dark:text-slate-200">
            {skillEvolution.permissions_summary.map((perm, idx) => (
              <li key={idx}>{perm}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="p-3 rounded bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-900 text-xs text-amber-900 dark:text-amber-200">
        <span className="font-semibold">[Policy Notice] </span>
        Automatic background installation is disabled. Skill packages require explicit manual owner review and verification.
      </div>
    </section>
  );
};
