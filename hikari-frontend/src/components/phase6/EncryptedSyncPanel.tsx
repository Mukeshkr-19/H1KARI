import React from "react";
import type { Phase6EncryptedSyncUpdate } from "../../utils/phase6/phase6Protocol";

type Props = {
  encryptedSync: Phase6EncryptedSyncUpdate | null;
  disabled?: boolean;
};

export const EncryptedSyncPanel: React.FC<Props> = ({ encryptedSync, disabled = false }) => {
  if (!encryptedSync) {
    return (
      <section
        aria-labelledby="heading-encrypted-sync"
        className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm ${
          disabled ? "opacity-60 pointer-events-none" : ""
        }`}
      >
        <h3
          id="heading-encrypted-sync"
          className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2"
        >
          User-Controlled Encrypted Sync
        </h3>
        <p className="text-sm text-slate-500 dark:text-slate-400 italic" role="status">
          No encrypted sync telemetry reported.
        </p>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="heading-encrypted-sync"
      className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm space-y-3 ${
        disabled ? "opacity-60 pointer-events-none" : ""
      }`}
    >
      <h3
        id="heading-encrypted-sync"
        className="text-lg font-semibold text-slate-900 dark:text-slate-100"
      >
        User-Controlled Encrypted Sync
      </h3>

      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
        <div className="p-2.5 rounded bg-slate-50 dark:bg-slate-950 border border-slate-100 dark:border-slate-800">
          <dt className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Enabled / Configured
          </dt>
          <dd className="mt-1 font-semibold text-slate-900 dark:text-slate-100">
            {encryptedSync.enabled ? "Enabled" : "Disabled"} | {encryptedSync.configured ? "Configured" : "Not Configured"}
          </dd>
        </div>

        <div className="p-2.5 rounded bg-slate-50 dark:bg-slate-950 border border-slate-100 dark:border-slate-800">
          <dt className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Sync Status
          </dt>
          <dd className="mt-1 font-semibold text-slate-900 dark:text-slate-100">
            {encryptedSync.status}
          </dd>
        </div>

        <div className="p-2.5 rounded bg-slate-50 dark:bg-slate-950 border border-slate-100 dark:border-slate-800 sm:col-span-2">
          <dt className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Conflict Count
          </dt>
          <dd className="mt-1 font-semibold text-slate-900 dark:text-slate-100">
            {encryptedSync.conflict_count} detected conflicts
          </dd>
        </div>
      </dl>

      <div className="p-3 rounded bg-slate-100 dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-300">
        <span className="font-semibold">[Privacy Guarantee] </span>
        Ciphertext manifest descriptors only. Plaintext content, private filenames, and object contents are never exposed to UI or network layers.
      </div>
    </section>
  );
};
