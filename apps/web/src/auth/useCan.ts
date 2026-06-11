/**
 * Hook helpers for role-based UI gating.
 *
 *   const canDelete = useCan('delete', 'export');
 *   if (!canDelete) return null;   // hide the button entirely
 *
 *   const canSeeAgents = useCanSeeNav('agents');
 */
import { useAuth } from './AuthContext';
import {
  type Action,
  type NavKey,
  type Resource,
  can,
  canSeeNav,
} from './permissions';

export function useCan(action: Action, resource: Resource): boolean {
  const { user, disabled } = useAuth();
  if (disabled) return true; // local-dev mode = full access
  if (!user) return false;
  return can(user.roles, action, resource);
}

export function useCanSeeNav(key: NavKey): boolean {
  const { user, disabled } = useAuth();
  if (disabled) return true;
  if (!user) return false;
  return canSeeNav(user.roles, key);
}
