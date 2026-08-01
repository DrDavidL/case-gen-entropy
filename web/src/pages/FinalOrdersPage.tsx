/**
 * Final Orders and the Oracle panel (Phase 4d).
 *
 * One screen, because the two are inseparable: **no Final Orders means no Oracle panel**.
 * That is the research group's explicit condition, and zero rows is the entire opt-out
 * mechanism — there is deliberately no global toggle (ADR-014).
 *
 * The panel is gated on more than having orders. `preflight` runs the diagnosis-leak
 * audit and the content-parity check first, and both can refuse. A leak hit is
 * overridable with a stated reason that gets recorded on every run it produces; a parity
 * break is not, because the panel would be rating a case the learner will never see.
 */

import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';

import {
  getFinalOrders,
  getOracle,
  getStructured,
  getOraclePreflight,
  proposeFinalOrders,
  putFinalOrders,
  runOracle,
  type OraclePreflight,
  type OracleResults,
  type StructuredRecord,
} from '../lib/api';
import { newId } from '../lib/listRows';

const MAX_ORDERS = 5;

interface OrderRow {
  rid: string;
  order_text: string;
  stem_action: string;
  suppression_synonyms: string;
  provenance: string;
}

function toRow(o: Record<string, unknown>): OrderRow {
  const str = (k: string) => (typeof o[k] === 'string' ? (o[k] as string) : '');
  const syn = Array.isArray(o.suppression_synonyms)
    ? (o.suppression_synonyms as string[]).join(', ')
    : '';
  return {
    rid: newId(),
    order_text: str('order_text'),
    stem_action: str('stem_action'),
    suppression_synonyms: syn,
    provenance: str('provenance') || 'author_entered',
  };
}

function toApi(rows: OrderRow[]) {
  return rows
    .filter((r) => r.order_text.trim() !== '')
    .map((r, i) => ({
      order_text: r.order_text.trim(),
      display_order: i + 1,
      stem_action: r.stem_action.trim() || null,
      provenance: r.provenance,
      suppress_results: true,
      suppression_synonyms: r.suppression_synonyms
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
    }));
}

