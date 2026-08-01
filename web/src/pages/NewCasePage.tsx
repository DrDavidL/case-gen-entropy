/**
 * Create a case (Phase 4d).
 *
 * Two steps, because generation is expensive and irreversible-ish: `POST /preview-case`
 * runs three sequential LLM calls (~1 minute) and writes nothing, holding the result in a
 * Redis session with a 1-hour TTL. Nothing reaches the database until the author reviews
 * the rendered case and presses Save, which is `POST /finalize-case`.
 *
 * Framework and likelihood-ratio data are generated here too and persisted by the save
 * (ADR-001). They used to be generated and thrown away on every case.
 */

import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import {
  finalizeCase,
  isSimReadyPreview,
  previewCase,
  type SimReadyPreview,
} from '../lib/api';
import { useAuth } from '../lib/useAuth';

export default function NewCasePage() {
  const navigate = useNavigate();
  const { username } = useAuth();

  const [description, setDescription] = useState('');
  const [diagnosis, setDiagnosis] = useState('');
  const [title, setTitle] = useState('');

  const [preview, setPreview] = useState<SimReadyPreview | null>(null);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const generate = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setGenerating(true);
      setError('');
      setPreview(null);
      try {
        const result = await previewCase({
          description,
          primary_diagnosis: diagnosis,
          output_format: 'sim_ready',
        });
        // Asked for sim_ready, so anything else means the backend answered a different
        // question. Better to say so than to render a half-empty preview.
        if (!isSimReadyPreview(result)) {
          setError('The backend returned a non sim-ready preview. Nothing was saved.');
          return;
        }
        setPreview(result);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setGenerating(false);
      }
    },
    [description, diagnosis],
  );

  const save = useCallback(async () => {
    if (!preview) return;
    setSaving(true);
    setError('');
    try {
      const result = await finalizeCase({
        session_id: preview.session_id,
        title: title.trim() || null,
        description,
        primary_diagnosis: diagnosis,
        output_format: 'sim_ready',
        allow_orders: true,
        learner_tasks: preview.default_learner_tasks,
        custom_input: preview.default_custom_input,
        custom_evaluation: preview.default_custom_evaluation,
        // Send the rendered content back untouched. Nothing on this screen edits it —
        // field-level editing is the structured editor's job, and the author lands there
        // straight after saving.
        rendered_content: preview.rendered_content,
        final_orders: [],
        run_oracle: false,
      });
      if ('case_id' in result) navigate(`/cases/${result.case_id}/edit`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [preview, title, description, diagnosis, navigate]);

  const canGenerate =
    Boolean(username) && description.trim() !== '' && diagnosis.trim() !== '';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold text-ink-900">New case</h2>
        <span className="text-xs text-ink-500">
          Generates the case, framework and likelihood ratios. Nothing is saved until you
          review it.
        </span>
      </div>

      {!username && (
        <div className="notice notice-warn">
          Sign in above to generate a case. Generating costs model calls, so it needs a
          credential.
        </div>
      )}
      {error && <div className="notice notice-error">{error}</div>}

      <form onSubmit={generate} className="card space-y-4 p-4">
        <div>
          <label htmlFor="dx" className="label">
            Primary diagnosis
          </label>
          <input
            id="dx"
            className="input"
            placeholder="e.g. Posterior circulation stroke"
            value={diagnosis}
            onChange={(e) => setDiagnosis(e.target.value)}
          />
          <p className="mt-1 text-xs text-ink-400">
            Also drives the Oracle&rsquo;s leak audit, which blocks the panel if the
            diagnosis appears in what the raters can see. A blank one is not allowed
            later, so it is required here.
          </p>
        </div>

        <div>
          <label htmlFor="desc" className="label">
            Case description
          </label>
          <textarea
            id="desc"
            rows={5}
            className="input"
            placeholder="Who the patient is, how they present, and anything the case should hinge on."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <div>
          <label htmlFor="title" className="label">
            Case name <span className="font-normal text-ink-400">(optional)</span>
          </label>
          <input
            id="title"
            className="input"
            placeholder="Defaults to the generated title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>

        <div className="flex items-center gap-3">
          <button type="submit" className="btn btn-primary" disabled={!canGenerate || generating}>
            {generating ? 'Generating…' : preview ? 'Regenerate' : 'Generate case'}
          </button>
          {generating && (
            <span className="text-xs text-ink-500">
              Three sequential model calls — this usually takes about a minute.
            </span>
          )}
        </div>
      </form>

      {preview && (
        <div className="card space-y-3 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-ink-800">
              Review before saving
              <span className="ml-2 font-normal text-ink-500">
                {preview.diagnostic_framework?.length ?? 0} tiers ·{' '}
                {preview.feature_likelihood_ratios?.length ?? 0} likelihood ratios
              </span>
            </h3>
            <button type="button" className="btn btn-primary" onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save case'}
            </button>
          </div>

          <p className="text-xs text-ink-500">
            Saving writes the case and its framework and likelihood-ratio data, then opens
            the field editor. Final Orders and the Oracle panel are set up there.
          </p>

          <div className="prose prose-sm max-h-[55vh] max-w-none overflow-y-auto rounded border border-ink-200 bg-ink-50 p-3">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {preview.rendered_content}
            </ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}
