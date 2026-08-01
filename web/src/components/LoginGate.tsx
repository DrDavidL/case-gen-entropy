/**
 * Login form and session banner (ADR-021).
 *
 * Not a route guard. Reads are unauthenticated, so the case list and case view stay
 * usable logged out; this only gates the actions that write. Wrapping the whole app in a
 * login wall would be a worse fit for how the API is actually scoped.
 */

import { useCallback, useState } from 'react';
import { checkCredential } from '../lib/api';
import { clearCredential, storeCredential } from '../lib/auth';
import { useAuth } from '../lib/useAuth';

export default function LoginGate() {
  const { username } = useAuth();
  const [user, setUser] = useState('');
  const [pass, setPass] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setBusy(true);
      setError(null);
      try {
        const ok = await checkCredential(user, pass);
        if (ok) {
          storeCredential(user, pass);
          setUser('');
          setPass('');
        } else {
          setError('Those credentials were rejected.');
        }
      } catch {
        // Distinguished from a rejected credential on purpose: telling an author their
        // password is wrong when the backend is simply unreachable sends them hunting
        // for a problem that does not exist.
        setError('Could not reach the backend. Check the connection and try again.');
      } finally {
        setBusy(false);
      }
    },
    [user, pass],
  );

  if (username) {
    return (
      <div className="flex items-center gap-3 text-sm">
        <span className="text-ink-600">
          Signed in as <span className="font-medium">{username}</span>
        </span>
        <button
          type="button"
          onClick={clearCredential}
          className="btn btn-secondary btn-sm"
        >
          Sign out
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="flex items-center gap-2 text-sm">
      <input
        aria-label="Username"
        placeholder="username"
        value={user}
        onChange={(e) => setUser(e.target.value)}
        autoComplete="username"
        className="input w-28"
      />
      <input
        aria-label="Password"
        placeholder="password"
        type="password"
        value={pass}
        onChange={(e) => setPass(e.target.value)}
        autoComplete="current-password"
        className="input w-32"
      />
      <button
        type="submit"
        disabled={busy || !user || !pass}
        className="btn btn-primary btn-sm"
      >
        {busy ? 'Checking…' : 'Sign in'}
      </button>
      {error && <span className="text-red-700">{error}</span>}
    </form>
  );
}
