import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  adoptCase,
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

  // Re-fetch after adoption. Separate from the mount effect rather than sharing it: this
  // one is triggered by an action, so there is no unmount race to cancel and no loading
  // state to reset — the page is already rendered and only the record changes.
  const reload = useCallback(async () => {
    const [c, s, a] = await Promise.all([
      getCase(id),
      getStructured(id),
      getAnalysis(id),
    ]);
    setSimCase(c);
    setRecord(s);
    setAnalysis(a);
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
  if (loading) return <p className="text-sm text-ink-500">Loading case…</p>;
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
          {simCase?.saved_name?.trim() || <em className="text-ink-400">Untitled</em>}
        </h2>
        <div className="flex items-center gap-3">
          {record && (
            <>
              <Link to={`/cases/${id}/edit`} className="btn btn-secondary btn-sm">
                Edit fields
              </Link>
              <Link to={`/cases/${id}/orders`} className="btn btn-secondary btn-sm">
                Final Orders &amp; Oracle
              </Link>
              <Link to={`/cases/${id}/analysis`} className="btn btn-secondary btn-sm">
                Framework &amp; LRs
              </Link>
            </>
          )}
          <span className="text-xs tabular-nums text-ink-400">#{id}</span>
        </div>
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
        <AdoptPanel
          caseId={id}
          defaultTitle={simCase?.saved_name ?? ''}
          onAdopted={reload}
        />
      )}

      <section className="rounded-lg border border-ink-200 bg-white p-5">
        <h3 className="mb-3 text-sm font-medium text-ink-500">Case content</h3>
        {content ? (
          <div
            className="prose prose-sm max-w-none prose-headings:font-semibold
                       prose-headings:text-ink-800"
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        ) : (
          <p className="text-sm text-ink-500">This case has no content.</p>
        )}
      </section>
    </div>
  );
}

/**
 * The way in for a case with no authoring record.
 *
 * 100 of 106 production cases were in this state, and the page previously described the
 * problem without offering the fix — the endpoint existed and nothing in the SPA called
 * it, so every one of those cases was a dead end.
 *
 * Adoption is preferred over forking the case (`copy`) because it is additive:
 * `case_details` is never written, so the learner-facing case list gains no duplicate and
 * the transcripts already keyed to this `case_id` stay attached to it.
 */
function AdoptPanel({
  caseId,
  defaultTitle,
  onAdopted,
}: {
  caseId: number;
  defaultTitle: string;
  onAdopted: () => Promise<void>;
}) {
  const [diagnosis, setDiagnosis] = useState('');
  const [title, setTitle] = useState(defaultTitle);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    if (!diagnosis.trim()) return;
    setBusy(true);
    setError('');
    try {
      await adoptCase(caseId, {
        primary_diagnosis: diagnosis.trim(),
        title: title.trim() || undefined,
      });
      await onAdopted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
      <p>
        This case has no authoring record, so it carries no version, no Final Orders, and
        no Oracle panel. Adopting it reads its structured record from the document below
        and writes that as version 1 (ADR-019).
      </p>
      <p className="mt-2 text-xs">
        The case itself is not modified — the simulator serves exactly the same document
        afterwards. Two things cannot be recovered and start empty: the diagnostic
        framework and the likelihood ratios were never stored for cases this old. Anything
        the markdown does not state is reconstructed by the model, so read the case through
        once afterwards.
      </p>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="adopt-dx">
            Primary diagnosis <span className="text-red-700">*</span>
          </label>
          <input
            id="adopt-dx"
            className="input"
            value={diagnosis}
            disabled={busy}
            placeholder="e.g. Asthma exacerbation"
            onChange={(e) => setDiagnosis(e.target.value)}
          />
          <p className="mt-1 text-xs text-amber-800">
            Required, and withheld from the Oracle panel. The leak audit builds its search
            terms from this field, so leaving it blank would let the audit pass having
            checked nothing.
          </p>
        </div>
        <div>
          <label className="label" htmlFor="adopt-title">
            Title
          </label>
          <input
            id="adopt-title"
            className="input"
            value={title}
            disabled={busy}
            onChange={(e) => setTitle(e.target.value)}
          />
          <p className="mt-1 text-xs text-amber-800">
            Defaults to the case&rsquo;s current name.
          </p>
        </div>
      </div>

      {error && (
        <div className="mt-3 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-800">
          {error}
        </div>
      )}

      <button
        className="btn btn-primary btn-sm mt-3"
        disabled={busy || diagnosis.trim() === ''}
        onClick={submit}
      >
        {busy ? 'Adopting — this takes a moment…' : 'Adopt this case'}
      </button>
    </div>
  );
}

function Back() {
  return (
    <Link to="/cases" className="text-sm text-ink-500 hover:text-ink-800">
      ← All cases
    </Link>
  );
}

function Chip({ label, tone }: { label: string; tone: 'ok' | 'warn' | 'neutral' }) {
  const tones = {
    ok: 'border-emerald-200 bg-emerald-50 text-emerald-800',
    warn: 'border-amber-200 bg-amber-50 text-amber-900',
    neutral: 'border-ink-200 bg-ink-50 text-ink-600',
  };
  return (
    <span className={`rounded-full border px-2.5 py-1 ${tones[tone]}`}>{label}</span>
  );
}
