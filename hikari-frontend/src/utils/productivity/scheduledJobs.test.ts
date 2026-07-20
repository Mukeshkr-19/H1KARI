import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { PREVIEW_LABEL_MAX } from "./actionPreview";
import {
  GENERIC_SCHEDULED_JOB_ERROR_MESSAGE,
  SCHEDULED_JOB_ATTEMPT_MAX,
  SCHEDULED_JOB_LIST_MAX,
  SCHEDULED_JOB_OWNERSHIP_LABEL,
  availableScheduledJobControls,
  clearScheduledJobPendingControl,
  isTerminalScheduledJobState,
  isValidJobId,
  mapScheduledJobErrorMessage,
  parseScheduledJobList,
  parseScheduledJobView,
  replaceScheduledJobInList,
  setScheduledJobPendingControl,
  updateScheduledJobState,
  type ScheduledJobState,
  type ScheduledJobView,
} from "./scheduledJobs";

function sampleJob(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    jobId: "job-1",
    actionLabel: "email.draft",
    state: "scheduled",
    nextRunLabel: "Tomorrow 09:00",
    attemptCount: 0,
    maxAttempts: 3,
    pendingControl: null,
    ...overrides,
  };
}

function parsed(overrides: Record<string, unknown> = {}): ScheduledJobView {
  const job = parseScheduledJobView(sampleJob(overrides));
  assert.ok(job);
  return job;
}

