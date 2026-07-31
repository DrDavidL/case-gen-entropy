import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  getAnalysis,
  getCase,
  getStructured,
  type CaseAnalysis,
  type SimReadyCase,
  type StructuredRecord,
} from '../lib/api';
import { ParityBanner } from '../components/ParityNotice';

/**
 * Read-only. The structured editor is the rest of Phase 4c; this page already surfaces
 * content-parity state, because an author needs to see a divergence continuously rather
 * than discover it when the Oracle refuses to run.
 */
export default function CaseViewPage() {
  const { caseId } = useParams();
  const id = Number(caseId);

  const [simCase, setSimCase] = useState<SimReadyCase | null>(null);
  const [record, setRecord] = useState<StructuredRecord | null>(null);
  const [analysis, setAnalysis] = useState<CaseAnalysis | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // A bad id is derived during render below, not set from here. eslint's
    // react-hooks/set-state-in-effect is right to flag the alternative: calling setState
    // unconditionally on mount schedules a second render for something already knowable.
    if (!Number.isFinite(id)) return;
    let cancelled = false;
    // The structured record and analysis are optional, so they must not fail the page.
    // getStructured/getAnalysis already turn 404 and 503 into null; this only guards
    // against the case row itself being unreachable.
    Promise.all([getCase(id), getStructured(id), getAnalysis(id)])
      .then(([c, s, a]) => {
        if (cancelled) return;
        setSimCase(c);
        setRecord(s);
        setAnalysis(a);
      })
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [id]);

  const badId = !Number.isFinite(id);
  if (badId) {
    return (
      <div className="space-y-4">
        <Back />
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          &ldquo;{caseId}&rdquo; is not a valid case id.
        </div>
      </div>
    );
  }
  if (loading) return <p className="text-sm text-slate-500">Loading case…</p>;
  if (error) {
    return (
      <div className="space-y-4">
        <Back />
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          {error}
        </div>
      </div>
    );
  }

  const content = simCase?.content ?? '';
  const lrCount = analysis?.feature_likelihood_ratios?.length ?? 0;
  const tierCount = analysis?.diagnostic_framework?.length ?? 0;

  return (
    <div className="space-y-5">
      <Back />

      <ParityBanner state={record} />

      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-base font-medium">
          {simCase?.saved_name?.trim() || <em className="text-slate-400">Untitled</em>}
        </h2>
        <span className="text-xs tabular-nums text-slate-400">#{id}</span>
      </div>

      <div className="flex flex-wrap gap-2 text-xs">
        <Chip
          label={record ? `Version ${record.version}` : 'No authoring record'}
          tone={record ? 'neutral' : 'warn'}
        />
        {record && (
          <Chip
            label={record.parity_broken ? 'Content parity broken' : 'In parity'}
            tone={record.parity_broken ? 'warn' : 'ok'}
          />
        )}
        {record?.render_detached && <Chip label="Markdown detached" tone="warn" />}
        <Chip
          label={
            analysis ? `${tierCount} tiers · ${lrCount} LRs` : 'No stored analysis'
          }
          tone={analysis ? 'neutral' : 'warn'}
        />
      </div>

      {record?.parity_broken && record.parity_message && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          {record.parity_message}
        </div>
      )}

      {!record && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          This case has no authoring record, so it carries no version, no Final Orders,
          and no Oracle panel. It has to be adopted before any of those work (ADR-019).
        </div>
      )}

      <section className="rounded-lg border border-slate-200 bg-white p-5">
        <h3 className="mb-3 text-sm font-medium text-slate-500">Case content</h3>
        {content ? (
          <div
            className="prose prose-sm max-w-none prose-headings:font-semibold
                       prose-headings:text-slate-800"
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        ) : (
          <p className="text-sm text-slate-500">This case has no content.</p>
        )}
      </section>
    </div>
  );
}

function Back() {
  return (
    <Link to="/cases" className="text-sm text-slate-500 hover:text-slate-800">
      ← All cases
    </Link>
  );
}

function Chip({ label, tone }: { label: string; tone: 'ok' | 'warn' | 'neutral' }) {
  const tones = {
    ok: 'border-emerald-200 bg-emerald-50 text-emerald-800',
    warn: 'border-amber-200 bg-amber-50 text-amber-900',
    neutral: 'border-slate-200 bg-slate-50 text-slate-600',
  };
  return (
    <span className={`rounded-full border px-2.5 py-1 ${tones[tone]}`}>{label}</span>
  );
}
