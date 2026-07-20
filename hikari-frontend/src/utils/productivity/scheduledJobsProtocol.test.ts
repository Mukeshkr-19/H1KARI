import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { PREVIEW_LABEL_MAX } from "./actionPreview";
import { SCHEDULED_JOB_LIST_MAX, SCHEDULED_JOB_OWNERSHIP_LABEL } from "./scheduledJobs";
import {
  encodeScheduledJobCancel,
  encodeScheduledJobCreate,
  encodeScheduledJobPause,
  encodeScheduledJobResume,
  encodeScheduledJobsList,
  parseScheduledJobsServerMessage,
} from "./scheduledJobsProtocol";
import { createEmptyScheduleProposalFields } from "./scheduleProposal";

function wireJob(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    job_id: "job-1",
    action: "email.draft",
    state: "scheduled",
    next_run_at: 1_700_000_000,
    attempt_count: 0,
    max_attempts: 3,
    ...overrides,
  };
}

describe("scheduledJobsProtocol", () => {
  it("parses a valid scheduled_jobs list as immutable", () => {
    const parsed = parseScheduledJobsServerMessage({
      type: "scheduled_jobs",
      jobs: [wireJob(), wireJob({ job_id: "job-2", state: "paused" })],
    });
    assert.ok(parsed);
    assert.equal(parsed.type, "scheduled_jobs");
    if (parsed.type !== "scheduled_jobs") {
      return;
    }
    assert.equal(parsed.jobs.length, 2);
    assert.equal(parsed.jobs[0].ownershipLabel, SCHEDULED_JOB_OWNERSHIP_LABEL);
    assert.equal(parsed.jobs[0].pendingControl, null);
    assert.equal(parsed.jobs[0].jobId, "job-1");
    assert.equal(parsed.jobs[0].actionLabel, "email.draft");
    assert.equal(parsed.jobs[0].nextRunLabel, "1700000000");
    assert.throws(() => {
      (parsed as { type: string }).type = "nope";
    }, TypeError);
    assert.throws(() => {
      (parsed.jobs as unknown as { jobId: string }[]).push({ jobId: "x" });
    }, TypeError);
  });

  it("parses every job state through scheduled_job_update", () => {
    for (const state of [
      "scheduled",
      "paused",
      "running",
      "interrupted",
      "completed",
      "failed",
      "cancelled",
    ]) {
      const parsed = parseScheduledJobsServerMessage({
        type: "scheduled_job_update",
        job: wireJob({ state }),
      });
      assert.ok(parsed);
      assert.equal(parsed.type, "scheduled_job_update");
      if (parsed.type === "scheduled_job_update") {
        assert.equal(parsed.job.state, state);
        assert.equal(parsed.job.ownershipLabel, "Current session");
      }
    }
  });

  it("parses scheduled_job_error with safe codes only", () => {
    for (const code of ["control_failed", "job_not_found", "unavailable"]) {
      const parsed = parseScheduledJobsServerMessage({
        type: "scheduled_job_error",
        job_id: "job-1",
        code,
      });
      assert.ok(parsed);
      assert.equal(parsed.type, "scheduled_job_error");
      if (parsed.type === "scheduled_job_error") {
        assert.equal(parsed.code, code);
      }
    }
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_job_error",
        job_id: "job-1",
        code: "provider_timeout",
      }),
      null,
    );
  });

  it("rejects malformed and duplicate job ids", () => {
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_jobs",
        jobs: [wireJob({ job_id: "BAD" })],
      }),
      null,
    );
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_jobs",
        jobs: [wireJob({ job_id: "job:1" })],
      }),
      null,
    );
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_jobs",
        jobs: [wireJob(), wireJob({ job_id: "job-1", state: "paused" })],
      }),
      null,
    );
  });

  it("rejects oversized job lists", () => {
    const jobs = Array.from({ length: SCHEDULED_JOB_LIST_MAX + 1 }, (_, i) =>
      wireJob({ job_id: `job-${i}` }),
    );
    assert.equal(
      parseScheduledJobsServerMessage({ type: "scheduled_jobs", jobs }),
      null,
    );
  });

  it("rejects unknown fields on messages and jobs", () => {
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_jobs",
        jobs: [],
        extra: true,
      }),
      null,
    );
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_job_update",
        job: wireJob({ actor_id: "actor-1" }),
      }),
      null,
    );
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_job_update",
        job: wireJob({ session_id: "s1" }),
      }),
      null,
    );
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_job_update",
        job: wireJob({ proposal_id: "p1" }),
      }),
      null,
    );
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_job_error",
        job_id: "job-1",
        code: "unavailable",
        message: "secret stack",
      }),
      null,
    );
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_job_error",
        job_id: "job-1",
        code: "unavailable",
        provider: "smtp",
        detail: { raw: true },
      }),
      null,
    );
  });

  it("rejects inconsistent attempt counts", () => {
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_job_update",
        job: wireJob({ max_attempts: 0 }),
      }),
      null,
    );
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_job_update",
        job: wireJob({ attempt_count: 4, max_attempts: 3 }),
      }),
      null,
    );
    assert.equal(
      parseScheduledJobsServerMessage({
        type: "scheduled_job_update",
        job: wireJob({ attempt_count: true, max_attempts: 3 }),
      }),
      null,
    );
  });

  it("bounds and sanitizes action and quiet hours labels", () => {
    const parsed = parseScheduledJobsServerMessage({
      type: "scheduled_job_update",
      job: wireJob({
        action: `A\u0000${"a".repeat(PREVIEW_LABEL_MAX + 4)}`,
        quiet_hours_label: `Q\u202E${"q".repeat(PREVIEW_LABEL_MAX + 2)}`,
        next_run_at: "Tomorrow\u0007 09:00",
      }),
    });
    assert.ok(parsed);
    if (parsed.type !== "scheduled_job_update") {
      assert.fail("expected update");
    }
    assert.equal(parsed.job.actionLabel.endsWith("…"), true);
    assert.equal(parsed.job.actionLabel.includes("\u0000"), false);
    assert.equal(parsed.job.quietHoursLabel?.includes("\u202E"), false);
    assert.equal(parsed.job.nextRunLabel.includes("\u0007"), false);
  });

  it("encodes list pause resume and cancel requests", () => {
    assert.deepEqual(encodeScheduledJobsList(), {
      type: "scheduled_jobs_list",
    });
    assert.deepEqual(encodeScheduledJobPause("job-1"), {
      type: "scheduled_job_pause",
      job_id: "job-1",
    });
    assert.deepEqual(encodeScheduledJobResume("job-1"), {
      type: "scheduled_job_resume",
      job_id: "job-1",
    });
    assert.deepEqual(encodeScheduledJobCancel("job-1"), {
      type: "scheduled_job_cancel",
      job_id: "job-1",
    });
    assert.equal(encodeScheduledJobPause("BAD"), null);
    assert.equal(encodeScheduledJobResume("job:1"), null);
    assert.equal(encodeScheduledJobCancel(""), null);
  });

  it("encodes one exact scheduled read creation request", () => {
    const fields = {
      ...createEmptyScheduleProposalFields(),
      nextRunAt: "2026-07-21T09:00:00Z",
      maxAttempts: "3",
    };
    assert.deepEqual(
      encodeScheduledJobCreate(
        "request-1",
        "proposal-1",
        fields,
        () => BigInt("1784620800000000"),
      ),
      {
        type: "scheduled_job_create",
        request_id: "request-1",
        proposal_id: "proposal-1",
        next_run_at: "2026-07-21T09:00:00Z",
        max_attempts: 3,
      },
    );
    assert.equal(
      encodeScheduledJobCreate("bad id", "proposal-1", fields, () => BigInt(0)),
      null,
    );
  });

  it("parses correlated creation updates and scheduled read results", () => {
    const update = parseScheduledJobsServerMessage({
      type: "scheduled_job_update",
      request_id: "request-1",
      job: wireJob({ action: "browser.research" }),
    });
    assert.ok(update && update.type === "scheduled_job_update");
    if (update?.type === "scheduled_job_update") {
      assert.equal(update.request_id, "request-1");
    }
    const result = parseScheduledJobsServerMessage({
      type: "scheduled_job_research_result",
      job_id: "job-1",
      items: [
        {
          title: "Release",
          url: "https://example.com/release",
          domain: "example.com",
        },
      ],
    });
    assert.ok(result && result.type === "scheduled_job_research_result");
  });

  it("parses JSON strings and returns null without throwing on bad JSON", () => {
    const parsed = parseScheduledJobsServerMessage(
      JSON.stringify({
        type: "scheduled_jobs",
        jobs: [wireJob()],
      }),
    );
    assert.ok(parsed);
    assert.equal(parsed.type, "scheduled_jobs");
    assert.equal(parseScheduledJobsServerMessage("{not-json"), null);
    assert.equal(parseScheduledJobsServerMessage(null), null);
    assert.equal(parseScheduledJobsServerMessage([]), null);
    assert.equal(
      parseScheduledJobsServerMessage({ type: "productivity_update" }),
      null,
    );
  });
});
