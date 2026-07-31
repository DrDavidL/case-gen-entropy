/**
 * Credential handling for the SPA (ADR-021).
 *
 * HTTP Basic, sent from the app's own login form, rather than a JWT. There is exactly one
 * shared account, so a bearer token would authenticate the same single identity: no
 * per-user attribution, and revoking the only token means rotating the password anyway.
 * JWT remains the target once real accounts exist — see ADR-021 for the revisit triggers.
 *
 * Stored in `sessionStorage`, not `localStorage`, deliberately. The credential is a
 * shared password rather than a revocable token, so it should not outlive the tab. Note
 * that this is not a security boundary against XSS — script running on this page can read
 * either one. It limits how long a credential sits on a shared or unattended machine,
 * which is the realistic risk here.
 */

const STORAGE_KEY = 'casegen.auth';

/** Listeners so React re-renders when auth state changes from anywhere. */
const listeners = new Set<() => void>();

function notify() {
  for (const l of listeners) l();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The stored `user:pass` as base64, or null. */
function readToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    // Safari in private mode can throw on storage access. Treat as logged out rather
    // than crashing the app on load.
    return null;
  }
}

export function getSnapshot(): string | null {
  return readToken();
}

export function isAuthenticated(): boolean {
  return readToken() !== null;
}

/**
 * The Authorization header for an authenticated request, or `{}` when logged out.
 *
 * Returns an object so callers can spread it unconditionally — an authenticated request
 * made while logged out then goes out bare and comes back 401, rather than sending the
 * literal string "undefined".
 */
export function authHeader(): Record<string, string> {
  const token = readToken();
  return token ? { Authorization: `Basic ${token}` } : {};
}

export function storeCredential(username: string, password: string): void {
  // btoa handles latin1 only; encodeURIComponent/unescape widens it to UTF-8 so a
  // non-ASCII password produces a valid header instead of throwing.
  const token = btoa(unescape(encodeURIComponent(`${username}:${password}`)));
  sessionStorage.setItem(STORAGE_KEY, token);
  notify();
}

export function clearCredential(): void {
  sessionStorage.removeItem(STORAGE_KEY);
  notify();
}

/** The logged-in username, decoded from the stored credential. Null when logged out. */
export function currentUsername(): string | null {
  const token = readToken();
  if (!token) return null;
  try {
    const decoded = decodeURIComponent(escape(atob(token)));
    const idx = decoded.indexOf(':');
    return idx === -1 ? decoded : decoded.slice(0, idx);
  } catch {
    return null;
  }
}
