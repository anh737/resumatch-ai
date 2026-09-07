import { useCallback, useEffect, useState } from 'react';
import { ArrowRight, Briefcase, Database, FileText, Gauge, RefreshCw, Server } from 'lucide-react';
import {
  getHealth,
  getUploadStats,
  listTraces,
  listUploads,
  type HealthResponse,
  type TraceRow,
  type UploadRow,
  type UploadStats,
} from '../api/admin';
import UploadsTable from '../components/UploadsTable';
import { usePolling } from '../hooks/usePolling';
import { routeHref } from '../hooks/useHashRoute';
import { formatCost, formatDuration, formatRelative, formatWhen } from '../utils/format';
import { embedHref } from '../utils/langfuse';

interface Props {
  onOpenTrace: (traceId: string) => void;
}

const DEPENDENCIES: { key: keyof HealthResponse; label: string; role: string }[] = [
  { key: 'postgres', label: 'PostgreSQL', role: 'upload rows, chat history' },
  { key: 'kafka', label: 'Kafka', role: 'resume.uploaded / job.uploaded events' },
  { key: 'minio', label: 'MinIO', role: 'raw CV / JD files' },
  { key: 'langfuse', label: 'Langfuse', role: 'traces (keys on bot-agent)' },
];

const PIPELINE = [
  { name: 'Admin portal', detail: 'POST /admin/uploads' },
  { name: 'bot-agent', detail: 'store file in MinIO, insert row, produce event' },
  { name: 'Kafka', detail: 'resume.uploaded / job.uploaded (key = upload id)' },
  { name: 'ai-embeddings', detail: 'MinIO → extract → gpt-4.1 structure → chunk → embed' },
  { name: 'Qdrant', detail: 'cv_information_technology / jd_jobs' },
];

function StatCard({ label, value, sub, tone }: { label: string; value: number | string; sub?: string; tone?: 'ok' | 'warn' | 'bad' }) {
  return (
    <div className={`stat${tone ? ` stat--${tone}` : ''}`}>
      <div className="stat__label">{label}</div>
      <div className="stat__value">{value}</div>
      {sub && <div className="stat__sub">{sub}</div>}
    </div>
  );
}

