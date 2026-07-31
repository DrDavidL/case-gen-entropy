/**
 * Row model for the dynamic-list editor.
 *
 * Separate from `ListEditor.tsx` because a module exporting both a component and plain
 * functions breaks Fast Refresh (`react-refresh/only-export-components`), whose failure
 * mode is a dev server that quietly stops hot-reloading.
 *
 * **Rows carry a client-side stable id, never an array index.** Index keys reuse the
 * wrong DOM node when a row is removed or moved, so an author's cursor and in-flight edit
 * land on a different row than the one they were typing in -- the same family of bug as
 * the `sim_image_links` leak in the Streamlit editor (CLAUDE.md). `toApi` strips the id,
 * so it never reaches the database.
 */

export interface Row {
  rid: string;
  a: string;
  b: string;
}

export function newId(): string {
  // randomUUID needs a secure context. Vite dev serves over http on localhost, which
  // counts, but a plain-http LAN preview does not — hence the fallback rather than a
  // crash on `crypto.randomUUID is not a function`.
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `r-${Math.random().toString(36).slice(2)}-${Date.now()}`;
}

/** API rows -> editor rows, assigning ids once at load. */
export function toRows(
  items: unknown,
  aKey: string,
  bKey: string,
): Row[] {
  if (!Array.isArray(items)) return [];
  return items.map((it) => {
    const rec = (it ?? {}) as Record<string, unknown>;
    return {
      rid: newId(),
      a: typeof rec[aKey] === 'string' ? (rec[aKey] as string) : '',
      b: typeof rec[bKey] === 'string' ? (rec[bKey] as string) : '',
    };
  });
}

/** Editor rows -> API rows. Drops the client-side id and any all-blank row. */
export function toApi(rows: Row[], aKey: string, bKey: string): Record<string, string>[] {
  return rows
    .filter((r) => r.a.trim() !== '' || r.b.trim() !== '')
    .map((r) => ({ [aKey]: r.a, [bKey]: r.b }));
}
