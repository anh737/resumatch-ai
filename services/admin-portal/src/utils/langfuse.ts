/**
 * Embedded Langfuse dashboard.
 *
 * Langfuse forbids framing (`X-Frame-Options: SAMEORIGIN`, CSP
 * `frame-ancestors 'none'`), so the portal's nginx serves a second listener
 * (host port 3002 by default) that reverse-proxies the Langfuse UI at its root
 * without those headers — see nginx/default.conf.template. The "Langfuse
 * dashboard" page (#/langfuse?path=…) renders that origin in an iframe, and
 * every Langfuse deep link inside the portal is rewritten onto it.
 */
import { routeHref, type Route } from '../hooks/useHashRoute';

const ENV_EMBED_URL = (import.meta.env.VITE_LANGFUSE_EMBED_URL ?? '').trim().replace(/\/+$/, '');
export const EMBED_PORT = '3002';

/** Browser-facing origin of the Langfuse proxy (build-time override or <portal host>:3002). */
export function embedBaseUrl(): string {
  if (ENV_EMBED_URL) return ENV_EMBED_URL;
  return `${window.location.protocol}//${window.location.hostname}:${EMBED_PORT}`;
}

/** Path (+ query / hash) of a Langfuse link, whatever host bot-agent built it with. */
export function langfusePath(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const parsed = new URL(url, window.location.href);
    return `${parsed.pathname}${parsed.search}${parsed.hash}` || '/';
  } catch {
    return undefined;
  }
}

export function embedRoute(url?: string | null): Route {
  return { page: 'langfuse', path: langfusePath(url) };
}

/** Hash href that opens `url` inside the embedded dashboard page. */
export function embedHref(url?: string | null): string {
  return routeHref(embedRoute(url));
}

/** iframe src for a dashboard path. */
export function embedSrc(path?: string): string {
  const clean = path && path.startsWith('/') ? path : '/';
  return `${embedBaseUrl()}${clean}`;
}