export default function OverviewPage({ onOpenTrace }: Props) {
  const [stats, setStats] = useState<UploadStats | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<UploadRow[]>([]);
  const [traces, setTraces] = useState<TraceRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [nextStats, nextUploads] = await Promise.all([getUploadStats(), listUploads({ limit: 6 })]);
      setStats(nextStats);
      setUploads(nextUploads);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  }, []);

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await getHealth());
      setHealthError(null);
    } catch (err) {
      setHealth(null);
      setHealthError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const refreshTraces = useCallback(async () => {
    try {
      const response = await listTraces({ limit: 5 });
      setTraces(response.configured && !response.error ? response.items : null);
    } catch {
      setTraces(null);
    }
  }, []);

  useEffect(() => {
    void refresh();
    void refreshHealth();
    void refreshTraces();
  }, [refresh, refreshHealth, refreshTraces]);

  const pending = (stats?.all.queued ?? 0) + (stats?.all.processing ?? 0);
  usePolling(refresh, pending > 0 ? 4000 : 15000);
  usePolling(refreshHealth, 30000);
  usePolling(refreshTraces, 20000);

  return (
    <div className="page">
      <div className="page__inner">
        {error && <div className="alert alert--error">{error}</div>}

        <section className="section">
          <div className="section__head">
            <h2 className="section__title">Ingestion</h2>
            <div className="section__actions">
              {stats?.last_upload_at && <span className="muted">Last upload {formatRelative(stats.last_upload_at)}</span>}
              <button className="icon-btn" onClick={() => void refresh()} title="Refresh" aria-label="Refresh">
                <RefreshCw size={16} className={refreshing ? 'spin' : undefined} />
              </button>
            </div>
          </div>
          <div className="stats">
            <StatCard label="Uploads" value={stats?.all.total ?? '—'} sub="all time" />
            <StatCard label="CVs indexed" value={stats?.cv.done ?? '—'} sub={stats ? `${stats.cv.points} vectors` : undefined} tone="ok" />
            <StatCard label="JDs indexed" value={stats?.jd.done ?? '—'} sub={stats ? `${stats.jd.points} vectors` : undefined} tone="ok" />
            <StatCard label="In progress" value={stats ? pending : '—'} sub="queued + processing" tone={pending > 0 ? 'warn' : undefined} />
            <StatCard label="Failed" value={stats?.all.failed ?? '—'} tone={(stats?.all.failed ?? 0) > 0 ? 'bad' : undefined} />
          </div>
          <div className="quick">
            <a className="quick__card" href={routeHref({ page: 'upload', kind: 'cv' })}>
              <FileText size={20} />
              <span>
                <strong>Upload CV</strong>
                <small>resume → one vector per section</small>
              </span>
              <ArrowRight size={16} />
            </a>
            <a className="quick__card" href={routeHref({ page: 'upload', kind: 'jd' })}>
              <Briefcase size={20} />
              <span>
                <strong>Upload JD</strong>
                <small>job posting → semantic chunks + fields</small>
              </span>
              <ArrowRight size={16} />
            </a>
          </div>
        </section>

        <div className="two-col">
          <section className="section card">
            <div className="section__head">
              <h2 className="section__title">
                <Server size={16} /> Service health
              </h2>
              <span className="muted">bot-agent /health?probe=true</span>
            </div>
            {healthError && <div className="alert alert--error">{healthError}</div>}
            <ul className="health">
              {DEPENDENCIES.map(({ key, label, role }) => {
                const value = health?.[key];
                const state = health == null ? 'unknown' : value ? 'ok' : 'down';
                return (
                  <li key={key} className="health__item">
                    <span className={`dot dot--${state}`} aria-hidden />
                    <span className="health__name">{label}</span>
                    <span className="health__role">{role}</span>
                    <span className={`health__state health__state--${state}`}>
                      {state === 'ok' ? 'up' : state === 'down' ? (key === 'langfuse' ? 'off / down' : 'down') : '…'}
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>

          <section className="section card">
            <div className="section__head">
              <h2 className="section__title">
                <Database size={16} /> Pipeline
              </h2>
            </div>
            <ol className="pipeline">
              {PIPELINE.map((step, i) => (
                <li key={step.name} className="pipeline__step">
                  <span className="pipeline__n">{i + 1}</span>
                  <span className="pipeline__text">
                    <strong>{step.name}</strong>
                    <small>{step.detail}</small>
                  </span>
                </li>
              ))}
            </ol>
          </section>
        </div>

        <section className="section">
          <div className="section__head">
            <h2 className="section__title">Recent uploads</h2>
            <div className="section__actions">
              <a className="link" href={routeHref({ page: 'upload', kind: 'cv' })}>
                All CVs
              </a>
              <a className="link" href={routeHref({ page: 'upload', kind: 'jd' })}>
                All JDs
              </a>
            </div>
          </div>
          <UploadsTable rows={uploads} showKind compact onOpenTrace={onOpenTrace} emptyText="No uploads yet — drop a CV or a JD to start." />
        </section>

        <section className="section">
          <div className="section__head">
            <h2 className="section__title">Recent traces</h2>
            <a className="link" href={routeHref({ page: 'traces' })}>
              Observability <ArrowRight size={14} />
            </a>
          </div>
          {traces == null ? (
            <div className="empty">
              Langfuse keys are not configured on bot-agent or Langfuse is unreachable — see Observability. The{' '}
              <a className="link" href={embedHref(null)}>
                embedded dashboard
              </a>{' '}
              still works with your own Langfuse login.
            </div>
          ) : traces.length === 0 ? (
            <div className="empty">No traces yet — chat with the bot or upload a document.</div>
          ) : (
            <div className="table-wrap">
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Trace</th>
                    <th>Tags</th>
                    <th>Latency</th>
                    <th>Cost</th>
                    <th>When</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {traces.map((trace) => (
                    <tr key={trace.id}>
                      <td>
                        <button className="link-btn" onClick={() => onOpenTrace(trace.id)}>
                          {trace.name ?? trace.id.slice(0, 8)}
                        </button>
                      </td>
                      <td>{trace.tags.length ? trace.tags.join(', ') : <span className="muted">—</span>}</td>
                      <td>{formatDuration(trace.latency_seconds)}</td>
                      <td>{formatCost(trace.total_cost)}</td>
                      <td title={formatWhen(trace.timestamp)}>{formatRelative(trace.timestamp)}</td>
                      <td>
                        <a className="icon-btn icon-btn--sm" href={embedHref(trace.url)} title="Open in the Langfuse dashboard">
                          <Gauge size={14} />
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
