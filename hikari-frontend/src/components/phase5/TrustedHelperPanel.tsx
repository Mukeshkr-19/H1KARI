"use client";

import type { Phase5State } from "@/utils/phase5/phase5State";

export interface TrustedHelperPanelProps {
  readonly state: Phase5State;
  readonly helperActorId: string;
  readonly expiresAt: string;
  readonly onHelperActorIdChange: (value: string) => void;
  readonly onExpiresAtChange: (value: string) => void;
  readonly onCreate: () => void;
  readonly onList: () => void;
  readonly onRevoke: (grantId: string) => void;
  readonly isOwner: boolean;
  readonly disabled?: boolean;
}

export function TrustedHelperPanel({
  state,
  helperActorId,
  expiresAt,
  onHelperActorIdChange,
  onExpiresAtChange,
  onCreate,
  onList,
  onRevoke,
  isOwner,
  disabled = false,
}: TrustedHelperPanelProps) {
  return (
    <section aria-labelledby="phase5-helper-heading" className="space-y-3">
      <h3 id="phase5-helper-heading" className="text-base font-semibold text-white">
        Trusted Helper
      </h3>
      <p className="text-sm text-gray-400">
        Grants require expiration. There is no permanent grant option and no delegation control.
      </p>
      {!isOwner && (
        <p role="status" className="text-sm text-amber-200">
          Only the paired owner can create or revoke helper grants.
        </p>
      )}
      <div>
        <label htmlFor="phase5-helper-actor" className="block text-sm text-gray-300 mb-1">
          Helper actor ID
        </label>
        <input
          id="phase5-helper-actor"
          type="text"
          value={helperActorId}
          maxLength={128}
          onChange={(event) => onHelperActorIdChange(event.target.value)}
          disabled={!isOwner || disabled || state.submitLocked}
          className="w-full bg-[#1a1a2e] border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-blue-500 min-h-[44px]"
        />
      </div>
      <div>
        <label htmlFor="phase5-helper-expires" className="block text-sm text-gray-300 mb-1">
          Access ends at (Unix timestamp)
        </label>
        <input
          id="phase5-helper-expires"
          type="text"
          inputMode="numeric"
          value={expiresAt}
          onChange={(event) => onExpiresAtChange(event.target.value)}
          disabled={!isOwner || disabled || state.submitLocked}
          className="w-full bg-[#1a1a2e] border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-blue-500 min-h-[44px]"
        />
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onCreate}
          disabled={!isOwner || disabled || state.submitLocked}
          className="min-h-[44px] px-4 py-2 rounded-lg bg-blue-700 text-white disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-blue-400"
        >
          Create grant
        </button>
        <button
          type="button"
          onClick={onList}
          disabled={!isOwner || disabled || state.submitLocked}
          className="min-h-[44px] px-4 py-2 rounded-lg bg-gray-700 text-white disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-gray-400"
        >
          List grants
        </button>
      </div>
      <ul className="space-y-2" aria-label="Helper grants">
        {state.helperGrants.map((grant) => (
          <li key={grant.grant_id} className="border border-gray-700 rounded-lg p-3 text-sm text-gray-200">
            <p>
              Scope: {grant.capability}
              {grant.data_subject ? ` / ${grant.data_subject}` : ""}
            </p>
            <p>
              Access ends at:{" "}
              <time dateTime={new Date(grant.expires_at * 1000).toISOString()}>
                {new Date(grant.expires_at * 1000).toLocaleString()}
              </time>
            </p>
            <p>Status: {grant.revoked ? "revoked" : "active"}</p>
            {!grant.revoked && (
              <button
                type="button"
                onClick={() => onRevoke(grant.grant_id)}
                disabled={!isOwner || disabled || state.submitLocked}
                className="mt-2 min-h-[44px] px-3 py-2 rounded-lg bg-red-800 text-white disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-red-400"
              >
                Revoke grant
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
