import React from "react";
import type { Phase6RepoIntelUpdate } from "../../utils/phase6/phase6Protocol";

type Props = {
  repoIntel: Phase6RepoIntelUpdate | null;
  disabled?: boolean;
};

export const RepoIntelPanel: React.FC<Props> = ({ repoIntel, disabled = false }) => {
  if (!repoIntel) {
    return (
      <section
        aria-labelledby="heading-repo-intel"
        className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm ${
          disabled ? "opacity-60 pointer-events-none" : ""
        }`}
      >
        <h3
          id="heading-repo-intel"
          className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2"
        >
          Repository Intelligence
        </h3>
        <p className="text-sm text-slate-500 dark:text-slate-400 italic" role="status">
          No repository intelligence search executed.
        </p>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="heading-repo-intel"
      className={`p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm space-y-4 ${
        disabled ? "opacity-60 pointer-events-none" : ""
      }`}
    >
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <h3
            id="heading-repo-intel"
            className="text-lg font-semibold text-slate-900 dark:text-slate-100"
          >
            Repository Intelligence
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Scan State: <span className="font-semibold text-slate-700 dark:text-slate-300">{repoIntel.scan_state}</span> | Query: &quot;{repoIntel.query_summary}&quot;
          </p>
        </div>
        <span className="inline-flex items-center px-2.5 py-1 text-xs font-medium rounded-md bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200 self-start sm:self-auto">
          {repoIntel.hit_count} Hits Found
        </span>
      </div>

      {repoIntel.results.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400 italic">
          No hits matching bounded query parameters.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-800">
          <table className="w-full text-left text-xs text-slate-800 dark:text-slate-200">
            <caption className="sr-only">
              Bounded Repository Intelligence Search Hits
            </caption>
            <thead className="bg-slate-100 dark:bg-slate-950 text-slate-600 dark:text-slate-400 font-semibold border-b border-slate-200 dark:border-slate-800">
              <tr>
                <th scope="col" className="p-2.5">File Path &amp; Line</th>
                <th scope="col" className="p-2.5">Symbol</th>
                <th scope="col" className="p-2.5">Score</th>
                <th scope="col" className="p-2.5">Provenance</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800 bg-white dark:bg-slate-900">
              {repoIntel.results.map((hit, idx) => (
                <tr key={`${hit.path}-${hit.line}-${idx}`} className="hover:bg-slate-50 dark:hover:bg-slate-950/50">
                  <td className="p-2.5 font-mono text-slate-900 dark:text-slate-100">
                    {hit.path}:{hit.line}
                  </td>
                  <td className="p-2.5 font-mono text-slate-600 dark:text-slate-400">
                    {hit.symbol ?? "—"}
                  </td>
                  <td className="p-2.5 font-semibold text-slate-800 dark:text-slate-200">
                    {hit.score.toFixed(3)}
                  </td>
                  <td className="p-2.5 text-slate-500 dark:text-slate-400">
                    {hit.provenance}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};
