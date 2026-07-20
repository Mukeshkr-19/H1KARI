import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { PREVIEW_LABEL_MAX, PREVIEW_VALUE_MAX } from "./actionPreview";
import {
  PREVIEW_ENTRY_MAX,
  createInitialProposalLifecycleState,
  freezeProposalSnapshot,
  isTerminalProposalLifecycleStatus,
  isValidProposalId,
  reduceProposalLifecycle,
  resolveLifecycleErrorCode,
  type ProposalLifecycleEntry,
  type ProposalLifecycleInput,
  type ProposalLifecycleState,
} from "./actionLifecycle";

function sampleProposal(
  overrides: Partial<ProposalLifecycleInput> = {},
): ProposalLifecycleInput {
  return {
    proposalId: "proposal-1",
    heading: "Send draft",
    actionLabel: "email.draft",
    riskLabel: "medium",
    targets: [{ label: "To", value: "user@example.com" }],
    payload: [{ label: "Subject", value: "Hello\nWorld" }],
    expirationLabel: "Expires soon",
    ...overrides,
  };
}

function previewState(
  proposal: ProposalLifecycleInput = sampleProposal(),
): ProposalLifecycleState {
  return reduceProposalLifecycle(createInitialProposalLifecycleState(), {
    type: "preview",
    proposal,
  });
}

function advance(
  start: ProposalLifecycleState,
  events: Parameters<typeof reduceProposalLifecycle>[1][],
): ProposalLifecycleState {
  return events.reduce(
    (state, event) => reduceProposalLifecycle(state, event),
    start,
  );
}

