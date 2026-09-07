import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, ChevronLeft, ChevronRight, Gauge, RefreshCw, ScanSearch, Search, X } from 'lucide-react';
import { routeHref } from '../hooks/useHashRoute';
import { embedHref } from '../utils/langfuse';
import { listTraces, type TracesResponse } from '../api/admin';
import { usePolling } from '../hooks/usePolling';
import { formatCost, formatDuration, formatRelative, formatWhen } from '../utils/format';

type Scope = 'all' | 'ingestion' | 'chat';

const SCOPES: { key: Scope; label: string; hint: string }[] = [
  { key: 'all', label: 'All traces', hint: 'everything the services report' },
  { key: 'ingestion', label: 'Ingestion', hint: 'ingest-cv / ingest-jd (tag: ingestion)' },
  { key: 'chat', label: 'Chat', hint: 'one trace per chat turn (name: chat)' },
];

const PAGE_SIZE = 20;

interface Props {
  onOpenTrace: (traceId: string) => void;
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

export default function TracesPage({ onOpenTrace }: Props) {
  const [data, setData] = useState<TracesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [scope, setScope] = useState<Scope>('all');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const name = useDebounced(search.trim(), 350);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const response = await listTraces({
        limit: PAGE_SIZE,
        page,
        tags: scope === 'ingestion' ? ['ingestion'] : undefined,
        name: name || (scope === 'chat' ? 'chat' : undefined),
      });
      setData(response);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [page, scope, name]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    setPage(1);
  }, [scope, name]);

  usePolling(refresh, 20000, page === 1);

  const summary = useMemo(() => {
    const items = data?.items ?? [];
    const cost = items.reduce((sum, t) => sum + (t.total_cost ?? 0), 0);
    const latencies = items.map((t) => t.latency_seconds).filter((l): l is number => l != null);
    const avg = latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : null;
    return { count: items.length, cost, avg };
  }, [data]);

  const configured = data?.configured ?? true;
  const totalPages = data?.total_pages ?? null;

  return (
    <div className="page">
      <div className="page__inner">
        <section className="section">
          <div className="intro">
            <div className="intro__text">
              <h2 className="section__title">
                <Activity size={18} /> Observability (Langfuse)
              </h2>
              <p className="muted">
                Every chat turn and every document ingestion is one Langfuse trace. bot-agent proxies the Langfuse public API, so the
                keys never reach the browser; open a trace here to see its LLM calls, spans, token usage and errors, or switch to
                the embedded Langfuse dashboard for the full picture.
              </p>
            </div>
            <a className="btn" href={routeHref({ page: 'langfuse' })}>
              <Gauge size={14} /> Langfuse dashboard
            </a>
          </div>

          {data && !configured && (
            <div className="alert alert--warn">
              <strong>Langfuse is not configured on bot-agent.</strong> Create API keys in the Langfuse UI (project settings) and set{' '}
              <code>LANGFUSE_PUBLIC_KEY</code> / <code>LANGFUSE_SECRET_KEY</code> in <code>services/bot-agent/.env</code>, then restart
              bot-agent. ai-agents and ai-embeddings need the same keys to emit traces.
            </div>
          )}
          {(error || data?.error) && <div className="alert alert--error">{error ?? data?.error}</div>}
        </section>

        {configured && (
          <section className="section">
            <div className="section__head section__head--wrap">
              <div className="chips">
                {SCOPES.map(({ key, label, hint }) => (
                  <button key={key} className={`chip${scope === key ? ' chip--active' : ''}`} onClick={() => setScope(key)} title={hint}>
                    {label}
                  </button>
                ))}
              </div>
              <div className="section__actions">
                <label className="search">
                  <Search size={14} />
                  <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Trace name…" />
                  {search && (
                    <button className="icon-btn icon-btn--sm" onClick={() => setSearch('')} aria-label="Clear">
                      <X size={12} />
                    </button>
                  )}
                </label>
                <button className="icon-btn" onClick={() => void refresh()} title="Refresh" aria-label="Refresh traces">
                  <RefreshCw size={16} className={loading ? 'spin' : undefined} />
                </button>
              </div>
            </div>

            <div className="summary">
              <span>
                <strong>{data?.total ?? summary.count}</strong> trace{(data?.total ?? summary.count) === 1 ? '' : 's'}
                {data?.total != null && data.total > summary.count && <span className="muted"> · {summary.count} on this page</span>}
              </span>
              <span>
                page cost <strong>{formatCost(summary.cost)}</strong>
              </span>
              <span>
                avg latency <strong>{formatDuration(summary.avg)}</strong>
              </span>
            </div>

            {data && data.items.length === 0 && !data.error ? (
              <div className="empty">No traces match — chat with the bot or upload a document to create some.</div>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Trace</th>
                      <th>Session</th>
                      <th>Tags</th>
                      <th>Env</th>
                      <th>Latency</th>
                      <th>Cost</th>
                      <th>When</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data?.items ?? []).map((trace) => (
                      <tr key={trace.id}>
                        <td>
                          <button className="link-btn" onClick={() => onOpenTrace(trace.id)} title={trace.id}>
                            {trace.name ?? trace.id.slice(0, 8)}
                          </button>
                        </td>
                        <td className="mono cell-clip" title={trace.session_id ?? undefined}>
                          {trace.session_id ?? '—'}
                        </td>
                        <td>
                          {trace.tags.length ? (
                            <span className="tags">
                              {trace.tags.map((tag) => (
                                <span key={tag} className="tag">
                                  {tag}
                                </span>
                              ))}
                            </span>
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                        <td>{trace.environment ?? <span className="muted">—</span>}</td>
                        <td>{formatDuration(trace.latency_seconds)}</td>
                        <td>{formatCost(trace.total_cost)}</td>
                        <td title={formatWhen(trace.timestamp)}>{formatRelative(trace.timestamp)}</td>
                        <td>
                          <div className="cell-actions">
                            <button className="btn btn--sm" onClick={() => onOpenTrace(trace.id)}>
                              <ScanSearch size={14} />
                              Details
                            </button>
                            <a className="icon-btn icon-btn--sm" href={embedHref(trace.url)} title="Open in the Langfuse dashboard">
                              <Gauge size={14} />
                            </a>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="pager">
              <button className="btn btn--sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1 || loading}>
                <ChevronLeft size={14} /> Previous
              </button>
              <span className="muted">
                Page {page}
                {totalPages != null && ` of ${totalPages}`}
              </span>
              <button
                className="btn btn--sm"
                onClick={() => setPage((p) => p + 1)}
                disabled={loading || (totalPages != null ? page >= totalPages : (data?.items.length ?? 0) < PAGE_SIZE)}
              >
                Next <ChevronRight size={14} />
              </button>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
