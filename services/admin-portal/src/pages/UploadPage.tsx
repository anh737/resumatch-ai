import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Briefcase, CheckCircle2, FileText, RefreshCw, Search, X, XCircle } from 'lucide-react';
import { listUploads, reprocessUpload, uploadDocument, type UploadKind, type UploadRow, type UploadStatus } from '../api/admin';
import Dropzone from '../components/Dropzone';
import UploadsTable from '../components/UploadsTable';
import { usePolling } from '../hooks/usePolling';
import { formatBytes } from '../utils/format';

const ACCEPT = '.pdf,.docx,.txt,.md';
const MAX_MB = 20;
const POLL_MS = 4000;
const DEFAULT_CATEGORY = 'INFORMATION-TECHNOLOGY';

interface Props {
  kind: UploadKind;
  onOpenTrace: (traceId: string) => void;
}

interface QueueItem {
  localId: string;
  file: File;
  progress: number;
  state: 'uploading' | 'accepted' | 'error';
  error?: string;
}

const COPY: Record<
  UploadKind,
  { title: string; icon: typeof FileText; dropTitle: string; intro: string; collection: string; points: string }
> = {
  cv: {
    title: 'Upload CV / resume',
    icon: FileText,
    dropTitle: 'Drop resumes here',
    intro:
      'Each resume is parsed by gpt-4.1 into objective, work experience, education, certifications and technical skills. Personal identifiers are scrubbed, then every non-empty section becomes one vector.',
    collection: 'cv_information_technology',
    points: 'one point per section (objective · work_exp · information · certification · technical_skills)',
  },
  jd: {
    title: 'Upload job description',
    icon: Briefcase,
    dropTitle: 'Drop job postings here',
    intro:
      'Each posting is parsed into title, company, location, salary, requirements, responsibilities and benefits, split into semantic chunks, and indexed as chunk points plus one point per key field.',
    collection: 'jd_jobs',
    points: 'type=chunk (semantic chunks) + type=field (description · requirements · responsibilities)',
  },
};

const FILTERS: { key: 'all' | UploadStatus; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'queued', label: 'Queued' },
  { key: 'processing', label: 'Processing' },
  { key: 'done', label: 'Done' },
  { key: 'failed', label: 'Failed' },
];

