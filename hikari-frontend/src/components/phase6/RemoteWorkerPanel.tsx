import React from "react";
import type { Phase6RemoteWorkerUpdate } from "../../utils/phase6/phase6Protocol";

type Props = {
  remoteWorker: Phase6RemoteWorkerUpdate | null;
  disabled?: boolean;
};

export const RemoteWorkerPanel: React.FC<Props> = ({ remoteWorker, disabled = false }) => {
  if (!remoteWorker) {
    return (
      <section
        aria-labelledby="heading-remote-worker"
        className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm ${
          disabled ? "opacity-60 pointer-events-none" : ""
        }`}
      >
        <h3
          id="heading-remote-worker"
          className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2"
        >
          Remote Worker Telemetry
        </h3>
        <p className="text-sm text-slate-500 dark:text-slate-400 italic" role="status">
          No remote worker jobs active or reported.
        </p>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="heading-remote-worker"
      className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm space-y-3 ${
        disabled ? "opacity-60 pointer-events-none" : ""
      }`}
    >
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <h3
          id="heading-remote-worker"
          className="text-lg font-semibold text-slate-900 dark:text-slate-100"
        >
          Remote Worker Job ({remoteWorker.job_id})
        </h3>
        <span className="inline-flex items-center px-3 py-1 text-xs font-semibold rounded-full bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-slate-100 self-start sm:self-auto">
          State: {remoteWorker.state}
        </span>
      </div>

      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
        <div className="p-2.5 rounded bg-slate-50 dark:bg-slate-950 border border-slate-100 dark:border-slate-800">
          <dt className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Worker ID
          </dt>
          <dd className="mt-1 font-mono text-slate-900 dark:text-slate-100">
            {remoteWorker.worker_id}
          </dd>
        </div>

        <div className="p-2.5 rounded bg-slate-50 dark:bg-slate-950 border border-slate-100 dark:border-slate-800">
          <dt className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Quarantined / Evidence
          </dt>
          <dd className="mt-1 font-medium text-slate-900 dark:text-slate-100">
            Quarantined: {remoteWorker.quarantined ? "Yes (Quarantined)" : "No"} | Evidence: {remoteWorker.has_evidence ? "Yes" : "No"}
          </dd>
        </div>
      </dl>

      <div className="p-3 rounded bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-900 text-xs text-amber-900 dark:text-amber-200">
        <span className="font-semibold">[Authority Disclaimer] </span>
        Remote worker execution outputs do not carry local authority or verified task success.
      </div>
    </section>
  );
};
