/**
 * Gate for routes that read case content.
 *
 * The case endpoints require a credential now, so without this every signed-out visit
 * would render a wall of 401 error text. This asks for a sign-in instead.
 *
 * It is a UI convenience, not the security boundary — the boundary is
 * `verify_credentials` on the endpoints themselves. Anyone can still bypass this
 * component; they cannot bypass the API.
 */

import type { ReactNode } from 'react';
import { useAuth } from '../lib/useAuth';

export default function RequireAuth({ children }: { children: ReactNode }) {
  const { username } = useAuth();
  if (username) return <>{children}</>;

  return (
    <div className="mx-auto max-w-md py-12 text-center">
      <div className="card p-6">
        <h2 className="text-base font-semibold text-ink-900">Sign in to view cases</h2>
        <p className="mt-2 text-sm text-ink-600">
          Case content is not public. Use the sign-in fields at the top right.
        </p>
      </div>
    </div>
  );
}
