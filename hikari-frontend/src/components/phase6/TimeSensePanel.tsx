import React from "react";
import type { Phase6TimeSenseUpdate } from "../../utils/phase6/phase6Protocol";

type Props = {
  timeSense: Phase6TimeSenseUpdate | null;
  disabled?: boolean;
};

export const TimeSensePanel: React.FC<Props> = ({ timeSense, disabled = false }) => {
  if (!timeSense) {
    return (
      <section
        aria-labelledby="heading-time-sense"
        className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm ${
          disabled ? "opacity-60 pointer-events-none" : ""
        }`}
      >
        <h3
          id="heading-time-sense"
          className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2"
        >
          Time Sense Telemetry
        </h3>
        <p className="text-sm text-slate-500 dark:text-slate-400 italic" role="status">
          No time sense telemetry reported.
        </p>
      </section>
    );
  }

  const ageMinutes = (timeSense.task_age_seconds / 60).toFixed(1);

  return (
    <section
      aria-labelledby="heading-time-sense"
      className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm space-y-3 ${
        disabled ? "opacity-60 pointer-events-none" : ""
      }`}
    >
      <h3
        id="heading-time-sense"
        className="text-lg font-semibold text-slate-900 dark:text-slate-100"
      >
        Time Sense Telemetry
      </h3>

      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
        <div className="p-2.5 rounded bg-slate-50 dark:bg-slate-950 border border-slate-100 dark:border-slate-800">
          <dt className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Active Task Age
          </dt>
          <dd className="mt-1 text-base font-semibold text-slate-900 dark:text-slate-100">
            {ageMinutes} mins ({Math.round(timeSense.task_age_seconds)}s)
          </dd>
        </div>

        <div className="p-2.5 rounded bg-slate-50 dark:bg-slate-950 border border-slate-100 dark:border-slate-800">
          <dt className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Heartbeat Status
          </dt>
          <dd className="mt-1 text-base font-semibold text-slate-900 dark:text-slate-100">
            {timeSense.heartbeat_status}
          </dd>
        </div>

        <div className="p-2.5 rounded bg-slate-50 dark:bg-slate-950 border border-slate-100 dark:border-slate-800">
          <dt className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Suppression State
          </dt>
          <dd className="mt-1 font-medium text-slate-800 dark:text-slate-200">
            {timeSense.suppression_state}
          </dd>
        </div>

        <div className="p-2.5 rounded bg-slate-50 dark:bg-slate-950 border border-slate-100 dark:border-slate-800">
          <dt className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Background Status
          </dt>
          <dd className="mt-1 font-medium text-slate-800 dark:text-slate-200">
            {timeSense.background_status}
          </dd>
        </div>
      </dl>

      {timeSense.stuck_reason && (
        <div
          role="alert"
          className="p-3 rounded-md bg-amber-50 border border-amber-200 text-amber-900 dark:bg-amber-950/60 dark:border-amber-800 dark:text-amber-200 text-xs font-medium"
        >
          <span className="font-semibold">[Stuck Reason] </span>
          {timeSense.stuck_reason}
        </div>
      )}
    </section>
  );
};
