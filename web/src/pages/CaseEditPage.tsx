/**
 * The structured editor (ADR-002, Phase 4c).
 *
 * Authors edit fields; the renderer derives the markdown. The preview pane is rendered by
 * the server so exactly one renderer stays authoritative — a client-side reimplementation
 * would drift, which is not hypothetical here (see `/oracle/render-items`).
 *
 * Saving always writes a new version, and while the current version is detached it
 * discards the hand-edited markdown. That is guarded by `DetachedSaveConfirm`, which also
 * offers `/resync` — the opposite operation — so the two are never one button.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import {
  ApiError,
  getStructured,
  renderPreview,
  saveStructured,
  type StructuredContent,
  type StructuredRecord,
} from '../lib/api';
import { useAuth } from '../lib/useAuth';
import { Field, FieldGroup } from '../components/fields';
import { ListEditor } from '../components/ListEditor';
import { toApi, toRows, type Row } from '../lib/listRows';
import { DetachedSaveConfirm, ParityBanner } from '../components/ParityNotice';
import {
  DOOR_CHART,
  EXAM_TEXT,
  FAMILY,
  HPI,
  LISTS,
  MEDS,
  PATIENT_APPROACH,
  PMH,
  REASONING,
  SOCIAL,
  TEACHING,
  TOP,
  VITALS,
  type GroupDef,
} from '../lib/fieldSpec';

/** Read a nested string without `any`, tolerating a record that predates a field. */
function readStr(obj: unknown, path: string[]): string {
  let cur: unknown = obj;
  for (const p of path) {
    if (typeof cur !== 'object' || cur === null) return '';
    cur = (cur as Record<string, unknown>)[p];
  }
  return typeof cur === 'string' ? cur : '';
}

/** Immutably set a nested string, creating intermediate objects as needed. */
function writeStr(obj: unknown, path: string[], value: string): Record<string, unknown> {
  const base: Record<string, unknown> =
    typeof obj === 'object' && obj !== null ? { ...(obj as Record<string, unknown>) } : {};
  if (path.length === 1) {
    base[path[0]] = value;
    return base;
  }
  base[path[0]] = writeStr(base[path[0]], path.slice(1), value);
  return base;
}

