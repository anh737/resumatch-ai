import { useCallback, useEffect, useState } from 'react';
import type { UploadKind } from '../api/admin';

/**
 * Tiny hash router:
 *   #/overview | #/upload/cv | #/upload/jd | #/traces | #/traces/<id>
 *   #/langfuse | #/langfuse?path=<encoded Langfuse path>   (embedded dashboard)
 * Hash routes keep the nginx SPA fallback trivial and make every page (an open
 * trace, a dashboard location) linkable.
 */
export type Route =
  | { page: 'overview' }
  | { page: 'upload'; kind: UploadKind }
  | { page: 'traces'; traceId?: string }
  | { page: 'langfuse'; path?: string };

export function parseRoute(hash: string): Route {
  const [rawPath, rawQuery = ''] = hash.replace(/^#\/?/, '').split('?', 2);
  const path = rawPath.replace(/\/+$/, '');
  if (path === 'upload/cv') return { page: 'upload', kind: 'cv' };
  if (path === 'upload/jd') return { page: 'upload', kind: 'jd' };
  if (path === 'traces') return { page: 'traces' };
  if (path.startsWith('traces/')) return { page: 'traces', traceId: decodeURIComponent(path.slice('traces/'.length)) };
  if (path === 'langfuse') {
    const target = new URLSearchParams(rawQuery).get('path');
    return { page: 'langfuse', path: target && target.startsWith('/') ? target : undefined };
  }
  return { page: 'overview' };
}

export function routeHref(route: Route): string {
  switch (route.page) {
    case 'upload':
      return `#/upload/${route.kind}`;
    case 'traces':
      return route.traceId ? `#/traces/${encodeURIComponent(route.traceId)}` : '#/traces';
    case 'langfuse':
      return route.path ? `#/langfuse?path=${encodeURIComponent(route.path)}` : '#/langfuse';
    default:
      return '#/overview';
  }
}

export function sameRoute(a: Route, b: Route): boolean {
  return routeHref(a) === routeHref(b);
}

export function useHashRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  const navigate = useCallback((next: Route) => {
    const href = routeHref(next);
    if (window.location.hash === href) return;
    window.location.hash = href;
  }, []);

  return [route, navigate];
}
