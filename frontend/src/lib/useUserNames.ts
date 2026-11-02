import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { itemsOf } from '../api/client';

/** id → name map from /users (backend deals/reports carry only ids). */
export function useUserNames(): (id?: number | null, fallback?: string | null) => string {
  const { data } = useQuery({ queryKey: ['users', 'names'], queryFn: () => api.users.list({}), staleTime: 300_000 });
  const map = new Map(itemsOf(data).map((u) => [u.id, u.name]));
  return (id, fallback) => fallback || (id ? map.get(id) || `#${id}` : '—');
}

/** dashboard_metrics setting: backend stores a JSON array of enabled keys; mock may store a record. */
export function parseMetrics(v: unknown): Record<string, boolean> | null {
  if (!v) return null;
  let x = v;
  if (typeof x === 'string') { try { x = JSON.parse(x); } catch { return null; } }
  if (Array.isArray(x)) return Object.fromEntries(x.map((k) => [String(k), true]));
  if (typeof x === 'object') return x as Record<string, boolean>;
  return null;
}
