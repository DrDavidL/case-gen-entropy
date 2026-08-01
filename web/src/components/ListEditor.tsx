/**
 * Editor for the record's three dynamic lists.
 *
 * **Rows are keyed by a client-side stable id, never by array index.** This is the
 * specific thing the Streamlit editor could not do safely: `sim_image_links` kept list
 * state in a slot initialised with `if "key" not in st.session_state`, so it was never
 * refreshed across case loads and leaked one case's rows into another, which then got
 * saved (see CLAUDE.md). Index keys have the same shape of bug in React — delete row 2
 * of 5 and every row below it shifts, so React reuses the wrong DOM node and an author's
 * cursor, scroll position, and in-flight edit land on a different row than the one they
 * were typing in.
 *
 * The id exists only in the browser. `toApi` strips it, so nothing about this
 * reconciliation detail reaches the database.
 */

import { Field } from './fields';
import { newId, type Row } from '../lib/listRows';

export function ListEditor({
  title,
  rows,
  onChange,
  labelA,
  labelB,
}: {
  title: string;
  rows: Row[];
  onChange: (next: Row[]) => void;
  labelA: string;
  labelB: string;
}) {
  const update = (rid: string, patch: Partial<Row>) =>
    onChange(rows.map((r) => (r.rid === rid ? { ...r, ...patch } : r)));

  const remove = (rid: string) => onChange(rows.filter((r) => r.rid !== rid));

  const move = (rid: string, delta: number) => {
    const i = rows.findIndex((r) => r.rid === rid);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= rows.length) return;
    const next = rows.slice();
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };

  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink-800">
          {title}{' '}
          <span className="font-normal text-ink-400">({rows.length})</span>
        </h3>
        <button
          type="button"
          onClick={() => onChange([...rows, { rid: newId(), a: '', b: '' }])}
          className="btn btn-secondary btn-sm"
        >
          Add row
        </button>
      </div>

      {rows.length === 0 && (
        <p className="text-xs text-ink-400">None yet.</p>
      )}

      <div className="space-y-3">
        {rows.map((row, i) => (
          // key is the stable row id. Using `i` here is the bug this component exists to
          // avoid -- see the module comment.
          <div
            key={row.rid}
            className="grid gap-2 rounded border border-ink-100 bg-ink-50/60 p-2 sm:grid-cols-[1fr_1fr_auto]"
          >
            <Field
              label={labelA}
              value={row.a}
              onChange={(v) => update(row.rid, { a: v })}
              multiline
              rows={2}
            />
            <Field
              label={labelB}
              value={row.b}
              onChange={(v) => update(row.rid, { b: v })}
              multiline
              rows={2}
            />
            <div className="flex flex-row gap-1 sm:flex-col sm:justify-center">
              <button
                type="button"
                aria-label={`Move ${labelA} row ${i + 1} up`}
                disabled={i === 0}
                onClick={() => move(row.rid, -1)}
                className="btn btn-ghost btn-sm"
              >
                ↑
              </button>
              <button
                type="button"
                aria-label={`Move ${labelA} row ${i + 1} down`}
                disabled={i === rows.length - 1}
                onClick={() => move(row.rid, 1)}
                className="btn btn-ghost btn-sm"
              >
                ↓
              </button>
              <button
                type="button"
                aria-label={`Remove ${labelA} row ${i + 1}`}
                onClick={() => remove(row.rid)}
                className="btn btn-ghost btn-sm text-red-700 hover:bg-red-50"
              >
                ✕
              </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
