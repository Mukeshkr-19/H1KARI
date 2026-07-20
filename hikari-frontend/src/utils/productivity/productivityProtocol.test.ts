import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { PREVIEW_LABEL_MAX } from "./actionPreview";
import { PREVIEW_ENTRY_MAX } from "./actionLifecycle";
import {
  PRODUCTIVITY_ACTIONS,
  encodeProductivityCancel,
  encodeProductivityConfirm,
  encodeProductivityCalendarDraftPrepare,
  encodeProductivityCalendarReadPrepare,
  encodeProductivityEmailDraftPrepare,
  encodeProductivityResearchPrepare,
  encodeProductivityReminderPrepare,
  encodeProductivityStatus,
  parseProductivityServerMessage,
} from "./productivityProtocol";
import {
  createApprovalScopeStateFromAllowed,
  selectApprovalDuration,
  selectApprovalScope,
  setPersistentAcknowledgement,
} from "./approvalScopes";

function confirmation(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    type: "productivity_confirmation_required",
    proposal_id: "proposal-1",
    action: "email.draft",
    heading: "Send draft",
    risk_label: "medium",
    targets: [{ label: "To", value: "user@example.com" }],
    payload: [{ label: "Subject", value: "Hello\nWorld" }],
    expires_at: 1_700_000_000,
    allowed_scopes: ["once"],
    ...overrides,
  };
}

