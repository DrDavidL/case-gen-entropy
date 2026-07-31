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
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
        Could not load cases. {error}
      </div>
    );
  }
  if (!cases) return <p className="text-sm text-slate-500">Loading cases…</p>;

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
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-base font-medium">Cases</h2>
        <span className="text-sm text-slate-500">
          {shown.length}
          {shown.length !== cases.length && ` of ${cases.length}`}
        </span>
      </div>

      <input
        value={filter}
        onChange={e => setFilter(e.target.value)}
        placeholder="Filter by name or id"
        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm
                   focus:border-slate-400 focus:outline-none"
      />

      {shown.length === 0 ? (
        <p className="text-sm text-slate-500">No cases match that filter.</p>
      ) : (
        <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
          {shown.map(c => (
            <li key={c.id}>
              <Link
                to={`/cases/${c.id}`}
                className="flex items-center justify-between gap-4 px-4 py-3
                           hover:bg-slate-50"
              >
                <span className="truncate text-sm">
                  {c.saved_name?.trim() || <em className="text-slate-400">Untitled</em>}
                </span>
                <span className="shrink-0 text-xs tabular-nums text-slate-400">
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