export default function FinalOrdersPage() {
  const { caseId } = useParams();
  const id = Number(caseId);

  const [rows, setRows] = useState<OrderRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [preflight, setPreflight] = useState<OraclePreflight | null>(null);
  const [results, setResults] = useState<OracleResults | null>(null);
  const [override, setOverride] = useState('');
  // Needed by `propose`, which derives candidates from diagnostic_workup.
  const [record, setRecord] = useState<StructuredRecord | null>(null);

  const refresh = useCallback(async () => {
    const [fo, res, rec] = await Promise.all([
      getFinalOrders(id),
      getOracle(id).catch(() => null),
      getStructured(id).catch(() => null),
    ]);
    setRows(((fo.final_orders ?? []) as Record<string, unknown>[]).map(toRow));
    setResults(res);
    setRecord(rec);
  }, [id]);

  useEffect(() => {
    if (!Number.isFinite(id)) return;
    let cancelled = false;
    void (async () => {
      try {
        await refresh();
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, refresh]);

  const save = useCallback(async () => {
    setBusy('save');
    setError('');
    setNote('');
    try {
      const r = await putFinalOrders(id, { final_orders: toApi(rows) });
      const detached = r.detached_panel_runs ?? [];
      setNote(
        detached.length
          ? `Saved. ${detached.length} removed order(s) had panel runs; those distributions ` +
            `are no longer attached to a live order: ` +
            detached.map((d) => `${d.order_text} (${d.panel_runs_detached})`).join(', ')
          : 'Saved.',
      );
      setPreflight(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy('');
    }
  }, [id, rows, refresh]);

  const propose = useCallback(async () => {
    setBusy('propose');
    setError('');
    try {
      if (!record?.content_structured) {
        setError('This case has no structured record, so candidates cannot be derived.');
        return;
      }
      const r = await proposeFinalOrders(
        record.content_structured,
        record.primary_diagnosis ?? '',
      );
      const cands = (r.candidates ?? []) as Record<string, unknown>[];
      // Appended, never substituted: the author accepts each one explicitly, and
      // provenance records that the model suggested it (ADR-004).
      setRows((cur) => [...cur, ...cands.map(toRow).map((c) => ({ ...c, provenance: 'llm_suggested_accepted' }))].slice(0, MAX_ORDERS));
      setNote(`${cands.length} candidate(s) added below. Nothing is saved until you press Save.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy('');
    }
  }, [record]);

  const check = useCallback(async () => {
    setBusy('preflight');
    setError('');
    try {
      setPreflight(await getOraclePreflight(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy('');
    }
  }, [id]);

  const run = useCallback(async () => {
    setBusy('run');
    setError('');
    setNote('');
    try {
      await runOracle(id, override.trim() || undefined);
      setNote('Panel queued. This takes roughly 10 seconds per Final Order.');
      // Poll until every run leaves the pending/running states.
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 4000));
        const res = await getOracle(id);
        setResults(res);
        const states = (res.items ?? []).map((it) => it.run?.status);
        if (states.length && states.every((s) => s === 'complete' || s === 'failed')) break;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy('');
    }
  }, [id, override]);

  if (!Number.isFinite(id)) return <p className="notice notice-error">Bad case id.</p>;
  if (loading) return <p className="text-sm text-ink-500">Loading…</p>;

  const leakBlocked = preflight?.reason === 'diagnosis_leak';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link to={`/cases/${id}`} className="text-sm text-ink-500 hover:text-ink-800">
          ← Back to case
        </Link>
        <div className="flex items-center gap-2">
          <button className="btn btn-secondary" onClick={propose} disabled={busy !== ''}>
            {busy === 'propose' ? 'Asking…' : 'Suggest orders'}
          </button>
          <button className="btn btn-primary" onClick={save} disabled={busy !== ''}>
            {busy === 'save' ? 'Saving…' : 'Save Final Orders'}
          </button>
        </div>
      </div>

      {error && <div className="notice notice-error">{error}</div>}
      {note && <div className="notice notice-good">{note}</div>}

      <section className="card p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-ink-800">
            Final Orders <span className="font-normal text-ink-400">({rows.length} of {MAX_ORDERS})</span>
          </h2>
          <button
            className="btn btn-secondary btn-sm"
            disabled={rows.length >= MAX_ORDERS}
            onClick={() =>
              setRows((r) => [
                ...r,
                { rid: newId(), order_text: '', stem_action: '', suppression_synonyms: '', provenance: 'author_entered' },
              ])
            }
          >
            Add order
          </button>
        </div>

        {rows.length === 0 && (
          <p className="text-sm text-ink-500">
            None. A case with no Final Orders has no script-concordance item and no Oracle
            panel — that is the supported way to opt out.
          </p>
        )}

        <div className="space-y-3">
          {rows.map((row, i) => (
            <div key={row.rid} className="rounded border border-ink-100 bg-ink-50/60 p-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="label">Order</label>
                  <input
                    className="input"
                    value={row.order_text}
                    placeholder="Brain MRI"
                    onChange={(e) =>
                      setRows((r) => r.map((x) => (x.rid === row.rid ? { ...x, order_text: e.target.value } : x)))
                    }
                  />
                </div>
                <div>
                  <label className="label">Stem action (optional)</label>
                  <input
                    className="input"
                    value={row.stem_action}
                    placeholder="ordering a brain MRI"
                    onChange={(e) =>
                      setRows((r) => r.map((x) => (x.rid === row.rid ? { ...x, stem_action: e.target.value } : x)))
                    }
                  />
                  <p className="mt-1 text-xs text-ink-400">
                    Leave blank to derive it. Set it for activations — &ldquo;ordering a stroke
                    team activation&rdquo; is wrong.
                  </p>
                </div>
                <div className="sm:col-span-2">
                  <label className="label">Suppression synonyms (comma separated)</label>
                  <input
                    className="input"
                    value={row.suppression_synonyms}
                    placeholder="MRI brain, MR brain"
                    onChange={(e) =>
                      setRows((r) =>
                        r.map((x) => (x.rid === row.rid ? { ...x, suppression_synonyms: e.target.value } : x)),
                      )
                    }
                  />
                </div>
              </div>
              <div className="mt-2 flex items-center justify-between">
                <span className="chip chip-neutral">{row.provenance.replace(/_/g, ' ')}</span>
                <button
                  className="btn btn-ghost btn-sm text-red-700 hover:bg-red-50"
                  onClick={() => setRows((r) => r.filter((x) => x.rid !== row.rid))}
                >
                  Remove order {i + 1}
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="card space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-ink-800">Oracle panel</h2>
          <div className="flex items-center gap-2">
            <button className="btn btn-secondary" onClick={check} disabled={busy !== ''}>
              {busy === 'preflight' ? 'Checking…' : 'Preflight (free)'}
            </button>
            <button
              className="btn btn-primary"
              onClick={run}
              disabled={busy !== '' || !preflight || (!preflight.ready && !(leakBlocked && override.trim()))}
            >
              {busy === 'run' ? 'Running…' : `Run panel${preflight ? ` (${preflight.estimated_calls} calls)` : ''}`}
            </button>
          </div>
        </div>

        {!preflight && (
          <p className="text-sm text-ink-500">
            Run preflight first. It shows exactly what the raters will see and runs the
            blocking checks, and it costs nothing.
          </p>
        )}

        {preflight && (
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap gap-2">
              <span className={`chip ${preflight.ready ? 'chip-good' : 'chip-warn'}`}>
                {preflight.ready ? 'Ready' : `Blocked: ${preflight.reason}`}
              </span>
              <span className="chip chip-neutral">stem {preflight.stem_version}</span>
              <span className="chip chip-neutral">roster {preflight.panel_roster_version}</span>
              <span className="chip chip-neutral">{preflight.roster?.length ?? 0} seats</span>
              <span className={`chip ${preflight.leak_audit?.passed ? 'chip-good' : 'chip-warn'}`}>
                leak audit {preflight.leak_audit?.passed ? 'clean' : 'HIT'}
              </span>
            </div>

            {preflight.message && <p className="notice notice-warn">{preflight.message}</p>}

            {leakBlocked && (
              <div className="space-y-2">
                <label className="label">
                  Override reason (recorded on every run this produces)
                </label>
                <input
                  className="input"
                  value={override}
                  placeholder="e.g. CVA appears only as the father's history"
                  onChange={(e) => setOverride(e.target.value)}
                />
                <p className="text-xs text-ink-500">
                  Only a leak hit is overridable. A content-parity break is not — the panel
                  would be rating a case the learner will not see.
                </p>
              </div>
            )}

            <details>
              <summary className="cursor-pointer text-xs text-ink-600">
                What the raters see ({preflight.blinded_context?.length ?? 0} chars, diagnosis withheld)
              </summary>
              <pre className="mt-2 max-h-64 overflow-auto rounded bg-ink-50 p-2 text-xs whitespace-pre-wrap">
                {preflight.blinded_context}
              </pre>
            </details>

            {(preflight.items ?? []).map((it) => (
              <details key={it.final_order_id}>
                <summary className="cursor-pointer text-xs text-ink-600">
                  Rating item for &ldquo;{it.order_text}&rdquo;
                </summary>
                <pre className="mt-2 rounded bg-ink-50 p-2 text-xs whitespace-pre-wrap">
                  {it.oracle_item}
                </pre>
              </details>
            ))}
          </div>
        )}

        {(results?.items ?? []).some((it) => it.run) && (
          <div className="space-y-3 border-t border-ink-200 pt-3">
            <h3 className="text-sm font-semibold text-ink-800">Results</h3>
            {(results?.items ?? []).map((it, i) => {
              const agg = it.aggregate as Record<string, unknown> | null;
              const hist = (agg?.histogram ?? {}) as Record<string, number>;
              const n = Number(agg?.realized_n ?? 0);
              const flags = (agg?.flags ?? []) as { code?: string; message?: string }[];
              if (!it.run) return null;
              return (
                <div key={i} className="rounded border border-ink-200 p-3">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm font-medium">
                      {(it.final_order as Record<string, unknown>).order_text as string}
                    </span>
                    <span className="flex gap-2">
                      {it.stale && <span className="chip chip-warn">stale</span>}
                      <span className="chip chip-neutral">n={n}</span>
                      <span className="chip chip-neutral">
                        entropy {Number(agg?.normalized_entropy ?? 0).toFixed(2)}
                      </span>
                    </span>
                  </div>
                  <div className="space-y-1">
                    {['-2', '-1', '0', '1', '2'].map((k) => {
                      const v = hist[k] ?? 0;
                      const pct = n ? Math.round((v / n) * 100) : 0;
                      return (
                        <div key={k} className="flex items-center gap-2 text-xs">
                          <span className="w-6 text-right tabular-nums text-ink-500">{k}</span>
                          <div className="h-3 flex-1 rounded bg-ink-100">
                            <div className="h-3 rounded bg-brand-500" style={{ width: `${pct}%` }} />
                          </div>
                          <span className="w-14 tabular-nums text-ink-500">
                            {v} ({pct}%)
                          </span>
                        </div>
                      );
                    })}
                  </div>
                  {flags.map((f, j) => (
                    <p key={j} className="mt-2 text-xs text-amber-800">
                      {f.message}
                    </p>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