function uid(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

export default function UploadPage({ kind, onOpenTrace }: Props) {
  const copy = COPY[kind];
  const [rows, setRows] = useState<UploadRow[]>([]);
  const [rowsError, setRowsError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [category, setCategory] = useState(DEFAULT_CATEGORY);
  const [filter, setFilter] = useState<'all' | UploadStatus>('all');
  const [search, setSearch] = useState('');
  const timers = useRef<number[]>([]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      setRows(await listUploads({ kind, limit: 200 }));
      setRowsError(null);
    } catch (err) {
      setRowsError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
      setLoaded(true);
    }
  }, [kind]);

  // Reset per-kind state when switching between the CV and JD pages.
  useEffect(() => {
    setRows([]);
    setLoaded(false);
    setQueue([]);
    setFilter('all');
    setSearch('');
    void refresh();
  }, [refresh]);

  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), []);

  const pending = rows.some((r) => r.status === 'queued' || r.status === 'processing');
  usePolling(refresh, POLL_MS, pending);

  const patch = (localId: string, changes: Partial<QueueItem>) =>
    setQueue((items) => items.map((it) => (it.localId === localId ? { ...it, ...changes } : it)));

  const onFiles = useCallback(
    (files: File[]) => {
      for (const file of files) {
        const localId = uid();
        const tooBig = file.size > MAX_MB * 1024 * 1024;
        setQueue((items) => [
          { localId, file, progress: 0, state: tooBig ? 'error' : 'uploading', error: tooBig ? `Larger than ${MAX_MB} MB` : undefined },
          ...items,
        ]);
        if (tooBig) continue;
        uploadDocument(kind, file, {
          category: kind === 'cv' ? category : undefined,
          onProgress: (progress) => patch(localId, { progress }),
        })
          .then((row) => {
            patch(localId, { state: 'accepted', progress: 100 });
            setRows((current) => [row, ...current.filter((r) => r.id !== row.id)]);
            timers.current.push(window.setTimeout(() => setQueue((items) => items.filter((it) => it.localId !== localId)), 4000));
          })
          .catch((err: unknown) => {
            patch(localId, { state: 'error', error: err instanceof Error ? err.message : String(err) });
          });
      }
    },
    [kind, category],
  );

  const onReprocess = useCallback(async (row: UploadRow) => {
    try {
      const updated = await reprocessUpload(row.id);
      setRows((current) => current.map((r) => (r.id === updated.id ? updated : r)));
      setRowsError(null);
    } catch (err) {
      setRowsError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const counts = useMemo(() => {
    const c: Record<'all' | UploadStatus, number> = { all: rows.length, queued: 0, processing: 0, done: 0, failed: 0 };
    for (const r of rows) c[r.status] += 1;
    return c;
  }, [rows]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter(
      (r) => (filter === 'all' || r.status === filter) && (!q || r.filename.toLowerCase().includes(q) || r.document_id.includes(q)),
    );
  }, [rows, filter, search]);

  const Icon = copy.icon;

  return (
    <div className="page">
      <div className="page__inner">
        <section className="section">
          <div className="intro">
            <div className="intro__text">
              <h2 className="section__title">{copy.title}</h2>
              <p className="muted">{copy.intro}</p>
              <dl className="intro__facts">
                <div>
                  <dt>Collection</dt>
                  <dd className="mono">{copy.collection}</dd>
                </div>
                <div>
                  <dt>Points</dt>
                  <dd>{copy.points}</dd>
                </div>
                <div>
                  <dt>Accepted</dt>
                  <dd>PDF, DOCX, TXT, MD · up to {MAX_MB} MB each</dd>
                </div>
              </dl>
            </div>
          </div>

          <div className="upload-grid">
            <Dropzone
              accept={ACCEPT}
              title={copy.dropTitle}
              hint="or click to choose · several files at once are fine"
              icon={<Icon size={28} />}
              onFiles={onFiles}
            />
            <div className="upload-side">
              {kind === 'cv' && (
                <label className="field">
                  <span className="field__label">Category</span>
                  <input
                    className="field__input"
                    list="cv-categories"
                    value={category}
                    onChange={(e) => setCategory(e.target.value)}
                    placeholder={DEFAULT_CATEGORY}
                    spellCheck={false}
                  />
                  <datalist id="cv-categories">
                    <option value="INFORMATION-TECHNOLOGY" />
                    <option value="ENGINEERING" />
                    <option value="DATA-SCIENCE" />
                    <option value="FINANCE" />
                    <option value="HR" />
                  </datalist>
                  <span className="field__hint">
                    Stored as the payload <code>category</code> (filterable by <code>retrieval_cv</code>). Blank = INFORMATION-TECHNOLOGY.
                  </span>
                </label>
              )}
              {kind === 'jd' && (
                <div className="note">
                  <strong>Document id</strong>
                  <p>
                    Each posting gets <code>upload_&lt;epoch-ms&gt;</code> as its <code>id</code>, following the offline{' '}
                    <code>&lt;source&gt;_&lt;id&gt;</code> convention. Re-uploading the same file creates a new posting.
                  </p>
                </div>
              )}
              <div className="note">
                <strong>What happens next</strong>
                <p>
                  bot-agent stores the file in MinIO and publishes a Kafka event keyed by the upload id. ai-embeddings picks it up,
                  reads the file back from MinIO and writes the vectors to Qdrant. Status updates land here automatically.
                </p>
              </div>
            </div>
          </div>

          {queue.length > 0 && (
            <ul className="queue" aria-live="polite">
              {queue.map((item) => (
                <li key={item.localId} className={`queue__item queue__item--${item.state}`}>
                  <span className="queue__icon" aria-hidden>
                    {item.state === 'accepted' ? <CheckCircle2 size={16} /> : item.state === 'error' ? <XCircle size={16} /> : <Icon size={16} />}
                  </span>
                  <span className="queue__name" title={item.file.name}>
                    {item.file.name}
                  </span>
                  <span className="queue__size">{formatBytes(item.file.size)}</span>
                  <span className="queue__state">
                    {item.state === 'uploading' && `Uploading ${item.progress}%`}
                    {item.state === 'accepted' && 'Accepted — queued for ingestion'}
                    {item.state === 'error' && (item.error ?? 'Failed')}
                  </span>
                  {item.state === 'uploading' && (
                    <span className="progress" aria-hidden>
                      <span className="progress__bar" style={{ width: `${item.progress}%` }} />
                    </span>
                  )}
                  {item.state === 'error' && (
                    <button
                      className="icon-btn icon-btn--sm"
                      onClick={() => setQueue((items) => items.filter((it) => it.localId !== item.localId))}
                      aria-label="Dismiss"
                    >
                      <X size={14} />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="section">
          <div className="section__head section__head--wrap">
            <h2 className="section__title">
              {kind === 'cv' ? 'CV uploads' : 'JD uploads'}
              {pending && <span className="live">live</span>}
            </h2>
            <div className="section__actions">
              <label className="search">
                <Search size={14} />
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filter by file or id…" />
                {search && (
                  <button className="icon-btn icon-btn--sm" onClick={() => setSearch('')} aria-label="Clear">
                    <X size={12} />
                  </button>
                )}
              </label>
              <button className="icon-btn" onClick={() => void refresh()} title="Refresh" aria-label="Refresh uploads">
                <RefreshCw size={16} className={refreshing ? 'spin' : undefined} />
              </button>
            </div>
          </div>
          <div className="chips">
            {FILTERS.map(({ key, label }) => (
              <button key={key} className={`chip${filter === key ? ' chip--active' : ''}`} onClick={() => setFilter(key)}>
                {label}
                <span className="chip__count">{counts[key]}</span>
              </button>
            ))}
          </div>
          {rowsError && <div className="alert alert--error">{rowsError}</div>}
          {loaded && (
            <UploadsTable
              rows={visible}
              onOpenTrace={onOpenTrace}
              onReprocess={onReprocess}
              emptyText={rows.length === 0 ? `No ${kind.toUpperCase()} uploads yet.` : 'Nothing matches the current filter.'}
            />
          )}
        </section>
      </div>
    </div>
  );
}
