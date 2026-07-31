import { useEffect, useState } from 'react';
import { getBuildInfo } from '../lib/api';

/**
 * Backend build provenance (ADR-012).
 *
 * Deploys reported success for four months while the backend served a stale image,
 * because a mutable image tag made the container update a silent no-op. Surfacing the
 * SHA is what makes that visible, so this footer is a safety control, not decoration.
 *
 * Unlike the Streamlit footer there is no frontend/backend drift warning here, and there
 * should not be: this SPA is served *by* the backend from the same image, so the two
 * cannot diverge. That is the point of retiring the separate frontend container at 4e.
 */
export default function BuildFooter() {
  const [sha, setSha] = useState<string | null>(null);
  const [builtAt, setBuiltAt] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    getBuildInfo()
      .then(info => {
        const build = (info as { build?: { git_sha?: string; build_time?: string } })
          ?.build;
        setSha(build?.git_sha ?? 'unknown');
        setBuiltAt(build?.build_time ?? null);
      })
      .catch(() => setFailed(true));
  }, []);

  return (
    <footer className="border-t border-slate-200 bg-white">
      <div
        className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-1 px-4
                   py-3 text-xs text-slate-400"
      >
        {failed ? (
          <span className="text-amber-700">Backend unreachable.</span>
        ) : (
          <>
            <span>
              backend <code className="text-slate-500">{sha ?? '…'}</code>
            </span>
            {builtAt && <span>built {builtAt}</span>}
          </>
        )}
      </div>
    </footer>
  );
}
