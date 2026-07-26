import React from "react";
import type { Phase6ModelEvalUpdate } from "../../utils/phase6/phase6Protocol";

type Props = {
  modelEval: Phase6ModelEvalUpdate | null;
  disabled?: boolean;
};

const PRIVACY_BADGES: Record<Phase6ModelEvalUpdate["privacy_class"], { text: string; bgClass: string }> = {
  local_only: { text: "[Local Only] Local Only", bgClass: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200" },
  gateway_ok: { text: "[Gateway OK] Gateway Allowed", bgClass: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200" },
  remote_ok: { text: "[Remote OK] Remote Allowed", bgClass: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200" },
};

export const ModelEvalPanel: React.FC<Props> = ({ modelEval, disabled = false }) => {
  if (!modelEval) {
    return (
      <section
        aria-labelledby="heading-model-eval"
        className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm ${
          disabled ? "opacity-60 pointer-events-none" : ""
        }`}
      >
        <h3
          id="heading-model-eval"
          className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2"
        >
          Measured Model Routing Evaluation
        </h3>
        <p className="text-sm text-slate-500 dark:text-slate-400 italic" role="status">
          No model routing evaluation reported.
        </p>
      </section>
    );
  }

  const privInfo = PRIVACY_BADGES[modelEval.privacy_class] ?? { text: modelEval.privacy_class, bgClass: "bg-slate-100 text-slate-800" };

  return (
    <section
      aria-labelledby="heading-model-eval"
      className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm space-y-3 ${
        disabled ? "opacity-60 pointer-events-none" : ""
      }`}
    >
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <h3
            id="heading-model-eval"
            className="text-lg font-semibold text-slate-900 dark:text-slate-100"
          >
            Model Candidate ({modelEval.candidate_id})
          </h3>
          <p className="text-sm font-medium text-slate-700 dark:text-slate-300 mt-0.5">
            Recommendation: {modelEval.recommendation}
          </p>
        </div>
        <span
          className={`inline-flex items-center px-3 py-1 text-xs font-semibold rounded-full ${privInfo.bgClass} self-start sm:self-auto min-h-[32px]`}
          aria-label={`Privacy class: ${modelEval.privacy_class}`}
        >
          {privInfo.text}
        </span>
      </div>

      {/* Metrics Grid */}
      <dl className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm">
        <div className="p-2.5 rounded bg-slate-50 dark:bg-slate-950 border border-slate-100 dark:border-slate-800">
          <dt className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Quality Score
          </dt>
          <dd className="mt-1 text-base font-semibold text-slate-900 dark:text-slate-100">
            {(modelEval.quality_score * 100).toFixed(1)}%
          </dd>
        </div>

        <div className="p-2.5 rounded bg-slate-50 dark:bg-slate-950 border border-slate-100 dark:border-slate-800">
          <dt className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Safety Score
          </dt>
          <dd className="mt-1 text-base font-semibold text-slate-900 dark:text-slate-100">
            {(modelEval.safety_score * 100).toFixed(1)}%
          </dd>
        </div>

        <div className="p-2.5 rounded bg-slate-50 dark:bg-slate-950 border border-slate-100 dark:border-slate-800">
          <dt className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Measured Latency
          </dt>
          <dd className="mt-1 text-base font-semibold text-slate-900 dark:text-slate-100">
            {modelEval.latency_ms.toFixed(1)} ms
          </dd>
        </div>
      </dl>

      {/* Capabilities */}
      <div className="space-y-1 bg-slate-50 dark:bg-slate-950 p-2.5 rounded border border-slate-100 dark:border-slate-800">
        <span className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider block">
          Capabilities:
        </span>
        <div className="flex flex-wrap gap-1.5 pt-1">
          {modelEval.capabilities.map((cap, idx) => (
            <span
              key={idx}
              className="px-2 py-0.5 text-xs font-medium rounded bg-slate-200 dark:bg-slate-800 text-slate-800 dark:text-slate-200"
            >
              {cap}
            </span>
          ))}
        </div>
      </div>

      {modelEval.rejection_reason && (
        <div
          role="alert"
          className="p-3 rounded bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-900 text-xs text-rose-900 dark:text-rose-200 font-medium"
        >
          <span className="font-semibold">[Rejection Reason] </span>
          {modelEval.rejection_reason}
        </div>
      )}
    </section>
  );
};
