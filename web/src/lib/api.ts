/**
 * Backend client.
 *
 * Response shapes come from `types.gen.ts`, generated from the committed `openapi.json`
 * (ADR-020). Do not hand-write an interface for anything the API already describes: the
 * point of generating is that renaming a field server-side becomes a build error here
 * rather than a form input that silently stops being populated.
 *
 * Case reads now require a credential too, so case content is not browsable by anyone
 * with the URL. `GET /` (the build stamp) and `/sim-ready/case/{id}/final-orders` stay
 * open deliberately — the first is the deploy health check, the second is the contract
 * the simulator will read (direct-sim/FINAL_ORDERS_TODO.md). Writes send
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

export const listCases = () => request<CaseListItem[]>('/sim-ready/cases', { auth: true });

export const getCase = (id: number) =>
  request<SimReadyCase>(`/sim-ready/case/${id}`, { auth: true });

/**
 * The canonical structured record. 404 is expected and not an error condition: cases
 * predating the authoring record have none until they are adopted (ADR-019), and 503
 * means the backend has no authoring schema. Both return null so a caller can render
 * the case without it.
 */
export async function getStructured(id: number): Promise<StructuredRecord | null> {
  try {
    return await request<StructuredRecord>(`/sim-ready/case/${id}/structured`, { auth: true });
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 503)) return null;
    throw e;
  }
}

/** Framework + LR data. Null for the same reasons as `getStructured`. */
export async function getAnalysis(id: number): Promise<CaseAnalysis | null> {
  try {
    return await request<CaseAnalysis>(`/sim-ready/case/${id}/analysis`, { auth: true });
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

// --- Case generation (Phase 4d) ---------------------------------------------

type Body<T> = T extends { requestBody: { content: { 'application/json': infer B } } }
  ? B
  : never;

export type CaseInput = Body<paths['/preview-case']['post']>;
export type CasePreview = Ok<paths['/preview-case']['post']>;
export type FinalizeResult = Ok<paths['/finalize-case']['post']>;
export type CaseSaveBody = Body<paths['/finalize-case']['post']>;

/**
 * Generate a case for review. Writes nothing to the database.
 *
 * Three sequential LLM calls (details -> framework -> likelihood ratios), so this takes
 * roughly a minute. The result lives in a Redis session with a 1-hour TTL and is only
 * persisted by `finalizeCase`.
 */
export const previewCase = (input: CaseInput) =>
  request<CasePreview>('/preview-case', {
    method: 'POST',
    auth: true,
    body: JSON.stringify(input),
  });

/** Persist a previewed case. Returns the new `case_id`. */
export const finalizeCase = (body: CaseSaveBody) =>
  request<FinalizeResult>('/finalize-case', {
    method: 'POST',
    auth: true,
    body: JSON.stringify(body),
  });

/**
 * The sim-ready shape of a preview.
 *
 * `/preview-case` is typed as a union because the endpoint still serves the beta format,
 * whose response lacks `rendered_content` and the simulator defaults. Narrowing rather
 * than casting means that if the beta branch is ever removed — or its shape changes —
 * this stops compiling instead of reading `undefined` at runtime.
 */
export type SimReadyPreview = Extract<CasePreview, { rendered_content: string }>;

export function isSimReadyPreview(p: CasePreview): p is SimReadyPreview {
  return 'rendered_content' in p;
}

// --- Final Orders and the Oracle (Phase 4d) ---------------------------------

export type FinalOrders = Ok<paths['/sim-ready/case/{case_id}/final-orders']['get']>;
export type FinalOrdersUpdate = Ok<paths['/sim-ready/case/{case_id}/final-orders']['put']>;
export type OraclePreflight = Ok<
  paths['/sim-ready/case/{case_id}/oracle/preflight']['get']
>;
export type OracleResults = Ok<paths['/sim-ready/case/{case_id}/oracle']['get']>;
export type ProposedOrders = Ok<paths['/final-orders/propose']['post']>;

/** Unauthenticated — this is the shape the simulator reads. */
export const getFinalOrders = (caseId: number) =>
  request<FinalOrders>(`/sim-ready/case/${caseId}/final-orders`);

/**
 * Replace the case's Final Orders.
 *
 * Replace semantics: the submitted list is the authoritative statement of what the case
 * has. Rows are reconciled by order text server-side, so editing or reordering keeps
 * existing panel runs attached — but **removing a rated order detaches its runs**, which
 * comes back in `detached_panel_runs` and must be surfaced, not swallowed.
 */
export const putFinalOrders = (
  caseId: number,
  body: { final_orders: unknown[]; oracle_specialty?: string | null; run_oracle?: boolean },
) =>
  request<FinalOrdersUpdate>(`/sim-ready/case/${caseId}/final-orders`, {
    method: 'PUT',
    auth: true,
    body: JSON.stringify(body),
  });

/**
 * Candidate orders from the generator. Writes nothing; the author accepts explicitly.
 *
 * Takes the case record rather than a case id: the endpoint wants `session_id` OR
 * `case_details` + `primary_diagnosis`, and a saved case has no live session. It derives
 * candidates from `diagnostic_workup`.
 */
export const proposeFinalOrders = (
  caseDetails: unknown,
  primaryDiagnosis: string,
) =>
  request<ProposedOrders>('/final-orders/propose', {
    method: 'POST',
    auth: true,
    body: JSON.stringify({
      case_details: caseDetails,
      primary_diagnosis: primaryDiagnosis,
    }),
  });

/** Everything checkable before spending a model call. Costs nothing. */
export const getOraclePreflight = (caseId: number) =>
  request<OraclePreflight>(`/sim-ready/case/${caseId}/oracle/preflight`, { auth: true });

/** Queue the panel. 15 calls per Final Order; poll `getOracle` for the result. */
export const runOracle = (caseId: number, leakOverrideReason?: string) =>
  request<unknown>(`/sim-ready/case/${caseId}/oracle/run`, {
    method: 'POST',
    auth: true,
    body: JSON.stringify(
      leakOverrideReason ? { leak_override_reason: leakOverrideReason } : {},
    ),
  });

export const getOracle = (caseId: number) =>
  request<OracleResults>(`/sim-ready/case/${caseId}/oracle`, { auth: true });

/**
 * Edit likelihood ratios and tier priors in place (ADR-007).
 *
 * **Does not create a new version.** LRs are authoring analysis, not learner-facing
 * content, so versioning each tweak would add lineage noise without protecting a learner
 * run. Rows whose value actually changed are stamped `author_overridden` server-side —
 * re-saving an untouched form relabels nothing.
 */
export const updateAnalysis = (
  caseId: number,
  body: {
    feature_likelihood_ratios?: { id: number; likelihood_ratio: number }[];
    diagnostic_framework?: {
      tier_level: number;
      a_priori_probabilities: Record<string, number>;
    }[];
  },
) =>
  request<CaseAnalysis>(`/sim-ready/case/${caseId}/analysis`, {
    method: 'PUT',
    auth: true,
    body: JSON.stringify(body),
  });
