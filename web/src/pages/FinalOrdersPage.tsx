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
  suggestSynonyms,
  type OraclePreflight,
  type OracleResults,
  type StructuredRecord,
} from '../lib/api';
import { newId } from '../lib/listRows';

const MAX_ORDERS = 5;

/**
 * Why synonyms exist, shown where the author is deciding whether to bother.
 *
 * Written as a hover rather than body copy because it answers a question most authors
 * will not think to ask: the field looks optional and is not. It is what the simulator's
 * pre-model interception matches on, so an empty list means the learner who types "ACA"
 * instead of "Anti-centromere antibody" gets a real result — and the rating that order
 * exists to collect is then worthless.
 *
 * `title` on a focusable element rather than a custom popover: it reaches keyboard and
 * screen-reader users for free, and this is explanatory text, not an interaction.
 */
function InfoHover({ label, children }: { label: string; children: string }) {
  return (
    <span
      tabIndex={0}
      role="note"
      aria-label={`${label}: ${children}`}
      title={children}
      className="ml-1 inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-ink-300 text-[10px] font-semibold leading-none text-ink-500 align-middle hover:border-ink-500 hover:text-ink-700 focus:outline-none focus:ring-2 focus:ring-offset-1"
    >
      i
    </span>
  );
}

// Why a stored distribution no longer describes what would be asked today. Three causes,
// three different fixes, so they are not collapsed into one sentence. Mirrors
// _STALE_REASON_TEXT in frontend/app.py; the two UIs must not disagree about what a
// stale distribution means.
const STALE_REASON_TEXT: Record<string, string> = {
  content_drift:
    'The case content has changed since this panel ran, so this distribution describes a ' +
    'version the learner will not see.',
  item_changed:
    'The wording of this order has been edited since the panel ran. Learners see the ' +
    'current wording and would be scored against the old panel’s answer to a different ' +
    'question.',
  stem_changed:
    'The rating stem has changed since this panel ran. The stem is the measurement ' +
    'instrument, so these ratings are not comparable to ones collected now.',
  item_unverifiable:
    'This run does not record what it asked, so there is no way to confirm it matches the ' +
    'current item.',
  unknown: 'This distribution may no longer describe the current case.',
};