describe("scheduledJobs", () => {
  it("parses every job state into an immutable view", () => {
    const states: ScheduledJobState[] = [
      "scheduled",
      "paused",
      "running",
      "interrupted",
      "completed",
      "failed",
      "cancelled",
    ];
    for (const state of states) {
      const job = parsed({ state });
      assert.equal(job.state, state);
      assert.equal(job.ownershipLabel, SCHEDULED_JOB_OWNERSHIP_LABEL);
      assert.throws(() => {
        (job as { actionLabel: string }).actionLabel = "nope";
      }, TypeError);
    }
  });

  it("exposes the correct controls for each state", () => {
    assert.deepEqual([...availableScheduledJobControls("scheduled")], [
      "pause",
      "cancel",
    ]);
    assert.deepEqual([...availableScheduledJobControls("paused")], [
      "resume",
      "cancel",
    ]);
    assert.deepEqual([...availableScheduledJobControls("running")], ["cancel"]);
    assert.deepEqual([...availableScheduledJobControls("interrupted")], [
      "resume",
      "cancel",
    ]);
    for (const state of ["completed", "failed", "cancelled"] as const) {
      assert.deepEqual([...availableScheduledJobControls(state)], []);
      assert.equal(isTerminalScheduledJobState(state), true);
    }
  });

  it("rejects malformed ids without trimming or truncation", () => {
    for (const jobId of [
      "BAD",
      "Job-1",
      "job:1",
      " job-1",
      "job-1 ",
      "café",
      "id\u0000",
      `${"a".repeat(80)}x`,
      "",
    ]) {
      assert.equal(isValidJobId(jobId), false);
      assert.equal(parseScheduledJobView(sampleJob({ jobId })), null);
    }
    const exact = "a".repeat(80);
    assert.equal(isValidJobId(exact), true);
    assert.ok(parseScheduledJobView(sampleJob({ jobId: exact })));
  });

  it("rejects unknown fields and privacy-sensitive keys", () => {
    for (const banned of [
      { actorId: "actor-1" },
      { sessionId: "session-1" },
      { proposalPayload: { body: "secret" } },
      { query: "SELECT 1" },
      { emailBody: "hello" },
      { calendarContent: "meeting" },
      { providerResponse: { ok: true } },
      { token: "secret" },
      { extra: true },
    ]) {
      assert.equal(
        parseScheduledJobView({ ...sampleJob(), ...banned }),
        null,
      );
    }
  });

  it("rejects malformed objects and non-array lists", () => {
    assert.equal(parseScheduledJobView(null), null);
    assert.equal(parseScheduledJobView([]), null);
    assert.equal(parseScheduledJobView("job"), null);
    assert.equal(parseScheduledJobList({}), null);
    assert.equal(parseScheduledJobList(null), null);
  });

  it("rejects boolean negative inconsistent and overflow attempt counts", () => {
    for (const attemptCount of [true, false, -1, 1.5, SCHEDULED_JOB_ATTEMPT_MAX + 1, "1"]) {
      assert.equal(
        parseScheduledJobView(sampleJob({ attemptCount })),
        null,
      );
    }
    for (const maxAttempts of [true, false, -1, SCHEDULED_JOB_ATTEMPT_MAX + 1]) {
      assert.equal(
        parseScheduledJobView(sampleJob({ maxAttempts })),
        null,
      );
    }
    assert.equal(parseScheduledJobView(sampleJob({ maxAttempts: 0 })), null);
    assert.equal(
      parseScheduledJobView(sampleJob({ attemptCount: 3, maxAttempts: 2 })),
      null,
    );
    assert.ok(
      parseScheduledJobView(
        sampleJob({
          attemptCount: SCHEDULED_JOB_ATTEMPT_MAX,
          maxAttempts: SCHEDULED_JOB_ATTEMPT_MAX,
        }),
      ),
    );
  });

  it("rejects pending controls that are unavailable for the job state", () => {
    assert.equal(
      parseScheduledJobView(sampleJob({ state: "completed", pendingControl: "cancel" })),
      null,
    );
    assert.equal(
      parseScheduledJobView(sampleJob({ state: "scheduled", pendingControl: "resume" })),
      null,
    );
    assert.ok(
      parseScheduledJobView(sampleJob({ state: "scheduled", pendingControl: "pause" })),
    );
  });

  it("rejects oversized job lists and duplicate ids", () => {
    const tooMany = Array.from({ length: SCHEDULED_JOB_LIST_MAX + 1 }, (_, i) =>
      sampleJob({ jobId: `job-${i}` }),
    );
    assert.equal(parseScheduledJobList(tooMany), null);
    assert.equal(
      parseScheduledJobList([sampleJob(), sampleJob({ jobId: "job-1" })]),
      null,
    );
    const list = parseScheduledJobList([
      sampleJob({ jobId: "job-a" }),
      sampleJob({ jobId: "job-b", state: "paused" }),
    ]);
    assert.ok(list);
    assert.equal(list.length, 2);
  });

  it("bounds and sanitizes display text", () => {
    const job = parsed({
      actionLabel: `A\u0000${"a".repeat(PREVIEW_LABEL_MAX + 5)}`,
      nextRunLabel: `N\u202E${"n".repeat(PREVIEW_LABEL_MAX + 2)}`,
      quietHoursLabel: `Q${"q".repeat(PREVIEW_LABEL_MAX + 1)}`,
    });
    assert.equal(job.actionLabel.endsWith("…"), true);
    assert.equal(job.actionLabel.includes("\u0000"), false);
    assert.equal(job.nextRunLabel.includes("\u202E"), false);
    assert.equal(job.quietHoursLabel?.endsWith("…"), true);
    assert.equal(job.ownershipLabel, "Current session");
  });

  it("sets pending controls and rejects stale ids", () => {
    const job = parsed({ state: "scheduled" });
    const pending = setScheduledJobPendingControl(job, "job-1", "pause");
    assert.ok(pending);
    assert.equal(pending.pendingControl, "pause");
    assert.equal(setScheduledJobPendingControl(job, "job-stale", "pause"), null);
    assert.equal(setScheduledJobPendingControl(pending, "job-1", "cancel"), null);
    assert.equal(setScheduledJobPendingControl(job, "job-1", "resume"), null);

    const cleared = clearScheduledJobPendingControl(pending, "job-1");
    assert.ok(cleared);
    assert.equal(cleared.pendingControl, null);
    assert.equal(clearScheduledJobPendingControl(pending, "stale"), null);
  });

  it("updates state only for matching job ids and clears pending", () => {
    const job = parsed({ state: "scheduled", pendingControl: "pause" });
    const updated = updateScheduledJobState(job, "job-1", "paused");
    assert.ok(updated);
    assert.equal(updated.state, "paused");
    assert.equal(updated.pendingControl, null);
    assert.equal(updateScheduledJobState(job, "other", "paused"), null);

    const list = parseScheduledJobList([
      sampleJob({ jobId: "job-a" }),
      sampleJob({ jobId: "job-b" }),
    ]);
    assert.ok(list);
    const replaced = replaceScheduledJobInList(list, updated);
    assert.equal(replaced, null);
    const nextA = updateScheduledJobState(list[0], "job-a", "running");
    assert.ok(nextA);
    const replacedOk = replaceScheduledJobInList(list, nextA);
    assert.ok(replacedOk);
    assert.equal(replacedOk[0].state, "running");
    assert.equal(replacedOk[1].jobId, "job-b");
  });

  it("maps only safe error codes and never raw detail", () => {
    assert.equal(
      mapScheduledJobErrorMessage("control_failed"),
      "The job control could not be completed.",
    );
    assert.equal(
      mapScheduledJobErrorMessage("Error: boom https://evil.example"),
      GENERIC_SCHEDULED_JOB_ERROR_MESSAGE,
    );
    assert.equal(
      mapScheduledJobErrorMessage({ message: "stack" }),
      GENERIC_SCHEDULED_JOB_ERROR_MESSAGE,
    );
  });
});
