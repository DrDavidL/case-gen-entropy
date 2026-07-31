/**
 * The two form primitives the structured editor is built from.
 *
 * The record has 49 scalar fields across 10 groups. Hand-writing 49 inputs would mean 49
 * places for a field name to be mistyped, and a mistyped name is invisible: it renders as
 * an empty box an author cannot distinguish from a field that is genuinely blank. So the
 * layout is a declarative spec (`fieldSpec.ts`) whose keys are checked against the
 * generated types, and these components render it.
 */

/** Every scalar on the structured record is a string, so `Field` only handles strings. */
export function Field({
  label,
  value,
  onChange,
  multiline = false,
  rows = 3,
  hint,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  multiline?: boolean;
  rows?: number;
  hint?: string;
}) {
  const id = `f-${label.replace(/\W+/g, '-').toLowerCase()}`;
  const shared =
    'w-full rounded border border-slate-300 px-2 py-1.5 text-sm ' +
    'focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-400';

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs font-medium text-slate-600">
        {label}
      </label>
      {multiline ? (
        <textarea
          id={id}
          rows={rows}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={shared}
        />
      ) : (
        <input
          id={id}
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={shared}
        />
      )}
      {hint && <p className="text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

export function FieldGroup({
  title,
  children,
  columns = 2,
}: {
  title: string;
  children: React.ReactNode;
  columns?: 1 | 2;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="mb-3 text-sm font-semibold text-slate-800">{title}</h3>
      <div
        className={
          columns === 1 ? 'space-y-3' : 'grid gap-3 sm:grid-cols-2'
        }
      >
        {children}
      </div>
    </section>
  );
}
