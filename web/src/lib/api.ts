/**
 * Backend client.
 *
 * Response shapes come from `types.gen.ts`, generated from the committed `openapi.json`
 * (ADR-020). Do not hand-write an interface for anything the API already describes: the
 * point of generating is that renaming a field server-side becomes a build error here
 * rather than a form input that silently stops being populated.
 *
 * Reads are unauthenticated by design (`/sim-ready/cases`, `/sim-ready/case/{id}`,
 * `/sim-ready/case/{id}/structured`, `/sim-ready/case/{id}/analysis`, `/`). Writes send
 * HTTP Basic from the app's own login form — see `auth.ts` and ADR-021 for why that is
 * Basic rather than the JWT ADR-020 originally specified. HTTP Basic stays live on the
 * backend until Phase 4e regardless, because the Streamlit UI authenticates with it.
 */

import { authHeader, clearCredential } from './auth';
import type { paths } from './types.gen';

/** Relative in dev (vite proxies to :8000) and in production (FastAPI serves this SPA). */
const BASE = import.meta.env.VITE_BACKEND_URL ?? '';

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  // Fields assigned in the body rather than declared as constructor parameter
  // properties: tsconfig sets `erasableSyntaxOnly`, which forbids TypeScript syntax that
  // emits runtime code.
  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

interface RequestOptions extends RequestInit {
  /** Send the stored Basic credential. Omit for the unauthenticated read endpoints. */
  auth?: boolean;
}

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const { auth = false, ...rest } = init ?? {};
  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    headers: {
      'Content-Type': 'application/json',
      ...(auth ? authHeader() : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    // A rejected credential on an authenticated call means the stored one is no longer
    // good — the password was rotated, or it was wrong all along. Drop it so the app
    // falls back to the login form instead of retrying a credential that cannot work.
    // Only on authenticated calls: a 401 from elsewhere says nothing about what we hold.
    if (res.status === 401 && auth) clearCredential();
    // FastAPI puts the message in `detail`, but an ingress or proxy error will not be
    // JSON at all. Falling back to the status text keeps a 502 from surfacing as an
    // unrelated JSON parse error.
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

type Ok<T> = T extends { responses: { 200: { content: { 'application/json': infer R } } } }
  ? R
  : never;

export type BuildInfo = Ok<paths['/']['get']>;
export type SimReadyCase = Ok<paths['/sim-ready/case/{case_id}']['get']>;
export type StructuredRecord = Ok<
  paths['/sim-ready/case/{case_id}/structured']['get']
>;
export type CaseAnalysis = Ok<paths['/sim-ready/case/{case_id}/analysis']['get']>;
export type AuthCheck = Ok<paths['/auth/check']['get']>;
export type RenderPreview =
  paths['/sim-ready/render-preview']['post'] extends {
    responses: { 200: { content: { 'application/json': infer R } } };
  }
    ? R
    : never;
/** The structured record's shape, as the save and preview endpoints accept it. */
export type StructuredContent =
  paths['/sim-ready/render-preview']['post'] extends {
    requestBody: { content: { 'application/json': { content_structured: infer S } } };
  }
    ? S
    : never;

/** One row of `GET /sim-ready/cases`. */
export interface CaseListItem {
  id: number;
  saved_name: string | null;
  allow_orders?: boolean | null;
}

export const getBuildInfo = () => request<BuildInfo>('/');

export const listCases = () => request<CaseListItem[]>('/sim-ready/cases');

export const getCase = (id: number) =>
  request<SimReadyCase>(`/sim-ready/case/${id}`);

/**
 * The canonical structured record. 404 is expected and not an error condition: cases
 * predating the authoring record have none until they are adopted (ADR-019), and 503
 * means the backend has no authoring schema. Both return null so a caller can render
 * the case without it.
 */
export async function getStructured(id: number): Promise<StructuredRecord | null> {
  try {
    return await request<StructuredRecord>(`/sim-ready/case/${id}/structured`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 503)) return null;
    throw e;
  }
}

/** Framework + LR data. Null for the same reasons as `getStructured`. */
export async function getAnalysis(id: number): Promise<CaseAnalysis | null> {
  try {
    return await request<CaseAnalysis>(`/sim-ready/case/${id}/analysis`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 503)) return null;
    throw e;
  }
}

/**
 * Validate a credential without storing it.
 *
 * Returns false on 401 and throws on anything else, so a wrong password and an
 * unreachable backend produce different outcomes at the login form. Reporting a network
 * failure as "wrong password" would send an author hunting for a credential problem that
 * does not exist.
 */
export async function checkCredential(
  username: string,
  password: string,
): Promise<boolean> {
  const token = btoa(unescape(encodeURIComponent(`${username}:${password}`)));
  const res = await fetch(`${BASE}/auth/check`, {
    headers: { Authorization: `Basic ${token}` },
  });
  if (res.status === 401) return false;
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return true;
}

/**
 * Render a structured record to markdown without saving.
 *
 * Server-side so one renderer stays authoritative: this is the same function the save
 * path calls, so the preview is byte-for-byte what a save would store.
 */
export const renderPreview = (contentStructured: StructuredContent) =>
  request<RenderPreview>('/sim-ready/render-preview', {
    method: 'POST',
    body: JSON.stringify({ content_structured: contentStructured }),
  });

/**
 * Save structured fields. Always writes a new version (ADR-002, ADR-003).
 *
 * **This overwrites hand-edited markdown.** If the current version is detached
 * (`parity_broken` with reason `render_detached`), the author's markdown edits are
 * replaced by the render of these fields and are not recoverable from the new version.
 * `POST .../resync` is the opposite operation — it folds hand edits *into* the record.
 * The two are not interchangeable and must never be presented as one button.
 */
export const saveStructured = (
  caseId: number,
  body: { content_structured: StructuredContent } & Record<string, unknown>,
) =>
  request<unknown>(`/sim-ready/case/${caseId}/structured`, {
    method: 'PUT',
    auth: true,
    body: JSON.stringify(body),
  });
