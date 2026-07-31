/**
 * Backend client.
 *
 * Response shapes come from `types.gen.ts`, generated from the committed `openapi.json`
 * (ADR-020). Do not hand-write an interface for anything the API already describes: the
 * point of generating is that renaming a field server-side becomes a build error here
 * rather than a form input that silently stops being populated.
 *
 * No auth yet. Every endpoint this file touches is unauthenticated by design
 * (`/sim-ready/cases`, `/sim-ready/case/{id}`, `/sim-ready/case/{id}/structured`,
 * `/sim-ready/case/{id}/analysis`, `/`). Editing needs a token and arrives with the
 * editor in Phase 4c, alongside the backend's JWT endpoint. Note that HTTP Basic stays
 * live until Phase 4e, because the Streamlit UI authenticates with it.
 */

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) {
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
