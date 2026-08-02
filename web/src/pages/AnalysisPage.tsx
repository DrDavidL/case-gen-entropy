/**
 * Diagnostic framework and likelihood ratios (ADR-001, ADR-007).
 *
 * **Read-only, deliberately.** No endpoint accepts edited tiers or LRs for a *saved*
 * case — `persist_case_version` is the only writer, and it is reachable only from paths
 * that generate the analysis or carry it forward. Streamlit is not ahead here: its tier
 * and LR editors operate on the Redis session during generation, so once a case is saved
 * neither UI can change these values. Editing is Phase 5, and it needs a backend endpoint
 * that writes a new version and records `author_overridden` provenance.
 *
 * What this screen does provide is the transparency ADR-007 asks for: the numbers, where
 * they came from, and which bucket each one moves — data that until ADR-001 was generated
 * on every case and then thrown away.
 */

import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';

import { getAnalysis, type CaseAnalysis } from '../lib/api';

/** Shared bands so the same LR reads the same everywhere on the page. */
function strength(lr: number): { label: string; chip: string } {
  const v = Math.abs(lr) >= 1 ? lr : 1 / (lr || 1);
  if (v >= 10) return { label: 'strong', chip: 'chip-good' };
  if (v >= 5) return { label: 'moderate', chip: 'chip-neutral' };
  if (v >= 2) return { label: 'small', chip: 'chip-neutral' };
  return { label: 'minimal', chip: 'chip-warn' };
}

export default function AnalysisPage() {
  const { caseId } = useParams();
  const id = Number(caseId);

  const [data, setData] = useState<CaseAnalysis | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [tier, setTier] = useState<number | 'all'>('all');

  useEffect(() => {
    if (!Number.isFinite(id)) return;
    let cancelled = false;
    getAnalysis(id)
      .then((d) => !cancelled && setData(d))
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (!Number.isFinite(id)) return <p className="notice notice-error">Bad case id.</p>;
  if (loading) return <p className="text-sm text-ink-500">Loading analysis…</p>;
  if (error) return <p className="notice notice-error">{error}</p>;

  // No local shape assertions: `CaseAnalysisResponse` types these element-wise, so a
  // renamed field server-side breaks this build instead of rendering blank cells.
  const tiers = data?.diagnostic_framework ?? [];
  const lrs = data?.feature_likelihood_ratios ?? [];

  if (!data || (tiers.length === 0 && lrs.length === 0)) {
    return (
      <div className="space-y-4">
        <Back id={id} />
        <div className="notice notice-warn">
          This case has no framework or likelihood-ratio data. Cases adopted from the
          simulator have none — it was never stored for them, and it cannot be
          reconstructed from the markdown (ADR-019). Cases generated since then do.
        </div>
      </div>
    );
  }

  const shown = tier === 'all' ? lrs : lrs.filter((l) => l.tier_level === tier);
  const byCategory = shown.reduce<Record<string, typeof lrs>>((acc, l) => {
    (acc[l.feature_category] ??= []).push(l);
    return acc;
  }, {});

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Back id={id} />
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="chip chip-neutral">version {data.version}</span>
          <span className="chip chip-neutral">{tiers.length} tiers</span>
          <span className="chip chip-neutral">{lrs.length} likelihood ratios</span>
        </div>
      </div>

      <div className="notice notice-warn text-xs">
        Read-only. No endpoint accepts edited tiers or likelihood ratios for a saved case
        yet — Streamlit cannot change them after saving either, because its editors work
        on the generation session. Editing with recorded provenance is Phase 5 (ADR-007).
      </div>

      <section className="card p-4">
        <h2 className="mb-3 text-sm font-semibold text-ink-800">Diagnostic framework</h2>
        <div className="space-y-4">
          {tiers.map((t) => {
            const priors = t.a_priori_probabilities ?? {};
            const total = Object.values(priors).reduce((a, b) => a + b, 0);
            return (
              <div key={t.tier_level} className="rounded border border-ink-200 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="text-sm font-medium">Tier {t.tier_level}</h3>
                  {/* Distributions must sum to 1.0. Validation happens at export time,
                      not generation, so a drifted tier is worth seeing here. */}
                  <span
                    className={`chip ${Math.abs(total - 1) < 0.01 ? 'chip-good' : 'chip-warn'}`}
                  >
                    priors sum {total.toFixed(3)}
                  </span>
                </div>
                <ul className="space-y-1">
                  {(t.buckets ?? []).map((b) => {
                    const p = priors[b.name];
                    return (
                      <li key={b.name} className="text-xs">
                        <div className="flex items-center gap-2">
                          <span className="w-44 shrink-0 font-medium text-ink-800">
                            {b.name}
                          </span>
                          <div className="h-2 flex-1 rounded bg-ink-100">
                            <div
                              className="h-2 rounded bg-brand-500"
                              style={{ width: `${Math.round((p ?? 0) * 100)}%` }}
                            />
                          </div>
                          <span className="w-12 text-right tabular-nums text-ink-500">
                            {p === undefined ? '—' : p.toFixed(3)}
                          </span>
                        </div>
                        <p className="ml-0 text-ink-500">{b.description}</p>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </div>
      </section>

      <section className="card p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-ink-800">Likelihood ratios</h2>
          <div className="flex gap-1">
            {(['all', 1, 2, 3] as const).map((t) => (
              <button
                key={String(t)}
                onClick={() => setTier(t)}
                className={`btn btn-sm ${tier === t ? 'btn-primary' : 'btn-secondary'}`}
              >
                {t === 'all' ? 'All tiers' : `Tier ${t}`}
              </button>
            ))}
          </div>
        </div>

        {shown.length === 0 && (
          <p className="text-sm text-ink-500">No likelihood ratios at this tier.</p>
        )}

        {Object.entries(byCategory).map(([cat, rows]) => (
          <div key={cat} className="mb-4">
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-500">
              {cat.replace(/_/g, ' ')}
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-ink-500">
                    <th className="py-1 pr-2 font-medium">Feature</th>
                    <th className="py-1 pr-2 font-medium">Moves bucket</th>
                    <th className="py-1 pr-2 text-right font-medium">LR</th>
                    <th className="py-1 pr-2 font-medium">Strength</th>
                    <th className="py-1 font-medium">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {rows
                    .slice()
                    .sort((a, b) => b.likelihood_ratio - a.likelihood_ratio)
                    .map((l, i) => {
                      const s = strength(l.likelihood_ratio);
                      return (
                        <tr key={i} className="border-t border-ink-100 align-top">
                          <td className="py-1 pr-2">{l.feature_name}</td>
                          <td className="py-1 pr-2 text-ink-600">{l.diagnostic_bucket}</td>
                          <td className="py-1 pr-2 text-right tabular-nums font-medium">
                            {l.likelihood_ratio}
                          </td>
                          <td className="py-1 pr-2">
                            <span className={`chip ${s.chip}`}>{s.label}</span>
                          </td>
                          <td className="py-1 text-ink-500">
                            {(l.provenance ?? 'llm_generated').replace(/_/g, ' ')}
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}

function Back({ id }: { id: number }) {
  return (
    <Link to={`/cases/${id}`} className="text-sm text-ink-500 hover:text-ink-800">
      ← Back to case
    </Link>
  );
}
