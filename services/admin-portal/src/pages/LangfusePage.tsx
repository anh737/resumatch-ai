import { useEffect, useState } from 'react';
import { ExternalLink, Home, RefreshCw } from 'lucide-react';
import { routeHref } from '../hooks/useHashRoute';
import { embedBaseUrl, embedSrc } from '../utils/langfuse';

interface Props {
  /** Dashboard path to show (e.g. /project/<id>/traces/<trace>); root when undefined. */
  path?: string;
  /** Langfuse's own public URL (bot-agent's LANGFUSE_PUBLIC_URL), for the new-tab fallback. */
  publicUrl: string | null;
}

/**
 * The Langfuse UI itself, framed from the portal's proxy origin (see
 * utils/langfuse.ts). Cross-origin, so nothing inside the frame can be read
 * here — the toolbar only offers navigation, reload and an escape hatch.
 */
export default function LangfusePage({ path, publicUrl }: Props) {
  const [reloadKey, setReloadKey] = useState(0);
  const src = embedSrc(path);
  const newTab = publicUrl ? `${publicUrl.replace(/\/+$/, '')}${path ?? '/'}` : src;

  // A new dashboard location must remount the frame (same src key otherwise).
  useEffect(() => setReloadKey((k) => k + 1), [src]);

  return (
    <div className="lf">
      <div className="lf__bar">
        <a className="btn btn--sm" href={routeHref({ page: 'langfuse' })} title="Dashboard home">
          <Home size={14} /> Home
        </a>
        <span className="lf__path" title={src}>
          {embedBaseUrl()}
          <strong>{path ?? '/'}</strong>
        </span>
        <button className="btn btn--sm" onClick={() => setReloadKey((k) => k + 1)} title="Reload the frame">
          <RefreshCw size={14} /> Reload
        </button>
        <a className="icon-btn icon-btn--sm" href={newTab} target="_blank" rel="noreferrer" title="Open this view in a new tab">
          <ExternalLink size={14} />
        </a>
      </div>
      <iframe key={reloadKey} className="lf__frame" src={src} title="Langfuse dashboard" allow="clipboard-write" />
      <div className="lf__hint">
        Served through the portal's proxy (<code>{embedBaseUrl()}</code>) because Langfuse refuses to be framed directly. Your
        Langfuse session is shared with <code>{publicUrl ?? 'http://localhost:3031'}</code>; if you sign in inside the frame and it
        goes blank afterwards, press <strong>Reload</strong>.
      </div>
    </div>
  );
}
