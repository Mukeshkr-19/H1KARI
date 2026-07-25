"use client";

import { useEffect, useRef } from "react";
import type { Phase5State } from "@/utils/phase5/phase5State";
import { CarePanel } from "./CarePanel";
import { ChildModePanel } from "./ChildModePanel";
import { GuideHandsPanel } from "./GuideHandsPanel";
import { TeachMePanel } from "./TeachMePanel";
import { TrustedHelperPanel } from "./TrustedHelperPanel";

export interface Phase5PanelProps {
  readonly state: Phase5State;
  readonly isOwner: boolean;
  readonly teachTopic: string;
  readonly guideGoal: string;
  readonly guideConsequential: boolean;
  readonly carePrompt: string;
  readonly childActorId: string;
  readonly helperActorId: string;
  readonly helperExpiresAt: string;
  readonly onTeachTopicChange: (value: string) => void;
  readonly onGuideGoalChange: (value: string) => void;
  readonly onGuideConsequentialChange: (value: boolean) => void;
  readonly onCarePromptChange: (value: string) => void;
  readonly onChildActorIdChange: (value: string) => void;
  readonly onHelperActorIdChange: (value: string) => void;
  readonly onHelperExpiresAtChange: (value: string) => void;
  readonly onActivateOwnerSession: () => void;
  readonly onActivateChildSession: () => void;
  readonly onCloseSession: () => void;
  readonly onPrepareTeach: () => void;
  readonly onPrepareGuide: () => void;
  readonly onPrepareCare: () => void;
  readonly onConfirmApproval: () => void;
  readonly onCreateHelperGrant: () => void;
  readonly onListHelperGrants: () => void;
  readonly onRevokeHelperGrant: (grantId: string) => void;
}

export function Phase5Panel(props: Phase5PanelProps) {
  const statusRef = useRef<HTMLParagraphElement | null>(null);
  const previousStatus = useRef(props.state.status);

  useEffect(() => {
    if (previousStatus.current !== props.state.status) {
      statusRef.current?.focus();
      previousStatus.current = props.state.status;
    }
  }, [props.state.status]);

  useEffect(() => {
    return () => {
      // Component disposal: parent should also clear sensitive state.
    };
  }, []);

  const statusLabel = props.state.status.split("_").join(" ");
  const statusTone =
    props.state.status === "active" || props.state.status === "proposal_ready"
      ? "text-emerald-300"
      : props.state.status === "denied" || props.state.status === "error" || props.state.status === "revoked"
        ? "text-red-300"
        : "text-amber-200";

  return (
    <section
      aria-labelledby="phase5-panel-heading"
      className="space-y-6 border border-gray-800 rounded-xl p-4 bg-[#12121a]"
    >
      <header className="space-y-2">
        <h2 id="phase5-panel-heading" className="text-lg font-bold text-white">
          Phase 5 Access
        </h2>
        <p
          ref={statusRef}
          tabIndex={-1}
          role="status"
          aria-live="polite"
          className={`text-sm ${statusTone}`}
        >
          <span className="sr-only">Phase 5 session state: </span>
          {statusLabel}
          {props.state.expiresAt ? (
            <>
              {" · "}
              <span className="sr-only">Access ends at </span>
              expires {new Date(props.state.expiresAt * 1000).toLocaleString()}
            </>
          ) : null}
          {props.state.approvalRequired ? " · approval required" : null}
        </p>
        {props.state.errorMessage && (
          <p role="alert" aria-live="assertive" className="text-sm text-red-300">
            {props.state.errorMessage}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={props.onActivateOwnerSession}
            disabled={!props.isOwner || props.state.submitLocked}
            className="min-h-[44px] px-4 py-2 rounded-lg bg-indigo-700 text-white disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            Activate owner session
          </button>
          <button
            type="button"
            onClick={props.onCloseSession}
            disabled={!props.isOwner || !props.state.sessionId || props.state.submitLocked}
            className="min-h-[44px] px-4 py-2 rounded-lg bg-gray-700 text-white disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-gray-400"
          >
            Close session
          </button>
        </div>
      </header>

      <TeachMePanel
        state={props.state}
        topic={props.teachTopic}
        onTopicChange={props.onTeachTopicChange}
        onPrepare={props.onPrepareTeach}
        disabled={!props.isOwner}
      />
      <GuideHandsPanel
        state={props.state}
        goal={props.guideGoal}
        consequential={props.guideConsequential}
        onGoalChange={props.onGuideGoalChange}
        onConsequentialChange={props.onGuideConsequentialChange}
        onPrepare={props.onPrepareGuide}
        onConfirm={props.onConfirmApproval}
        disabled={!props.isOwner}
      />
      <CarePanel
        state={props.state}
        prompt={props.carePrompt}
        onPromptChange={props.onCarePromptChange}
        onPrepare={props.onPrepareCare}
        onConfirm={props.onConfirmApproval}
        disabled={!props.isOwner}
      />
      <ChildModePanel
        state={props.state}
        childActorId={props.childActorId}
        onChildActorIdChange={props.onChildActorIdChange}
        onActivate={props.onActivateChildSession}
        disabled={!props.isOwner}
      />
      <TrustedHelperPanel
        state={props.state}
        helperActorId={props.helperActorId}
        expiresAt={props.helperExpiresAt}
        onHelperActorIdChange={props.onHelperActorIdChange}
        onExpiresAtChange={props.onHelperExpiresAtChange}
        onCreate={props.onCreateHelperGrant}
        onList={props.onListHelperGrants}
        onRevoke={props.onRevokeHelperGrant}
        isOwner={props.isOwner}
        disabled={!props.isOwner}
      />
    </section>
  );
}