const SYNONYM_HELP =
  'The simulator blocks a Final Order result by matching what the learner typed against ' +
  'this list plus the order label. Anything not covered here returns a real result, and a ' +
  'learner who has seen the result cannot meaningfully rate whether ordering it was ' +
  'appropriate — that rating is the entire point of the item. Include abbreviations (ACA), ' +
  'both word orders (CT abdomen / abdominal CT), hyphen-free spellings, and the drug name ' +
  'for a treatment. Do not include a bare modality like "CT" or "antibody": that would ' +
  'also suppress unrelated orders and degrade the case.';

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

  /**
   * Fill in the phrasings a learner might actually type.
   *
   * Merged into what is already there, never substituted — the endpoint returns the
   * author's own synonyms alongside the model's, so accepting a suggestion cannot drop
   * one the author added deliberately.
   */
  const suggest = useCallback(async () => {
    setBusy('synonyms');
    setError('');
    setNote('');
    try {
      const r = await suggestSynonyms(toApi(rows));
      const byLabel = new Map(
        ((r.suggestions ?? []) as Record<string, unknown>[]).map((s) => [
          String(s.order_text ?? '').toLowerCase(),
          s,
        ]),
      );
      let added = 0;
      setRows((cur) =>
        cur.map((row) => {
          const hit = byLabel.get(row.order_text.trim().toLowerCase());
          if (!hit) return row;
          const merged = (hit.synonyms as string[] | undefined) ?? [];
          added += ((hit.added as string[] | undefined) ?? []).length;
          return { ...row, suppression_synonyms: merged.join(', ') };
        }),
      );
      setNote(
        added > 0
          ? `${added} phrasing(s) added. Review them, remove anything that could match a different order, then press Save.`
          : 'No new phrasings suggested — the existing synonyms already cover what the model would add.',
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy('');
    }
  }, [rows]);

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
  const allHits = (preflight?.leak_audit?.hits ?? []) as {
    term?: string;
    kind?: string;
    section?: string;
    snippet?: string;
  }[];
  // Split by kind. Both come from one audit, but they are different problems with
  // different remedies, and the diagnosis-leak copy below ("the diagnosis appears in what
  // the raters would see") is simply false about a withheld maneuver.
  const hits = allHits.filter((h) => h.kind !== 'withheld_finding');
  const blindingHits = allHits.filter((h) => h.kind === 'withheld_finding');
  const withheld = (preflight?.withheld_findings ?? []) as string[];
  const withheldRequested = (preflight?.withheld_findings_requested ?? []) as string[];

  /**
   * Why Run cannot be pressed, or null when it can.
   *
   * A disabled button with no explanation is the actual bug here: preflight correctly
   * refused, and the screen said only "Blocked: diagnosis_leak" in a chip.
   */
  const runDisabled: string | null =
    busy !== ''
      ? 'Working…'
      : !preflight
        ? 'Run preflight first — it is free and shows what the raters will see.'
        : preflight.ready
          ? null
          : leakBlocked
            ? override.trim()
              ? null
              : 'The diagnosis leaks into what the raters see. Fix the case, or state an override reason below.'
            : `Blocked: ${preflight.reason}. This is not overridable — the panel would rate a case the learner will not see.`;

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
          <div className="flex items-center gap-2">
            <button
              className="btn btn-secondary btn-sm"
              disabled={busy !== '' || rows.every((r) => r.order_text.trim() === '')}
              title="Ask the model for the phrasings a learner might type for these orders. Writes nothing."
              onClick={suggest}
            >
              {busy === 'synonyms' ? 'Suggesting…' : 'Suggest synonyms'}
            </button>
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
        </div>

        {rows.some((r) => r.order_text.trim() !== '' && r.suppression_synonyms.trim() === '') && (
          <div className="mb-3 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <span className="font-medium">Some orders have no synonyms.</span> Suppression
            matches on the order label plus this list, so a learner who phrases the order
            any other way gets a real result — and a learner who has seen the result cannot
            meaningfully rate whether ordering it was appropriate.
          </div>
        )}

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
                  <label className="label">
                    Suppression synonyms (comma separated)
                    <InfoHover label="Suppression synonyms">{SYNONYM_HELP}</InfoHover>
                  </label>
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
                  {row.order_text.trim() !== '' && row.suppression_synonyms.trim() === '' && (
                    <p className="mt-1 text-xs text-amber-700">
                      No synonyms. This order is blocked only when a learner types its label
                      exactly — any other phrasing returns a real result and invalidates the
                      rating. Use <span className="font-medium">Suggest synonyms</span> above.
                    </p>
                  )}
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
              disabled={runDisabled !== null}
              title={runDisabled ?? undefined}
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
              {withheldRequested.length > 0 && (
                <span className="chip chip-neutral">
                  {withheld.length} of {withheldRequested.length} withheld term(s) matched
                </span>
              )}
            </div>

            {blindingHits.length > 0 && (
              <div className="notice notice-warn space-y-2">
                <p className="font-medium">
                  A withheld finding is still in what the raters would see.
                </p>
                <ul className="space-y-1">
                  {blindingHits.map((h, i) => (
                    <li key={i} className="text-xs">
                      <span className="chip chip-warn">{h.term}</span>{' '}
                      <span className="text-ink-600">in {h.section}</span>
                      {h.snippet && (
                        <pre className="mt-1 overflow-x-auto rounded bg-white/60 p-1">
                          {h.snippet}
                        </pre>
                      )}
                    </li>
                  ))}
                </ul>
                <p className="text-xs">
                  Itemised exam and workup entries are dropped automatically; free-text
                  fields cannot be. Move the finding into an itemised entry in{' '}
                  <Link to={`/cases/${id}/edit`} className="underline">
                    the case editor
                  </Link>
                  , or stop withholding it. <strong>This is not overridable</strong> — you
                  are the person who declared it off-limits.
                </p>
              </div>
            )}

            {withheldRequested.length > 0 && withheld.length < withheldRequested.length && (
              <p className="notice notice-warn text-xs">
                <strong>
                  {withheldRequested.length - withheld.length} withheld term(s) matched
                  nothing in this case.
                </strong>{' '}
                A term that matches nothing looks like blinding and does nothing. Check the
                spelling against how the record names the finding.
              </p>
            )}

            {withheld.length > 0 && (
              <p className="text-xs text-ink-500">
                Withheld from the panel: {withheld.join(', ')}. Give human expert raters the
                same blinded context, or the two rating sets are not comparable.
              </p>
            )}

            {preflight.message && <p className="notice notice-warn">{preflight.message}</p>}

            {runDisabled && <p className="notice notice-warn">{runDisabled}</p>}

            {hits.length > 0 && (
              <div className="notice notice-warn space-y-2">
                <p className="font-medium">
                  The diagnosis appears in what the raters would see.
                </p>
                <ul className="space-y-1">
                  {hits.map((h, i) => (
                    <li key={i} className="text-xs">
                      <span className="chip chip-warn">{h.term}</span>{' '}
                      <span className="text-ink-600">in {h.section}</span>
                      {h.snippet && (
                        <pre className="mt-1 overflow-x-auto rounded bg-white/60 p-1">
                          {h.snippet}
                        </pre>
                      )}
                    </li>
                  ))}
                </ul>
                <p className="text-xs">
                  <strong>Usually this is a case problem, not an override.</strong> A rater
                  who can read the answer is not rating a decision — rename the offending
                  item (for example &ldquo;PCR for Cyclospora&rdquo; &rarr; &ldquo;stool PCR for
                  parasites&rdquo;) in{' '}
                  <Link to={`/cases/${id}/edit`} className="underline">
                    the case editor
                  </Link>
                  , then re-run preflight. Override only when the hit is genuinely benign,
                  such as a diagnosis that appears solely as a parent&rsquo;s history.
                </p>
              </div>
            )}

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
              const excluded = (agg?.excluded_calls ?? []) as {
                persona_id?: string;
                model?: string;
                status?: string;
                explanation?: string;
                error?: string;
              }[];
              const requested = Number(agg?.requested_n ?? 0);
              if (!it.run) return null;
              return (
                <div key={i} className="rounded border border-ink-200 p-3">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm font-medium">
                      {(it.final_order as Record<string, unknown>).order_text as string}
                    </span>
                    <span className="flex gap-2">
                      {it.stale && <span className="chip chip-warn">stale</span>}
                      <span className={excluded.length ? 'chip chip-warn' : 'chip chip-neutral'}>
                        n={n}
                        {requested && n !== requested ? `/${requested}` : ''}
                      </span>
                      <span className="chip chip-neutral">
                        entropy {Number(agg?.normalized_entropy ?? 0).toFixed(2)}
                      </span>
                    </span>
                  </div>
                  {it.stale && (
                    <p className="mb-2 text-xs text-amber-800">
                      {(it.stale_reasons?.length ? it.stale_reasons : ['unknown'])
                        .map((r) => STALE_REASON_TEXT[r] ?? STALE_REASON_TEXT.unknown)
                        .join(' ')}{' '}
                      Re-run the panel.
                    </p>
                  )}
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
                  {excluded.length > 0 && (
                    <details className="mt-2" open>
                      <summary className="cursor-pointer text-xs text-ink-600">
                        {excluded.length} of {requested || n + excluded.length} panelists
                        returned no rating
                      </summary>
                      <ul className="mt-1 space-y-1">
                        {excluded.map((c, j) => (
                          <li key={j} className="text-xs text-ink-600">
                            <span className="font-medium">{c.persona_id ?? 'unknown seat'}</span>
                            {c.model ? ` (${c.model})` : ''} — <code>{c.status}</code>:{' '}
                            {c.explanation}
                            {c.error && (
                              <span className="block break-all text-ink-500">{c.error}</span>
                            )}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
