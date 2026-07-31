/**
 * Content-parity state, and the confirmation that must precede a destructive save.
 *
 * `check_content_parity()` blocks the Oracle when the structured record and the markdown
 * the simulator serves have diverged. Two operations repair that, and **one of them
 * destroys work**:
 *
 * - `PUT .../structured` renders the fields over the markdown. Hand edits are gone.
 * - `POST .../resync` reads the edited markdown back *into* the record, with a model call.
 *
 * Both are correct; they are not interchangeable. ToDos is explicit that they must never
 * be one button and that the structured save needs a confirmation while detached — built
 * before the editor ships rather than after an author loses an evening's markdown.
 *
 * Parity is surfaced continuously rather than at save time, because the alternative is an
 * author discovering it when the Oracle refuses to run.
 */

interface ParityState {
  render_detached?: boolean;
  parity_broken?: boolean;
  parity_reason?: string | null;
  parity_message?: string | null;
}

/** Steady-state banner. Renders nothing when the record and the markdown agree. */
export function ParityBanner({ state }: { state: ParityState | null }) {
  if (!state?.parity_broken) return null;

  const detached = state.parity_reason === 'render_detached';
  return (
    <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
      <p className="font-medium">
        {detached
          ? 'This version’s markdown was hand-edited and no longer matches the structured record.'
          : 'The stored markdown has drifted from the structured record.'}
      </p>
      {state.parity_message && <p className="mt-1">{state.parity_message}</p>}
      <p className="mt-2">
        The Oracle will not run while these disagree — it must rate the case the learner
        actually sees.
      </p>
    </div>
  );
}

/**
 * Blocking confirmation shown when saving structured fields over a detached version.
 *
 * States the consequence in terms of what is lost, not in terms of flag names, and offers
 * the non-destructive alternative in the same breath. An author who wanted `/resync`
 * should be able to reach it from here rather than discovering afterwards that it existed.
 */
export function DetachedSaveConfirm({
  open,
  onConfirm,
  onResync,
  onCancel,
  busy = false,
}: {
  open: boolean;
  onConfirm: () => void;
  onResync?: () => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="detached-save-title"
        className="w-full max-w-lg rounded-lg bg-white p-5 shadow-xl"
      >
        <h2 id="detached-save-title" className="text-lg font-semibold text-slate-900">
          This will discard the hand-edited markdown
        </h2>

        <div className="mt-3 space-y-3 text-sm text-slate-700">
          <p>
            Someone edited this case’s markdown directly. Saving these fields re-renders
            the document from the structured record, so{' '}
            <strong>those markdown edits are replaced and will not be recoverable</strong>{' '}
            from the new version.
          </p>
          <p>
            If you meant to <em>keep</em> the markdown edits, re-sync instead. That reads
            the edited document back into the structured record — the opposite direction —
            and costs one model call.
          </p>
        </div>

        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-40"
          >
            Cancel
          </button>
          {onResync && (
            <button
              type="button"
              onClick={onResync}
              disabled={busy}
              className="rounded border border-slate-400 px-3 py-1.5 text-sm font-medium text-slate-800 hover:bg-slate-100 disabled:opacity-40"
            >
              Re-sync instead (keeps the markdown)
            </button>
          )}
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-40"
          >
            {busy ? 'Saving…' : 'Discard markdown and save'}
          </button>
        </div>
      </div>
    </div>
  );
}
