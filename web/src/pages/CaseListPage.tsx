import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { listCases, type CaseListItem } from '../lib/api';

export default function CaseListPage() {
  const [cases, setCases] = useState<CaseListItem[] | null>(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('');

  useEffect(() => {
    listCases()
      .then(setCases)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) {
    return (
      <div className="notice notice-error">
        Could not load cases. {error}
      </div>
    );
  }
  if (!cases) return <p className="text-sm text-ink-500">Loading cases…</p>;

  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? cases.filter(
        c =>
          (c.saved_name ?? '').toLowerCase().includes(needle) ||
          String(c.id).includes(needle),
      )
    : cases;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <h2 className="text-lg font-semibold text-ink-900">Cases</h2>
          <span className="text-sm text-ink-500">
            {shown.length}
            {shown.length !== cases.length && ` of ${cases.length}`}
          </span>
        </div>
        <Link to="/cases/new" className="btn btn-primary">
          New case
        </Link>
      </div>

      <input
        value={filter}
        onChange={e => setFilter(e.target.value)}
        placeholder="Filter by name or id"
className="input"
      />

      {shown.length === 0 ? (
        <p className="text-sm text-ink-500">No cases match that filter.</p>
      ) : (
        <ul className="card divide-y divide-ink-200 overflow-hidden">
          {shown.map(c => (
            <li key={c.id}>
              <Link
                to={`/cases/${c.id}`}
className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-ink-50"
              >
                <span className="truncate text-sm">
                  {c.saved_name?.trim() || <em className="text-ink-400">Untitled</em>}
                </span>
                <span className="shrink-0 text-xs tabular-nums text-ink-400">
                  #{c.id}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