export default function CaseEditPage() {
  const { caseId } = useParams();
  const id = Number(caseId);
  const { username } = useAuth();

  const [record, setRecord] = useState<StructuredRecord | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [lists, setLists] = useState<Record<string, Row[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState('');
  const [previewError, setPreviewError] = useState('');
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState('');

  useEffect(() => {
    if (!Number.isFinite(id)) return;
    let cancelled = false;
    getStructured(id)
      .then((r) => {
        if (cancelled) return;
        setRecord(r);
        const content = (r?.content_structured ?? {}) as Record<string, unknown>;
        setDraft(content);
        const next: Record<string, Row[]> = {};
        for (const l of LISTS) next[l.key] = toRows(content[l.key], l.a.key, l.b.key);
        setLists(next);
      })
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [id]);

  /** The draft as the API expects it: scalars plus the lists, without client row ids. */
  const payload = useMemo((): StructuredContent => {
    const out: Record<string, unknown> = { ...draft };
    for (const l of LISTS) out[l.key] = toApi(lists[l.key] ?? [], l.a.key, l.b.key);
    return out as StructuredContent;
  }, [draft, lists]);

  // Server-rendered preview, debounced. Typing in a 49-field form would otherwise fire a
  // request per keystroke; the endpoint is cheap but not free, and a preview that lags
  // behind by a few hundred ms is fine while one that stutters is not.
  useEffect(() => {
    if (loading || !record) return;
    const t = setTimeout(() => {
      renderPreview(payload)
        .then((r) => {
          setPreview(r.content_rendered);
          setPreviewError('');
        })
        .catch((e: Error) =>
          setPreviewError(
            e instanceof ApiError && e.status === 422
              ? `This record cannot be rendered yet: ${e.detail}`
              : e.message,
          ),
        );
    }, 400);
    return () => clearTimeout(t);
  }, [payload, loading, record]);

  const set = useCallback(
    (path: string[]) => (v: string) => setDraft((d) => writeStr(d, path, v)),
    [],
  );

  const doSave = useCallback(async () => {
    setSaving(true);
    setError('');
    try {
      await saveStructured(id, { content_structured: payload });
      const fresh = await getStructured(id);
      setRecord(fresh);
      setSaved(`Saved as version ${fresh?.version ?? '?'}.`);
      setConfirmOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setConfirmOpen(false);
    } finally {
      setSaving(false);
    }
  }, [id, payload]);

  const onSaveClick = useCallback(() => {
    setSaved('');
    // Only a *detached* version loses work on save. `content_drift` also breaks parity
    // but a structured save is the correct repair for it, with nothing to discard.
    if (record?.parity_reason === 'render_detached') setConfirmOpen(true);
    else void doSave();
  }, [record, doSave]);

  if (!Number.isFinite(id)) return <Msg>&ldquo;{caseId}&rdquo; is not a valid case id.</Msg>;
  if (loading) return <p className="text-sm text-ink-500">Loading case…</p>;
  if (!record)
    return (
      <div className="space-y-4">
        <Back id={id} />
        <Msg>
          This case has no authoring record to edit. Adopt it first — see ADR-019.
        </Msg>
      </div>
    );

  const g = <T,>(spec: GroupDef<T>, prefix: string[]) => (
    <FieldGroup title={spec.title} columns={spec.columns}>
      {spec.fields.map((f) => (
        <Field
          key={String(f.key)}
          label={f.label}
          hint={f.hint}
          multiline={f.multiline}
          rows={f.rows}
          value={readStr(draft, [...prefix, String(f.key)])}
          onChange={set([...prefix, String(f.key)])}
        />
      ))}
    </FieldGroup>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Back id={id} />
        <div className="flex items-center gap-3 text-sm">
          {saved && <span className="chip chip-good">{saved}</span>}
          {!username && (
            <span className="text-amber-700">Sign in to save.</span>
          )}
          <button
            type="button"
            onClick={onSaveClick}
            disabled={!username || saving}
            className="btn btn-primary"
          >
            {saving ? 'Saving…' : 'Save as new version'}
          </button>
        </div>
      </div>

      <ParityBanner state={record} />
      {error && <Msg>{error}</Msg>}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
        <div className="space-y-4">
          {g(TOP, [])}
          {g(PATIENT_APPROACH, ['patient_approach'])}
          {g(HPI, ['hpi'])}
          {g(PMH, ['past_medical_history'])}
          {g(SOCIAL, ['social_history'])}
          {g(FAMILY, ['family_history'])}
          {g(MEDS, ['medications_allergies'])}
          {g(EXAM_TEXT, [])}
          {LISTS.map((l) => (
            <ListEditor
              key={l.key}
              title={l.title}
              rows={lists[l.key] ?? []}
              onChange={(next) => setLists((s) => ({ ...s, [l.key]: next }))}
              labelA={l.a.label}
              labelB={l.b.label}
            />
          ))}
          {g(REASONING, ['diagnostic_reasoning'])}
          {g(TEACHING, ['teaching_points'])}
          {g(DOOR_CHART, ['door_chart'])}
          {g(VITALS, ['door_chart', 'vital_signs'])}
        </div>

        <aside className="lg:sticky lg:top-4 lg:self-start">
          <div className="card p-4">
            <h3 className="mb-2 text-sm font-semibold text-ink-800">
              Preview
              <span className="ml-2 font-normal text-ink-400">
                rendered by the server
              </span>
            </h3>
            {previewError ? (
              <p className="text-xs text-red-600">{previewError}</p>
            ) : (
              <div className="prose prose-sm max-h-[70vh] max-w-none overflow-y-auto">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{preview}</ReactMarkdown>
              </div>
            )}
          </div>
        </aside>
      </div>

      <DetachedSaveConfirm
        open={confirmOpen}
        busy={saving}
        onConfirm={() => void doSave()}
        onCancel={() => setConfirmOpen(false)}
      />
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

function Msg({ children }: { children: React.ReactNode }) {
  return (
    <div className="notice notice-warn">
      {children}
    </div>
  );
}
