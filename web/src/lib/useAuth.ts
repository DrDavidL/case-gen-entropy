/**
 * React binding for the credential store in `auth.ts`.
 *
 * Separate from `LoginGate.tsx` because a module that exports both a component and a
 * hook breaks Fast Refresh — eslint's `react-refresh/only-export-components` flags it,
 * and the failure mode is a dev server that silently stops hot-reloading.
 */

import { useSyncExternalStore } from 'react';
import { currentUsername, getSnapshot, subscribe } from './auth';

/** Re-renders whenever the stored credential changes, from anywhere in the app. */
export function useAuth(): { token: string | null; username: string | null } {
  const token = useSyncExternalStore(subscribe, getSnapshot);
  return { token, username: token ? currentUsername() : null };
}
