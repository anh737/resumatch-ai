import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, Gauge, X } from 'lucide-react';
import { embedHref } from '../utils/langfuse';
import { AdminApiError, getTrace, type ObservationRow, type TraceDetail } from '../api/admin';
import { formatCost, formatDuration, formatTokens, formatWhen, pretty, shortId } from '../utils/format';

interface Props {
  traceId: string;
  onClose: () => void;
}

interface TreeNode {
  row: ObservationRow;
  depth: number;
}

/** Flatten the observation forest (parent -> children) into render order with depth. */
function flatten(observations: ObservationRow[]): TreeNode[] {
  const ids = new Set(observations.map((o) => o.id));
  const children = new Map<string | null, ObservationRow[]>();
  for (const obs of observations) {
    const parent = obs.parent_id && ids.has(obs.parent_id) ? obs.parent_id : null;
    const list = children.get(parent) ?? [];
    list.push(obs);
    children.set(parent, list);
  }
  const byTime = (a: ObservationRow, b: ObservationRow) => (a.start_time ?? '').localeCompare(b.start_time ?? '');
  const out: TreeNode[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const row of (children.get(parent) ?? []).sort(byTime)) {
      out.push({ row, depth });
      walk(row.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  const [open, setOpen] = useState(false);
  const text = pretty(value);
  if (!text) return null;
  return (
    <div className="json">
      <button className="json__toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>{label}</span>
        <span className="json__size">{text.length.toLocaleString()} chars</span>
      </button>
      {open && <pre className="json__body">{text}</pre>}
    </div>
  );
}

function ObservationItem({ node }: { node: TreeNode }) {
  const { row, depth } = node;
  const [open, setOpen] = useState(false);
  const type = row.type.toLowerCase();
  const bad = row.level === 'ERROR' || row.level === 'WARNING';
  const hasIO = row.input != null || row.output != null;

  return (
    <li className="obs" style={{ ['--depth' as string]: depth }}>
      <button className={`obs__row${open ? ' obs__row--open' : ''}`} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className={`obs__type obs__type--${type}`}>{type}</span>
        <span className="obs__name">{row.name ?? shortId(row.id)}</span>
        {row.model && <span className="obs__meta">{row.model}</span>}
        {row.total_tokens != null && (
          <span className="obs__meta" title="input / output tokens">
            {formatTokens(row.input_tokens)} → {formatTokens(row.output_tokens)} tok
          </span>
        )}
        {row.total_cost != null && <span className="obs__meta">{formatCost(row.total_cost)}</span>}
        {bad && (
          <span className={`obs__level obs__level--${row.level?.toLowerCase()}`}>
            <AlertTriangle size={12} /> {row.level}
          </span>
        )}
        <span className="obs__latency">{formatDuration(row.latency_seconds)}</span>
        <span className="obs__chevron">{hasIO ? open ? <ChevronDown size={14} /> : <ChevronRight size={14} /> : null}</span>
      </button>
      {row.status_message && <div className="obs__status">{row.status_message}</div>}
      {open && hasIO && (
        <div className="obs__io">
          <JsonBlock label="Input" value={row.input} />
          <JsonBlock label="Output" value={row.output} />
        </div>
      )}
    </li>
  );
}

export default function TraceDrawer({ traceId, onClose }: Props) {
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setDetail(null);
    getTrace(traceId, controller.signal)
      .then(setDetail)
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === 'AbortError') return;
        setError(err instanceof AdminApiError || err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [traceId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const nodes = useMemo(() => (detail ? flatten(detail.observations) : []), [detail]);
  const generations = detail ? detail.observations.filter((o) => o.type.toUpperCase() === 'GENERATION') : [];
  const totalTokens = generations.reduce((sum, g) => sum + (g.total_tokens ?? 0), 0);
  const errors = detail ? detail.observations.filter((o) => o.level === 'ERROR').length : 0;

  return (
    <div className="drawer-backdrop" onMouseDown={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Trace details" onMouseDown={(e) => e.stopPropagation()}>
        <div className="drawer__head">
          <div className="drawer__titles">
            <div className="drawer__eyebrow">Langfuse trace</div>
            <h2 className="drawer__title">{detail?.name ?? shortId(traceId, 12)}</h2>
            <div className="drawer__id" title={traceId}>
              {traceId}
            </div>
          </div>
          <div className="drawer__actions">
            {detail && (
              <a className="btn btn--sm" href={embedHref(detail.url)} title="Open this trace in the embedded Langfuse dashboard">
                <Gauge size={14} /> Open in Langfuse
              </a>
            )}
            <button className="icon-btn" onClick={onClose} aria-label="Close">
              <X size={20} />
            </button>
          </div>
        </div>

        <div className="drawer__body">
          {loading && <div className="empty">Loading trace…</div>}
          {error && <div className="alert alert--error">{error}</div>}

          {detail && (
            <>
              <dl className="kv">
                <div>
                  <dt>When</dt>
                  <dd>{formatWhen(detail.timestamp)}</dd>
                </div>
                <div>
                  <dt>Latency</dt>
                  <dd>{formatDuration(detail.latency_seconds)}</dd>
                </div>
                <div>
                  <dt>Cost</dt>
                  <dd>{formatCost(detail.total_cost)}</dd>
                </div>
                <div>
                  <dt>LLM calls</dt>
                  <dd>
                    {generations.length}
                    {totalTokens > 0 && <span className="muted"> · {formatTokens(totalTokens)} tok</span>}
                  </dd>
                </div>
                <div>
                  <dt>Session</dt>
                  <dd className="mono" title={detail.session_id ?? undefined}>
                    {detail.session_id ? shortId(detail.session_id, 18) : '—'}
                  </dd>
                </div>
                <div>
                  <dt>Environment</dt>
                  <dd>{detail.environment ?? '—'}</dd>
                </div>
              </dl>

              {(detail.tags.length > 0 || errors > 0) && (
                <div className="chips">
                  {detail.tags.map((tag) => (
                    <span key={tag} className="chip chip--static">
                      {tag}
                    </span>
                  ))}
                  {errors > 0 && (
                    <span className="chip chip--static chip--error">
                      <AlertTriangle size={12} /> {errors} error{errors > 1 ? 's' : ''}
                    </span>
                  )}
                </div>
              )}

              {detail.metadata && Object.keys(detail.metadata).length > 0 && (
                <dl className="kv kv--dense">
                  {Object.entries(detail.metadata).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd className="mono">{pretty(value)}</dd>
                    </div>
                  ))}
                </dl>
              )}

              <section className="drawer__section">
                <h3 className="drawer__h">Trace input / output</h3>
                <JsonBlock label="Input" value={detail.input} />
                <JsonBlock label="Output" value={detail.output} />
                {detail.input == null && detail.output == null && <div className="muted">Not recorded.</div>}
              </section>

              <section className="drawer__section">
                <h3 className="drawer__h">
                  Observations <span className="muted">({detail.observations.length})</span>
                </h3>
                {nodes.length === 0 ? (
                  <div className="muted">No observations on this trace.</div>
                ) : (
                  <ul className="obs-list">
                    {nodes.map((node) => (
                      <ObservationItem key={node.row.id} node={node} />
                    ))}
                  </ul>
                )}
              </section>

              {detail.scores.length > 0 && (
                <section className="drawer__section">
                  <h3 className="drawer__h">Scores</h3>
                  <ul className="scores">
                    {detail.scores.map((score, i) => (
                      <li key={`${score.name}-${i}`}>
                        <span className="scores__name">{score.name}</span>
                        <span className="scores__value">{score.value ?? '—'}</span>
                        {score.comment && <span className="muted">{score.comment}</span>}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