describe("actionLifecycle", () => {
  it("starts idle without a proposal correlation", () => {
    const state = createInitialProposalLifecycleState();
    assert.deepEqual(state, {
      status: "idle",
      proposalId: null,
      proposal: null,
      error: null,
    });
  });

  it("freezes a copied snapshot on preview with nested immutability", () => {
    const targets = [{ label: "To", value: "a@example.com" }];
    const payload = [{ label: "Body", value: "text" }];
    const input = sampleProposal({
      proposalId: "proposal-freeze",
      targets,
      payload,
    });
    const state = previewState(input);
    assert.equal(state.status, "preview");
    assert.equal(state.proposalId, "proposal-freeze");
    assert.ok(state.proposal);
    assert.notEqual(state.proposal.targets, targets);
    assert.notEqual(state.proposal.payload, payload);
    targets[0].value = "mutated";
    payload[0].value = "mutated";
    assert.equal(state.proposal.targets[0].value, "a@example.com");
    assert.equal(state.proposal.payload[0].value, "text");
    assert.throws(() => {
      (state.proposal as { heading: string }).heading = "nope";
    }, TypeError);
    assert.throws(() => {
      (state.proposal.targets as ProposalLifecycleEntry[]).push({
        label: "x",
        value: "y",
      });
    }, TypeError);
    assert.throws(() => {
      (state.proposal.targets[0] as { value: string }).value = "changed";
    }, TypeError);
    assert.throws(() => {
      (state.proposal.payload[0] as { label: string }).label = "changed";
    }, TypeError);
  });

  it("rejects shared-prefix overlong proposal ids without truncation collisions", () => {
    const prefix = "a".repeat(80);
    const overlong = `${prefix}x`;
    assert.equal(isValidProposalId(prefix), true);
    assert.equal(isValidProposalId(overlong), false);
    assert.equal(overlong.startsWith(prefix), true);

    const accepted = previewState(sampleProposal({ proposalId: prefix }));
    assert.equal(accepted.status, "preview");
    assert.equal(accepted.proposalId, prefix);

    const idle = createInitialProposalLifecycleState();
    const rejected = reduceProposalLifecycle(idle, {
      type: "preview",
      proposal: sampleProposal({ proposalId: overlong }),
    });
    assert.equal(rejected, idle);

    assert.equal(
      reduceProposalLifecycle(accepted, {
        type: "confirm",
        proposalId: overlong,
      }),
      accepted,
    );
    assert.equal(accepted.proposalId === overlong, false);
  });

  it("rejects Unicode and control characters in proposal ids", () => {
    const idle = createInitialProposalLifecycleState();
    for (const proposalId of [
      "bad id",
      "BadId",
      "id\u0000",
      "id\u200E",
      "id\u202E",
      "café",
      "",
      " proposal-1",
      "proposal-1 ",
    ]) {
      assert.equal(isValidProposalId(proposalId), false);
      const next = reduceProposalLifecycle(idle, {
        type: "preview",
        proposal: sampleProposal({ proposalId }),
      });
      assert.equal(next, idle);
    }
  });

  it("rejects malformed preview objects and non-array targets or payload", () => {
    const idle = createInitialProposalLifecycleState();
    assert.equal(freezeProposalSnapshot(null), null);
    assert.equal(freezeProposalSnapshot("proposal"), null);
    assert.equal(freezeProposalSnapshot([]), null);

    assert.equal(
      reduceProposalLifecycle(idle, {
        type: "preview",
        proposal: sampleProposal({
          targets: { label: "To", value: "x" } as unknown as [],
        }),
      }),
      idle,
    );
    assert.equal(
      reduceProposalLifecycle(idle, {
        type: "preview",
        proposal: sampleProposal({
          payload: "body" as unknown as [],
        }),
      }),
      idle,
    );
    assert.equal(
      freezeProposalSnapshot({
        ...sampleProposal(),
        targets: [{ label: 1, value: "x" }],
      }),
      null,
    );
    assert.equal(
      freezeProposalSnapshot({
        ...sampleProposal(),
        payload: [null],
      }),
      null,
    );
  });

  it("rejects proposals that exceed destination or payload entry maxima", () => {
    const idle = createInitialProposalLifecycleState();
    const tooManyTargets = Array.from({ length: PREVIEW_ENTRY_MAX + 1 }, (_, i) => ({
      label: `t${i}`,
      value: `v${i}`,
    }));
    const tooManyPayload = Array.from({ length: PREVIEW_ENTRY_MAX + 1 }, (_, i) => ({
      label: `p${i}`,
      value: `v${i}`,
    }));
    assert.equal(
      reduceProposalLifecycle(idle, {
        type: "preview",
        proposal: sampleProposal({ targets: tooManyTargets }),
      }),
      idle,
    );
    assert.equal(
      reduceProposalLifecycle(idle, {
        type: "preview",
        proposal: sampleProposal({ payload: tooManyPayload }),
      }),
      idle,
    );
    const exact = Array.from({ length: PREVIEW_ENTRY_MAX }, (_, i) => ({
      label: `ok${i}`,
      value: `val${i}`,
    }));
    const accepted = previewState(
      sampleProposal({ targets: exact, payload: exact }),
    );
    assert.equal(accepted.status, "preview");
    assert.equal(accepted.proposal?.targets.length, PREVIEW_ENTRY_MAX);
    assert.equal(accepted.proposal?.payload.length, PREVIEW_ENTRY_MAX);
  });

  it("bounds and sanitizes snapshot text through preview helpers", () => {
    const longHeading = `H\u0000${"h".repeat(PREVIEW_LABEL_MAX + 5)}`;
    const longValue = `V\u202E${"v".repeat(PREVIEW_VALUE_MAX + 3)}`;
    const state = previewState(
      sampleProposal({
        heading: longHeading,
        actionLabel: `A\u200E${"a".repeat(PREVIEW_LABEL_MAX + 1)}`,
        riskLabel: `R\t${"r".repeat(PREVIEW_LABEL_MAX + 2)}`,
        expirationLabel: `E${"e".repeat(PREVIEW_LABEL_MAX + 4)}`,
        targets: [{ label: `L${"l".repeat(PREVIEW_LABEL_MAX)}`, value: longValue }],
        payload: [
          {
            label: "Body",
            value: "keep\n\ttabs",
            truncated: false,
          },
        ],
      }),
    );
    assert.equal(state.status, "preview");
    assert.ok(state.proposal);
    assert.equal(state.proposal.heading.length, PREVIEW_LABEL_MAX + 1);
    assert.equal(state.proposal.heading.endsWith("…"), true);
    assert.equal(state.proposal.heading.includes("\u0000"), false);
    assert.equal(state.proposal.actionLabel.endsWith("…"), true);
    assert.equal(state.proposal.riskLabel.endsWith("…"), true);
    assert.equal(state.proposal.expirationLabel?.endsWith("…"), true);
    assert.equal(state.proposal.targets[0].truncated, true);
    assert.equal(state.proposal.targets[0].value.endsWith("…"), true);
    assert.equal(state.proposal.targets[0].value.includes("\u202E"), false);
    assert.equal(state.proposal.payload[0].value, "keep\n\ttabs");
    assert.equal(state.proposal.payload[0].truncated, undefined);
  });

  it("covers every valid happy-path transition", () => {
    const id = "proposal-happy";
    const completed = advance(createInitialProposalLifecycleState(), [
      { type: "preview", proposal: sampleProposal({ proposalId: id }) },
      { type: "confirm", proposalId: id },
      { type: "approve", proposalId: id },
      { type: "execute", proposalId: id },
      { type: "complete", proposalId: id },
    ]);
    assert.equal(completed.status, "completed");
    assert.equal(completed.proposalId, id);
    assert.equal(completed.error, null);
    assert.equal(isTerminalProposalLifecycleStatus(completed.status), true);
  });

  it("covers every valid cancel transition", () => {
    const id = "proposal-cancel";
    for (const from of ["preview", "confirming", "approved", "executing"] as const) {
      let state = previewState(sampleProposal({ proposalId: id }));
      if (from === "confirming" || from === "approved" || from === "executing") {
        state = reduceProposalLifecycle(state, { type: "confirm", proposalId: id });
      }
      if (from === "approved" || from === "executing") {
        state = reduceProposalLifecycle(state, { type: "approve", proposalId: id });
      }
      if (from === "executing") {
        state = reduceProposalLifecycle(state, { type: "execute", proposalId: id });
      }
      assert.equal(state.status, from);
      const cancelling = reduceProposalLifecycle(state, {
        type: "cancel",
        proposalId: id,
      });
      assert.equal(cancelling.status, "cancelling");
      const cancelled = reduceProposalLifecycle(cancelling, {
        type: "cancelled",
        proposalId: id,
      });
      assert.equal(cancelled.status, "cancelled");
      assert.equal(cancelled.proposalId, id);
    }
  });

  it("covers valid fail transitions with safe error codes only", () => {
    const id = "proposal-fail";
    const stages: ProposalLifecycleState[] = [];
    let state = previewState(sampleProposal({ proposalId: id }));
    stages.push(state);
    state = reduceProposalLifecycle(state, { type: "confirm", proposalId: id });
    stages.push(state);
    state = reduceProposalLifecycle(state, { type: "approve", proposalId: id });
    stages.push(state);
    state = reduceProposalLifecycle(state, { type: "execute", proposalId: id });
    stages.push(state);
    state = reduceProposalLifecycle(state, { type: "cancel", proposalId: id });
    stages.push(state);

    for (const current of stages) {
      const failed = reduceProposalLifecycle(current, {
        type: "fail",
        proposalId: id,
        error: "confirm_failed",
      });
      assert.equal(failed.status, "failed");
      assert.equal(failed.error, "confirm_failed");
    }
  });

  it("confirmation is only accepted from preview", () => {
    const id = "proposal-confirm-only";
    const preview = previewState(sampleProposal({ proposalId: id }));
    const confirming = reduceProposalLifecycle(preview, {
      type: "confirm",
      proposalId: id,
    });
    assert.equal(confirming.status, "confirming");

    for (const status of [
      "confirming",
      "approved",
      "executing",
      "completed",
      "failed",
      "cancelling",
      "cancelled",
    ] as const) {
      let state: ProposalLifecycleState = confirming;
      if (status === "approved" || status === "executing" || status === "completed") {
        state = reduceProposalLifecycle(state, { type: "approve", proposalId: id });
      }
      if (status === "executing" || status === "completed") {
        state = reduceProposalLifecycle(state, { type: "execute", proposalId: id });
      }
      if (status === "completed") {
        state = reduceProposalLifecycle(state, { type: "complete", proposalId: id });
      }
      if (status === "failed") {
        state = reduceProposalLifecycle(confirming, {
          type: "fail",
          proposalId: id,
          error: "unavailable",
        });
      }
      if (status === "cancelling" || status === "cancelled") {
        state = reduceProposalLifecycle(confirming, {
          type: "cancel",
          proposalId: id,
        });
        if (status === "cancelled") {
          state = reduceProposalLifecycle(state, {
            type: "cancelled",
            proposalId: id,
          });
        }
      }
      assert.equal(state.status, status);
      const after = reduceProposalLifecycle(state, {
        type: "confirm",
        proposalId: id,
      });
      assert.equal(after, state);
    }
  });

  it("ignores duplicate confirm while confirming approved or executing", () => {
    const id = "proposal-dup-confirm";
    const confirming = advance(createInitialProposalLifecycleState(), [
      { type: "preview", proposal: sampleProposal({ proposalId: id }) },
      { type: "confirm", proposalId: id },
    ]);
    assert.equal(
      reduceProposalLifecycle(confirming, { type: "confirm", proposalId: id }),
      confirming,
    );
    const approved = reduceProposalLifecycle(confirming, {
      type: "approve",
      proposalId: id,
    });
    assert.equal(
      reduceProposalLifecycle(approved, { type: "confirm", proposalId: id }),
      approved,
    );
    const executing = reduceProposalLifecycle(approved, {
      type: "execute",
      proposalId: id,
    });
    assert.equal(
      reduceProposalLifecycle(executing, { type: "confirm", proposalId: id }),
      executing,
    );
  });

  it("ignores duplicate cancel and cancel for inactive proposals", () => {
    const id = "proposal-dup-cancel";
    const cancelling = advance(createInitialProposalLifecycleState(), [
      { type: "preview", proposal: sampleProposal({ proposalId: id }) },
      { type: "cancel", proposalId: id },
    ]);
    assert.equal(
      reduceProposalLifecycle(cancelling, { type: "cancel", proposalId: id }),
      cancelling,
    );
    const cancelled = reduceProposalLifecycle(cancelling, {
      type: "cancelled",
      proposalId: id,
    });
    assert.equal(
      reduceProposalLifecycle(cancelled, { type: "cancel", proposalId: id }),
      cancelled,
    );
    const idle = createInitialProposalLifecycleState();
    assert.equal(
      reduceProposalLifecycle(idle, { type: "cancel", proposalId: id }),
      idle,
    );
  });

  it("ignores stale or mismatched proposal ids", () => {
    const id = "proposal-live";
    const state = advance(createInitialProposalLifecycleState(), [
      { type: "preview", proposal: sampleProposal({ proposalId: id }) },
      { type: "confirm", proposalId: id },
    ]);
    const stale = "proposal-stale";
    assert.equal(
      reduceProposalLifecycle(state, { type: "approve", proposalId: stale }),
      state,
    );
    assert.equal(
      reduceProposalLifecycle(state, { type: "execute", proposalId: stale }),
      state,
    );
    assert.equal(
      reduceProposalLifecycle(state, { type: "complete", proposalId: stale }),
      state,
    );
    assert.equal(
      reduceProposalLifecycle(state, {
        type: "fail",
        proposalId: stale,
        error: "unavailable",
      }),
      state,
    );
    assert.equal(
      reduceProposalLifecycle(state, { type: "cancel", proposalId: stale }),
      state,
    );
    assert.equal(
      reduceProposalLifecycle(state, { type: "cancelled", proposalId: stale }),
      state,
    );
    assert.equal(
      reduceProposalLifecycle(state, { type: "confirm", proposalId: stale }),
      state,
    );
  });

  it("new preview may replace only idle or terminal states", () => {
    const first = previewState(sampleProposal({ proposalId: "one" }));
    assert.equal(
      reduceProposalLifecycle(first, {
        type: "preview",
        proposal: sampleProposal({ proposalId: "two" }),
      }),
      first,
    );

    for (const terminal of ["completed", "failed", "cancelled"] as const) {
      let state = previewState(sampleProposal({ proposalId: "old" }));
      state = reduceProposalLifecycle(state, {
        type: "confirm",
        proposalId: "old",
      });
      if (terminal === "completed") {
        state = advance(state, [
          { type: "approve", proposalId: "old" },
          { type: "execute", proposalId: "old" },
          { type: "complete", proposalId: "old" },
        ]);
      } else if (terminal === "failed") {
        state = reduceProposalLifecycle(state, {
          type: "fail",
          proposalId: "old",
          error: "proposal_expired",
        });
      } else {
        state = advance(state, [
          { type: "cancel", proposalId: "old" },
          { type: "cancelled", proposalId: "old" },
        ]);
      }
      assert.equal(state.status, terminal);
      const replaced = reduceProposalLifecycle(state, {
        type: "preview",
        proposal: sampleProposal({ proposalId: "fresh" }),
      });
      assert.equal(replaced.status, "preview");
      assert.equal(replaced.proposalId, "fresh");
    }
  });

  it("terminal states cannot return to executing", () => {
    const id = "proposal-terminal";
    const terminals = [
      advance(createInitialProposalLifecycleState(), [
        { type: "preview", proposal: sampleProposal({ proposalId: id }) },
        { type: "confirm", proposalId: id },
        { type: "approve", proposalId: id },
        { type: "execute", proposalId: id },
        { type: "complete", proposalId: id },
      ]),
      advance(createInitialProposalLifecycleState(), [
        { type: "preview", proposal: sampleProposal({ proposalId: id }) },
        { type: "fail", proposalId: id, error: "unavailable" },
      ]),
      advance(createInitialProposalLifecycleState(), [
        { type: "preview", proposal: sampleProposal({ proposalId: id }) },
        { type: "cancel", proposalId: id },
        { type: "cancelled", proposalId: id },
      ]),
    ];
    for (const terminal of terminals) {
      assert.equal(isTerminalProposalLifecycleStatus(terminal.status), true);
      assert.equal(
        reduceProposalLifecycle(terminal, { type: "execute", proposalId: id }),
        terminal,
      );
      assert.equal(
        reduceProposalLifecycle(terminal, { type: "approve", proposalId: id }),
        terminal,
      );
      assert.notEqual(terminal.status, "executing");
    }
  });

  it("stores only safe error codes and never raw messages", () => {
    const id = "proposal-errors";
    const preview = previewState(sampleProposal({ proposalId: id }));
    const unknown = reduceProposalLifecycle(preview, {
      type: "fail",
      proposalId: id,
      error: "Error: boom at https://evil.example?token=abc",
    });
    assert.equal(unknown.status, "failed");
    assert.equal(unknown.error, "unavailable");
    assert.equal(
      JSON.stringify(unknown).includes("https://evil.example"),
      false,
    );
    assert.equal(resolveLifecycleErrorCode("proposal_invalid"), "proposal_invalid");
    assert.equal(resolveLifecycleErrorCode({ message: "nope" }), "unavailable");
    assert.equal(resolveLifecycleErrorCode(undefined), "unavailable");
  });

  it("ignores invalid transitions outside the lifecycle graph", () => {
    const idle = createInitialProposalLifecycleState();
    assert.equal(
      reduceProposalLifecycle(idle, { type: "confirm", proposalId: "x" }),
      idle,
    );
    assert.equal(
      reduceProposalLifecycle(idle, { type: "approve", proposalId: "x" }),
      idle,
    );
    assert.equal(
      reduceProposalLifecycle(idle, { type: "execute", proposalId: "x" }),
      idle,
    );
    assert.equal(
      reduceProposalLifecycle(idle, { type: "complete", proposalId: "x" }),
      idle,
    );
    assert.equal(
      reduceProposalLifecycle(idle, { type: "cancelled", proposalId: "x" }),
      idle,
    );

    const preview = previewState(sampleProposal({ proposalId: "graph" }));
    assert.equal(
      reduceProposalLifecycle(preview, {
        type: "approve",
        proposalId: "graph",
      }),
      preview,
    );
    assert.equal(
      reduceProposalLifecycle(preview, {
        type: "execute",
        proposalId: "graph",
      }),
      preview,
    );
    assert.equal(
      reduceProposalLifecycle(preview, {
        type: "complete",
        proposalId: "graph",
      }),
      preview,
    );
    assert.equal(
      reduceProposalLifecycle(preview, {
        type: "cancelled",
        proposalId: "graph",
      }),
      preview,
    );

    const frozen = freezeProposalSnapshot(sampleProposal());
    assert.ok(frozen);
    assert.equal(frozen.proposalId, "proposal-1");
  });
});
