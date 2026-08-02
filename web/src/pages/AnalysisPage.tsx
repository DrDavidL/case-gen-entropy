/**
 * Diagnostic framework and likelihood ratios (ADR-001, ADR-007).
 *
 * Values and priors are editable **in place** — no new case version. LRs are authoring
 * analysis, not learner-facing content: the learner reads `case_details.content` and the
 * Oracle rates blinded structured fields, so versioning each tweak would add lineage
 * noise without protecting a learner run. Rows a human actually changed are stamped
 * `author_overridden` server-side, which is what keeps the edit visible afterwards.
 *
 * Bucket names and tier structure are not editable. Renaming a bucket orphans every LR
 * pointing at the old name; `/regenerate-lrs` is the supported repair for that.
 */

import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';

import { getAnalysis, updateAnalysis, type CaseAnalysis } from '../lib/api';

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
  // Edits held as strings so a half-typed "1." does not become NaN mid-keystroke.
  const [lrEdits, setLrEdits] = useState<Record<number, string>>({});
  const [priorEdits, setPriorEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState('');

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

  const dirty = Object.keys(lrEdits).length > 0 || Object.keys(priorEdits).length > 0;

  const save = async () => {
    setSaving(true);
    setSaved('');
    try {
      const priorsByTier: Record<number, Record<string, number>> = {};
      for (const [key, raw] of Object.entries(priorEdits)) {
        const [t, ...rest] = key.split('|');
        const bucket = rest.join('|');
        const v = Number(raw);
        if (!Number.isFinite(v)) continue;
        (priorsByTier[Number(t)] ??= {})[bucket] = v;
      }
      // Priors are replace-per-tier server-side, so an edited tier must be sent whole or
      // the untouched buckets would be dropped.
      const framework = Object.entries(priorsByTier).map(([t, edited]) => {
        const existing =
          (data?.diagnostic_framework ?? []).find((x) => x.tier_level === Number(t))
            ?.a_priori_probabilities ?? {};
        return {
          tier_level: Number(t),
          a_priori_probabilities: { ...existing, ...edited },
        };
      });

      const fresh = await updateAnalysis(id, {
        feature_likelihood_ratios: Object.entries(lrEdits)
          .map(([rid, raw]) => ({ id: Number(rid), likelihood_ratio: Number(raw) }))
          .filter((e) => Number.isFinite(e.likelihood_ratio) && e.likelihood_ratio > 0),
        diagnostic_framework: framework,
      });
      setData(fresh);
      setLrEdits({});
      setPriorEdits({});
      setSaved('Saved.');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

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
          {saved && <span className="chip chip-good">{saved}</span>}
          <button className="btn btn-primary btn-sm" onClick={save} disabled={!dirty || saving}>
            {saving ? 'Saving…' : dirty ? 'Save changes' : 'No changes'}
          </button>
          <span className="chip chip-neutral">version {data.version}</span>
          <span className="chip chip-neutral">{tiers.length} tiers</span>
          <span className="chip chip-neutral">{lrs.length} likelihood ratios</span>
        </div>
      </div>

      <div className="notice notice-warn text-xs">
        Edits save <strong>in place</strong> — no new case version, because these numbers
        are not learner-facing. Changed rows are marked <em>author overridden</em> so the
        edit stays visible. Bucket names are not editable here: renaming one orphans every
        likelihood ratio pointing at it, and re-running LR generation is the repair.
      </div>

      <section className="card p-4">
        <h2 className="mb-3 text-sm font-semibold text-ink-800">Diagnostic framework</h2>
        <div className="space-y-4">
          {tiers.map((t) => {
            const priors = t.a_priori_probabilities ?? {};
            // Sum the values on screen, edits included -- a sum that ignores what you
            // just typed is worse than none, because it looks authoritative.
            const total = (t.buckets ?? []).reduce((acc, b) => {
              const pending = priorEdits[`${t.tier_level}|${b.name}`];
              const v = pending !== undefined ? Number(pending) : (priors[b.name] ?? 0);
              return acc + (Number.isFinite(v) ? v : 0);
            }, 0);
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
                          <input
                            aria-label={`Prior for ${b.name}`}
                            className="input w-20 py-0.5 text-right tabular-nums"
                            value={priorEdits[`${t.tier_level}|${b.name}`] ?? (p ?? 0)}
                            onChange={(e) =>
                              setPriorEdits((s) => ({
                                ...s,
                                [`${t.tier_level}|${b.name}`]: e.target.value,
                              }))
                            }
                          />
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
                          <td className="py-1 pr-2 text-right">
                            <input
                              aria-label={`Likelihood ratio for ${l.feature_name}`}
                              className="input w-20 py-0.5 text-right tabular-nums"
                              value={lrEdits[l.id ?? -1] ?? l.likelihood_ratio}
                              onChange={(e) =>
                                l.id != null &&
                                setLrEdits((s) => ({ ...s, [l.id!]: e.target.value }))
                              }
                            />
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
