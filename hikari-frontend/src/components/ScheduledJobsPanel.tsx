"use client";

import { useId } from "react";
import {
  availableScheduledJobControls,
  mapScheduledJobErrorMessage,
  type ScheduledJobErrorCode,
  type ScheduledJobView,
} from "@/utils/productivity/scheduledJobs";

export type ScheduledJobsPanelProps = {
  jobs: ReadonlyArray<ScheduledJobView>;
  statusMessage?: string;
  error?: ScheduledJobErrorCode;
  onPause: (jobId: string) => void;
  onResume: (jobId: string) => void;
  onCancel: (jobId: string) => void;
};

function stateLabel(state: ScheduledJobView["state"]): string {
  switch (state) {
    case "scheduled":
      return "Scheduled";
    case "paused":
      return "Paused";
    case "running":
      return "Running";
    case "interrupted":
      return "Interrupted";
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
    default:
      return "Unknown";
  }
}

export function ScheduledJobsPanel({
  jobs,
  statusMessage,
  error,
  onPause,
  onResume,
  onCancel,
}: ScheduledJobsPanelProps) {
  const instanceId = useId();
  const headingId = `${instanceId}-heading`;
  const listId = `${instanceId}-list`;
  const errorMessage = error ? mapScheduledJobErrorMessage(error) : "";

  return (
    <section
      className="rounded-xl border border-gray-800 bg-[#1a1a2e] p-4"
      aria-labelledby={headingId}
    >
      <h2 id={headingId} className="text-lg font-semibold text-gray-100">
        Scheduled jobs
      </h2>

      {statusMessage ? (
        <p className="mt-2 text-sm text-amber-100" role="status" aria-live="polite">
          {statusMessage}
        </p>
      ) : null}

      {errorMessage ? (
        <p className="mt-2 text-sm text-red-200" role="alert">
          {errorMessage}
        </p>
      ) : null}

      {jobs.length === 0 ? (
        <p className="mt-3 text-sm text-gray-400">No scheduled jobs.</p>
      ) : (
        <ul id={listId} className="mt-4 space-y-3" aria-label="Scheduled job list">
          {jobs.map((job) => {
            const controls = availableScheduledJobControls(job.state);
            const pending = job.pendingControl !== null;
            return (
              <li
                key={job.jobId}
                className="rounded-lg border border-gray-700 bg-[#0f0f1a]/70 p-3"
              >
                <div className="space-y-1 text-sm text-gray-200">
                  <p className="font-medium text-gray-100 break-words">
                    {job.actionLabel || "Scheduled action"}
                  </p>
                  <p>
                    <span className="text-gray-400">State: </span>
                    {stateLabel(job.state)}
                  </p>
                  <p className="break-words">
                    <span className="text-gray-400">Next run: </span>
                    {job.nextRunLabel || "Not scheduled"}
                  </p>
                  <p>
                    <span className="text-gray-400">Ownership: </span>
                    {job.ownershipLabel}
                  </p>
                  {job.quietHoursLabel ? (
                    <p className="break-words">
                      <span className="text-gray-400">Quiet hours: </span>
                      {job.quietHoursLabel}
                    </p>
                  ) : null}
                  <p>
                    <span className="text-gray-400">Attempts: </span>
                    {job.attemptCount} of {job.maxAttempts}
                  </p>
                </div>

                {controls.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {controls.includes("pause") ? (
                      <button
                        type="button"
                        onClick={() => onPause(job.jobId)}
                        disabled={pending}
                        aria-label={`Pause ${job.actionLabel || "scheduled action"}`}
                        className="rounded-lg border border-gray-600 px-3 py-1.5 text-sm font-semibold text-gray-100 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        Pause
                      </button>
                    ) : null}
                    {controls.includes("resume") ? (
                      <button
                        type="button"
                        onClick={() => onResume(job.jobId)}
                        disabled={pending}
                        aria-label={`Resume ${job.actionLabel || "scheduled action"}`}
                        className="rounded-lg border border-gray-600 px-3 py-1.5 text-sm font-semibold text-gray-100 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        Resume
                      </button>
                    ) : null}
                    {controls.includes("cancel") ? (
                      <button
                        type="button"
                        onClick={() => onCancel(job.jobId)}
                        disabled={pending}
                        aria-label={`Cancel ${job.actionLabel || "scheduled action"}`}
                        className="rounded-lg border border-red-700/70 px-3 py-1.5 text-sm font-semibold text-red-100 hover:bg-red-950/40 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        Cancel
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