describe("productivityProtocol", () => {
  it("parses a valid confirmation_required message as immutable", () => {
    const parsed = parseProductivityServerMessage(confirmation());
    assert.ok(parsed);
    assert.equal(parsed.type, "productivity_confirmation_required");
    if (parsed.type !== "productivity_confirmation_required") {
      return;
    }
    assert.equal(parsed.proposal_id, "proposal-1");
    assert.equal(parsed.action, "email.draft");
    assert.equal(parsed.expires_at, 1_700_000_000);
    assert.deepEqual([...parsed.allowed_scopes], ["once"]);
    assert.throws(() => {
      (parsed as { heading: string }).heading = "nope";
    }, TypeError);
    assert.throws(() => {
      (parsed.targets as unknown as { label: string; value: string }[]).push({
        label: "x",
        value: "y",
      });
    }, TypeError);
  });

  it("accepts every ProductivityAction value", () => {
    for (const action of PRODUCTIVITY_ACTIONS) {
      const parsed = parseProductivityServerMessage(confirmation({ action }));
      assert.ok(parsed);
      assert.equal(parsed.type, "productivity_confirmation_required");
      if (parsed.type === "productivity_confirmation_required") {
        assert.equal(parsed.action, action);
      }
    }
  });

  it("parses non-empty duplicate-free ordered allowed_scopes subsets", () => {
    const ok = parseProductivityServerMessage(
      confirmation({ allowed_scopes: ["once"] }),
    );
    assert.ok(ok);
    if (ok.type === "productivity_confirmation_required") {
      assert.deepEqual([...ok.allowed_scopes], ["once"]);
    }
    const multi = parseProductivityServerMessage(
      confirmation({
        allowed_scopes: ["session", "duration", "precise_persistent"],
      }),
    );
    assert.ok(multi);
    if (multi.type === "productivity_confirmation_required") {
      assert.deepEqual([...multi.allowed_scopes], [
        "session",
        "duration",
        "precise_persistent",
      ]);
    }
    assert.equal(
      parseProductivityServerMessage(confirmation({ allowed_scopes: [] })),
      null,
    );
    assert.equal(
      parseProductivityServerMessage(
        confirmation({ allowed_scopes: ["once", "once"] }),
      ),
      null,
    );
    assert.equal(
      parseProductivityServerMessage(
        confirmation({ allowed_scopes: ["once", "always"] }),
      ),
      null,
    );
    assert.equal(
      parseProductivityServerMessage(confirmation({ allowed_scopes: "once" })),
      null,
    );
  });

  it("rejects empty allowed_scopes that were previously accepted", () => {
    assert.equal(
      parseProductivityServerMessage(confirmation({ allowed_scopes: [] })),
      null,
    );
  });

  it("parses productivity_update statuses", () => {
    for (const status of [
      "preview",
      "confirming",
      "approved",
      "executing",
      "completed",
      "failed",
      "cancelling",
      "cancelled",
    ]) {
      const parsed = parseProductivityServerMessage({
        type: "productivity_update",
        proposal_id: "proposal-1",
        status,
      });
      assert.ok(parsed);
      assert.equal(parsed.type, "productivity_update");
      if (parsed.type === "productivity_update") {
        assert.equal(parsed.status, status);
      }
    }
  });

  it("parses productivity_error with safe codes only", () => {
    for (const code of [
      "confirm_failed",
      "cancel_failed",
      "proposal_expired",
      "proposal_invalid",
      "unavailable",
    ]) {
      const parsed = parseProductivityServerMessage({
        type: "productivity_error",
        proposal_id: "proposal-1",
        code,
      });
      assert.ok(parsed);
      assert.equal(parsed.type, "productivity_error");
      if (parsed.type === "productivity_error") {
        assert.equal(parsed.code, code);
      }
    }
  });

  it("rejects unknown message types", () => {
    assert.equal(
      parseProductivityServerMessage({
        type: "companion_update",
        proposal_id: "proposal-1",
      }),
      null,
    );
    assert.equal(
      parseProductivityServerMessage({ type: "productivity_unknown" }),
      null,
    );
  });

  it("rejects unknown fields on every accepted message type", () => {
    assert.equal(
      parseProductivityServerMessage(confirmation({ extra: true })),
      null,
    );
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_update",
        proposal_id: "proposal-1",
        status: "preview",
        progress: 1,
      }),
      null,
    );
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_error",
        proposal_id: "proposal-1",
        code: "unavailable",
        message: "secret",
      }),
      null,
    );
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_error",
        proposal_id: "proposal-1",
        code: "unavailable",
        detail: "stack",
        provider: "smtp",
      }),
      null,
    );
  });

  it("rejects malformed proposal ids without trimming or truncation collisions", () => {
    for (const proposal_id of [
      "BAD",
      "Proposal-1",
      "proposal:1",
      "proposal 1",
      "café",
      "id\u0000",
      "id\u200E",
      " proposal-1",
      "proposal-1 ",
      `${"a".repeat(80)}x`,
      "",
    ]) {
      assert.equal(
        parseProductivityServerMessage(confirmation({ proposal_id })),
        null,
      );
    }
    const exact = "a".repeat(80);
    const accepted = parseProductivityServerMessage(
      confirmation({ proposal_id: exact }),
    );
    assert.ok(accepted);
    assert.equal(
      parseProductivityServerMessage(
        confirmation({ proposal_id: `${exact}x` }),
      ),
      null,
    );
    assert.equal(
      parseProductivityServerMessage(confirmation({ action: "email.send" })),
      null,
    );
  });

  it("rejects malformed or oversized targets and payload arrays", () => {
    assert.equal(
      parseProductivityServerMessage(
        confirmation({ targets: { label: "To", value: "x" } }),
      ),
      null,
    );
    assert.equal(
      parseProductivityServerMessage(confirmation({ payload: "x" })),
      null,
    );
    assert.equal(
      parseProductivityServerMessage(
        confirmation({
          targets: [{ label: "To", value: "x", extra: true }],
        }),
      ),
      null,
    );
    const tooMany = Array.from({ length: PREVIEW_ENTRY_MAX + 1 }, (_, i) => ({
      label: `t${i}`,
      value: `v${i}`,
    }));
    assert.equal(
      parseProductivityServerMessage(confirmation({ targets: tooMany })),
      null,
    );
    assert.equal(
      parseProductivityServerMessage(confirmation({ payload: tooMany })),
      null,
    );
  });

  it("rejects non-finite expires_at values", () => {
    for (const expires_at of [
      Number.NaN,
      Number.POSITIVE_INFINITY,
      Number.NEGATIVE_INFINITY,
      true,
      false,
      "1",
    ]) {
      assert.equal(
        parseProductivityServerMessage(confirmation({ expires_at })),
        null,
      );
    }
  });

  it("accepts only the five safe productivity error codes", () => {
    for (const code of [
      "confirm_failed",
      "cancel_failed",
      "proposal_expired",
      "proposal_invalid",
      "unavailable",
    ]) {
      const parsed = parseProductivityServerMessage({
        type: "productivity_error",
        proposal_id: "proposal-1",
        code,
      });
      assert.ok(parsed);
      if (parsed.type === "productivity_error") {
        assert.equal(parsed.code, code);
      }
    }
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_error",
        proposal_id: "proposal-1",
        code: "provider_timeout",
      }),
      null,
    );
  });

  it("bounds heading risk_label and entry text through preview helpers", () => {
    const parsed = parseProductivityServerMessage(
      confirmation({
        heading: `H\u0000${"h".repeat(PREVIEW_LABEL_MAX + 4)}`,
        risk_label: `R\u202E${"r".repeat(PREVIEW_LABEL_MAX + 2)}`,
        targets: [
          {
            label: `L${"l".repeat(PREVIEW_LABEL_MAX)}`,
            value: "ok",
          },
        ],
        payload: [{ label: "Body", value: "keep\n\ttab" }],
      }),
    );
    assert.ok(parsed);
    if (parsed.type !== "productivity_confirmation_required") {
      assert.fail("expected confirmation");
    }
    assert.equal(parsed.heading.endsWith("…"), true);
    assert.equal(parsed.heading.includes("\u0000"), false);
    assert.equal(parsed.risk_label.endsWith("…"), true);
    assert.equal(parsed.risk_label.includes("\u202E"), false);
    assert.equal(parsed.targets[0].truncated, true);
    assert.equal(parsed.payload[0].value, "keep\n\ttab");
  });

  it("never retains provider message detail or stack on errors", () => {
    const hostile = {
      type: "productivity_error",
      proposal_id: "proposal-1",
      code: "confirm_failed",
      message: "Error: boom https://evil.example?token=1",
      stack: "at Object.<anonymous>",
      provider: "smtp",
      detail: { raw: true },
    };
    assert.equal(parseProductivityServerMessage(hostile), null);
    const ok = parseProductivityServerMessage({
      type: "productivity_error",
      proposal_id: "proposal-1",
      code: "confirm_failed",
    });
    assert.ok(ok);
    assert.equal(JSON.stringify(ok).includes("evil.example"), false);
    assert.equal(JSON.stringify(ok).includes("stack"), false);
  });

  it("rejects unknown update statuses including idle", () => {
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_update",
        proposal_id: "proposal-1",
        status: "idle",
      }),
      null,
    );
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_update",
        proposal_id: "proposal-1",
        status: "running",
      }),
      null,
    );
  });

  it("encodes confirm cancel and status only for valid proposal ids", () => {
    const onceState = createApprovalScopeStateFromAllowed(["once"])!;
    assert.deepEqual(encodeProductivityConfirm("proposal-1", onceState), {
      type: "productivity_confirm",
      proposal_id: "proposal-1",
      scope: "once",
    });
    assert.deepEqual(encodeProductivityCancel("proposal-1"), {
      type: "productivity_cancel",
      proposal_id: "proposal-1",
    });
    assert.deepEqual(encodeProductivityStatus("proposal-1"), {
      type: "productivity_status",
      proposal_id: "proposal-1",
    });
    assert.equal(encodeProductivityConfirm("BAD", onceState), null);
    assert.equal(encodeProductivityCancel("BAD"), null);
    assert.equal(encodeProductivityStatus("BAD"), null);
    assert.equal(
      encodeProductivityConfirm(`${"a".repeat(80)}x`, onceState),
      null,
    );
  });

  it("encodes exact confirm fields for every ready scope", () => {
    const all = createApprovalScopeStateFromAllowed([
      "once",
      "session",
      "duration",
      "precise_persistent",
    ])!;

    assert.deepEqual(encodeProductivityConfirm("proposal-1", all), {
      type: "productivity_confirm",
      proposal_id: "proposal-1",
      scope: "once",
    });

    const session = selectApprovalScope(all, "session")!;
    assert.deepEqual(encodeProductivityConfirm("proposal-1", session), {
      type: "productivity_confirm",
      proposal_id: "proposal-1",
      scope: "session",
    });
    assert.equal(
      JSON.stringify(encodeProductivityConfirm("proposal-1", session)).includes(
        "duration_seconds",
      ),
      false,
    );
    assert.equal(
      JSON.stringify(encodeProductivityConfirm("proposal-1", session)).includes(
        "acknowledged",
      ),
      false,
    );

    const durationPending = selectApprovalScope(all, "duration")!;
    assert.equal(encodeProductivityConfirm("proposal-1", durationPending), null);
    const durationReady = selectApprovalDuration(durationPending, "1_hour")!;
    assert.deepEqual(encodeProductivityConfirm("proposal-1", durationReady), {
      type: "productivity_confirm",
      proposal_id: "proposal-1",
      scope: "duration",
      duration_seconds: 3600,
    });
    assert.equal(
      JSON.stringify(
        encodeProductivityConfirm("proposal-1", durationReady),
      ).includes("acknowledged"),
      false,
    );

    const persistentPending = selectApprovalScope(all, "precise_persistent")!;
    assert.equal(
      encodeProductivityConfirm("proposal-1", persistentPending),
      null,
    );
    const persistentReady = setPersistentAcknowledgement(
      persistentPending,
      true,
    )!;
    assert.deepEqual(encodeProductivityConfirm("proposal-1", persistentReady), {
      type: "productivity_confirm",
      proposal_id: "proposal-1",
      scope: "precise_persistent",
      acknowledged: true,
    });
    assert.equal(
      JSON.stringify(
        encodeProductivityConfirm("proposal-1", persistentReady),
      ).includes("duration_seconds"),
      false,
    );

    for (const seconds of [900, 3600, 28800] as const) {
      const choice =
        seconds === 900
          ? "15_minutes"
          : seconds === 3600
            ? "1_hour"
            : "8_hours";
      const ready = selectApprovalDuration(durationPending, choice)!;
      const encoded = encodeProductivityConfirm("proposal-1", ready);
      assert.ok(encoded);
      assert.equal(encoded.scope, "duration");
      if (encoded.scope === "duration") {
        assert.equal(encoded.duration_seconds, seconds);
      }
    }

    const encodedOnce = encodeProductivityConfirm("proposal-1", onceStateForPrivacy());
    assert.ok(encodedOnce);
    const raw = JSON.stringify(encodedOnce);
    assert.equal(raw.includes("actor"), false);
    assert.equal(raw.includes("session_id"), false);
    assert.equal(raw.includes("approval_id"), false);
    assert.equal(raw.includes("targets"), false);
    assert.equal(raw.includes("payload"), false);
  });

  it("encodes email draft prepare with exact fields only", () => {
    assert.deepEqual(
      encodeProductivityEmailDraftPrepare({
        type: "productivity_email_draft_prepare",
        request_id: "email-req-1",
        recipient: "bob@example.com",
        subject: "Ping",
        body: "Hello\nWorld",
      }),
      {
        type: "productivity_email_draft_prepare",
        request_id: "email-req-1",
        recipient: "bob@example.com",
        subject: "Ping",
        body: "Hello\nWorld",
      },
    );
    assert.equal(
      encodeProductivityEmailDraftPrepare({
        type: "productivity_email_draft_prepare",
        request_id: "email-req-1",
        recipient: "bob@example.com",
        subject: "Ping",
        body: "Hello",
        actor_id: "actor-1",
      }),
      null,
    );
    assert.equal(
      encodeProductivityEmailDraftPrepare({
        type: "productivity_email_draft_prepare",
        recipient: "bob@example.com",
        subject: "Ping",
        body: "Hello",
      }),
      null,
    );
    assert.equal(
      encodeProductivityEmailDraftPrepare({
        type: "productivity_email_draft_prepare",
        request_id: "Bad ID",
        recipient: "bob@example.com",
        subject: "Ping",
        body: "Hello",
      }),
      null,
    );
    assert.equal(
      encodeProductivityEmailDraftPrepare({
        type: "productivity_email_draft_prepare",
        request_id: "email-req-1",
        recipient: "",
        subject: "Ping",
        body: "Hello",
      }),
      null,
    );
  });

  it("encodes calendar read and draft prepare messages with exact protocol fields", () => {
    const read = encodeProductivityCalendarReadPrepare({
      type: "productivity_calendar_read_prepare",
      request_id: "cal-read-1",
      start: "2026-07-18T09:00:00-04:00",
      end: "2026-07-18T10:00:00-04:00",
      calendar_name: "Work",
    });
    assert.ok(read);
    assert.equal(read.type, "productivity_calendar_read_prepare");
    assert.equal(read.request_id, "cal-read-1");
    assert.equal(read.start, "2026-07-18T09:00:00-04:00");
    assert.equal(read.end, "2026-07-18T10:00:00-04:00");
    assert.equal(read.calendar_name, "Work");

    const draft = encodeProductivityCalendarDraftPrepare({
      type: "productivity_calendar_draft_prepare",
      request_id: "cal-draft-1",
      title: "Planning",
      start: "2026-07-19T14:00:00Z",
      end: "2026-07-19T15:30:00Z",
      calendar_name: "Work",
      location: "Office",
      notes: "Bring notes",
    });
    assert.ok(draft);
    assert.equal(draft.type, "productivity_calendar_draft_prepare");
    assert.equal(draft.title, "Planning");
    assert.equal(draft.calendar_name, "Work");
    assert.equal(draft.location, "Office");
    assert.equal(draft.notes, "Bring notes");
  });

  it("rejects malformed calendar prepare encodes and unknown fields", () => {
    assert.equal(
      encodeProductivityCalendarReadPrepare({
        type: "productivity_calendar_read_prepare",
        request_id: "Bad ID",
        start: "2026-07-18T09:00:00-04:00",
        end: "2026-07-18T10:00:00-04:00",
      }),
      null,
    );
    assert.equal(
      encodeProductivityCalendarReadPrepare({
        type: "productivity_calendar_read_prepare",
        request_id: "cal-read-1",
        start: "2026-07-18T09:00:00-04:00",
        end: "2026-07-18T10:00:00-04:00",
        proposal_id: "prop-1",
      }),
      null,
    );
    assert.equal(
      encodeProductivityCalendarDraftPrepare({
        type: "productivity_calendar_draft_prepare",
        request_id: "cal-draft-1",
        title: "",
        start: "2026-07-19T14:00:00Z",
        end: "2026-07-19T15:30:00Z",
        calendar_name: "Work",
      }),
      null,
    );
    assert.equal(
      encodeProductivityCalendarDraftPrepare({
        type: "productivity_calendar_draft_prepare",
        request_id: "cal-draft-1",
        title: "Planning",
        start: "2026-07-19T14:00:00Z",
        end: "2026-07-19T15:30:00Z",
      }),
      null,
    );
  });

  it("encodes research prepare messages with exact protocol fields", () => {
    const withDomains = encodeProductivityResearchPrepare({
      type: "productivity_research_prepare",
      request_id: "research-req-1",
      query: "Latest release notes",
      domains: ["example.com"],
      max_results: 5,
    });
    assert.ok(withDomains);
    assert.equal(withDomains.type, "productivity_research_prepare");
    assert.equal(withDomains.query, "Latest release notes");
    assert.deepEqual(withDomains.domains, ["example.com"]);
    assert.equal(withDomains.max_results, 5);

    const minimal = encodeProductivityResearchPrepare({
      type: "productivity_research_prepare",
      request_id: "research-req-2",
      query: "Open questions",
    });
    assert.ok(minimal);
    assert.equal("domains" in minimal, false);
    assert.equal("max_results" in minimal, false);
  });

  it("rejects malformed research prepare encodes and unknown fields", () => {
    assert.equal(
      encodeProductivityResearchPrepare({
        type: "productivity_research_prepare",
        request_id: "Bad ID",
        query: "q",
      }),
      null,
    );
    assert.equal(
      encodeProductivityResearchPrepare({
        type: "productivity_research_prepare",
        request_id: "research-req-1",
        query: "q",
        proposal_id: "prop-1",
      }),
      null,
    );
    assert.equal(
      encodeProductivityResearchPrepare({
        type: "productivity_research_prepare",
        request_id: "research-req-1",
        query: "",
      }),
      null,
    );
  });

  it("encodes reminder prepare messages with exact protocol fields", () => {
    const originalDateNow = Date.now;
    Date.now = () => Date.UTC(2026, 6, 20, 0, 0, 0);
    try {
      const withOptional = encodeProductivityReminderPrepare({
        type: "productivity_reminder_prepare",
        request_id: "reminder-req-1",
        title: "Pick up package",
        remind_at: "2027-01-15T09:00:00-05:00",
        notes: "Bring ID",
        list_name: "Errands",
      });
      assert.ok(withOptional);
      assert.equal(withOptional.type, "productivity_reminder_prepare");
      assert.equal(withOptional.request_id, "reminder-req-1");
      assert.equal(withOptional.title, "Pick up package");
      assert.equal(withOptional.remind_at, "2027-01-15T09:00:00-05:00");
      assert.equal(withOptional.notes, "Bring ID");
      assert.equal(withOptional.list_name, "Errands");

      const minimal = encodeProductivityReminderPrepare({
        type: "productivity_reminder_prepare",
        request_id: "reminder-req-2",
        title: "Call back",
        remind_at: "2027-02-01T12:00:00Z",
      });
      assert.ok(minimal);
      assert.equal("notes" in minimal, false);
      assert.equal("list_name" in minimal, false);
    } finally {
      Date.now = originalDateNow;
    }
  });

  it("rejects malformed reminder prepare encodes and unknown fields", () => {
    const originalDateNow = Date.now;
    Date.now = () => Date.UTC(2026, 6, 20, 0, 0, 0);
    try {
      assert.equal(
        encodeProductivityReminderPrepare({
          type: "productivity_reminder_prepare",
          request_id: "Bad ID",
          title: "Title",
          remind_at: "2027-02-01T12:00:00Z",
        }),
        null,
      );
      assert.equal(
        encodeProductivityReminderPrepare({
          type: "productivity_reminder_prepare",
          request_id: "reminder-req-1",
          title: "Title",
          remind_at: "2027-02-01T12:00:00Z",
          proposal_id: "prop-1",
        }),
        null,
      );
      assert.equal(
        encodeProductivityReminderPrepare({
          type: "productivity_reminder_prepare",
          request_id: "reminder-req-1",
          title: "",
          remind_at: "2027-02-01T12:00:00Z",
        }),
        null,
      );
      assert.equal(
        encodeProductivityReminderPrepare({
          type: "productivity_reminder_prepare",
          request_id: "reminder-req-1",
          title: "Title",
          remind_at: "2030-02-01T09:00",
        }),
        null,
      );
    } finally {
      Date.now = originalDateNow;
    }
  });

  it("parses optional request_id on confirmation and error and rejects malformed ids", () => {
    const withId = parseProductivityServerMessage(
      confirmation({ request_id: "email-req-1" }),
    );
    assert.ok(withId);
    assert.equal(withId.type, "productivity_confirmation_required");
    if (withId.type === "productivity_confirmation_required") {
      assert.equal(withId.request_id, "email-req-1");
    }
    assert.equal(
      parseProductivityServerMessage(confirmation({ request_id: "Bad ID" })),
      null,
    );
    const errorWithId = parseProductivityServerMessage({
      type: "productivity_error",
      proposal_id: "proposal-1",
      code: "unavailable",
      request_id: "email-req-9",
    });
    assert.ok(errorWithId);
    if (errorWithId?.type === "productivity_error") {
      assert.equal(errorWithId.request_id, "email-req-9");
    }
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_error",
        proposal_id: "proposal-1",
        code: "unavailable",
        request_id: "Bad ID",
      }),
      null,
    );
    const withoutId = parseProductivityServerMessage({
      type: "productivity_error",
      proposal_id: "proposal-1",
      code: "unavailable",
    });
    assert.ok(withoutId);
    if (withoutId?.type === "productivity_error") {
      assert.equal(withoutId.request_id, undefined);
    }
  });

  it("parses JSON strings and returns null without throwing on bad JSON", () => {
    const parsed = parseProductivityServerMessage(
      JSON.stringify(confirmation()),
    );
    assert.ok(parsed);
    assert.equal(parsed.type, "productivity_confirmation_required");
    assert.equal(parseProductivityServerMessage("{not-json"), null);
    assert.equal(parseProductivityServerMessage(null), null);
    assert.equal(parseProductivityServerMessage([]), null);
  });

  it("parses research results and rejects unsafe or unknown fields", () => {
    const parsed = parseProductivityServerMessage({
      type: "productivity_research_result",
      proposal_id: "proposal-1",
      items: [
        {
          title: "Hello",
          url: "https://example.com/path",
          domain: "example.com",
          snippet: "A line\nwith detail",
        },
      ],
    });
    assert.ok(parsed);
    assert.equal(parsed.type, "productivity_research_result");
    if (parsed.type === "productivity_research_result") {
      assert.equal(parsed.items.length, 1);
      assert.equal(parsed.items[0]?.domain, "example.com");
      assert.throws(() => {
        (parsed.items as unknown as object[]).push({});
      }, TypeError);
    }
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_research_result",
        proposal_id: "proposal-1",
        items: [
          {
            title: "Hello",
            url: "http://example.com/path",
            domain: "example.com",
          },
        ],
      }),
      null,
    );
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_research_result",
        proposal_id: "proposal-1",
        query: "secret",
        items: [],
      }),
      null,
    );
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_research_result",
        proposal_id: "proposal-1",
        items: [
          {
            title: "Hello",
            url: "https://example.com/path",
            domain: "other.com",
          },
        ],
      }),
      null,
    );
    const oversized = Array.from({ length: 21 }, (_, index) => ({
      title: `Item ${index}`,
      url: `https://example.com/${index}`,
      domain: "example.com",
    }));
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_research_result",
        proposal_id: "proposal-1",
        items: oversized,
      }),
      null,
    );
  });

  it("parses calendar results and rejects malformed instants or unknown fields", () => {
    const parsed = parseProductivityServerMessage({
      type: "productivity_calendar_result",
      proposal_id: "proposal-1",
      events: [
        {
          title: "Meet",
          start: "2026-07-20T13:00:00Z",
          end: "2026-07-20T14:00:00+00:00",
          calendar: "Work",
          location: "Room 1",
        },
      ],
    });
    assert.ok(parsed);
    assert.equal(parsed.type, "productivity_calendar_result");
    if (parsed.type === "productivity_calendar_result") {
      assert.equal(parsed.events[0]?.calendar, "Work");
    }
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_calendar_result",
        proposal_id: "proposal-1",
        events: [
          {
            title: "Meet",
            start: "2026-07-20T13:00:00",
            end: "2026-07-20T14:00:00Z",
            calendar: "Work",
          },
        ],
      }),
      null,
    );
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_calendar_result",
        proposal_id: "proposal-1",
        session_id: "session",
        events: [],
      }),
      null,
    );
    const oversized = Array.from({ length: 101 }, (_, index) => ({
      title: `Event ${index}`,
      start: "2026-07-20T13:00:00Z",
      end: "2026-07-20T14:00:00Z",
      calendar: "Work",
    }));
    assert.equal(
      parseProductivityServerMessage({
        type: "productivity_calendar_result",
        proposal_id: "proposal-1",
        events: oversized,
      }),
      null,
    );
  });
});

function onceStateForPrivacy() {
  return createApprovalScopeStateFromAllowed(["once"])!;
}
