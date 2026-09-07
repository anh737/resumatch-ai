import { useState } from 'react';
import { Gauge, RotateCw, ScanSearch } from 'lucide-react';
import { embedHref } from '../utils/langfuse';
import type { UploadRow } from '../api/admin';
import StatusBadge from './StatusBadge';
import { formatRelative, formatWhen } from '../utils/format';

interface Props {
  rows: UploadRow[];
  showKind?: boolean;
  compact?: boolean;
  emptyText?: string;
  onOpenTrace: (traceId: string) => void;
  /** Re-run the ingestion of a finished/failed row (file is already stored). */
  onReprocess?: (row: UploadRow) => Promise<void>;
}

export default function UploadsTable({ rows, showKind = false, compact = false, emptyText = 'No uploads yet.', onOpenTrace, onReprocess }: Props) {
  const [busyId, setBusyId] = useState<string | null>(null);
  if (rows.length === 0) return <div className="empty">{emptyText}</div>;

  const reprocess = async (row: UploadRow) => {
    if (!onReprocess || busyId) return;
    setBusyId(row.id);
    try {
      await onReprocess(row);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="table-wrap">
      <table className={`table${compact ? ' table--compact' : ''}`}>
        <thead>
          <tr>
            <th>File</th>
            {showKind && <th>Kind</th>}
            <th>Status</th>
            <th className="num">Points</th>
            <th>Uploaded</th>
            {!compact && <th>Updated</th>}
            <th>Trace</th>
            {onReprocess && !compact && <th></th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className={row.status === 'failed' ? 'row--failed' : undefined}>
              <td className="cell-file">
                <div className="cell-file__name" title={row.filename}>
                  {row.filename}
                </div>
                <div className="cell-file__id" title="Document id (payload `id` in Qdrant)">
                  {row.document_id}
                </div>
              </td>
              {showKind && (
                <td>
                  <span className={`kind kind--${row.kind}`}>{row.kind.toUpperCase()}</span>
                </td>
              )}
              <td>
                <StatusBadge status={row.status} title={row.error ?? undefined} />
                {row.error && !compact && <div className="cell-error">{row.error}</div>}
              </td>
              <td className="num">{row.points ?? '—'}</td>
              <td title={formatWhen(row.created_at)}>{formatRelative(row.created_at)}</td>
              {!compact && <td title={formatWhen(row.updated_at)}>{formatRelative(row.updated_at)}</td>}
              <td>
                {row.trace_id ? (
                  <div className="cell-actions">
                    <button className="btn btn--sm" onClick={() => onOpenTrace(row.trace_id!)} title="Inspect the ingestion trace">
                      <ScanSearch size={14} />
                      Details
                    </button>
                    {row.trace_url && (
                      <a className="icon-btn icon-btn--sm" href={embedHref(row.trace_url)} title="Open in the Langfuse dashboard">
                        <Gauge size={14} />
                      </a>
                    )}
                  </div>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              {onReprocess && !compact && (
                <td>
                  {(row.status === 'done' || row.status === 'failed') && (
                    <button
                      className="icon-btn icon-btn--sm"
                      onClick={() => void reprocess(row)}
                      disabled={busyId !== null}
                      title="Re-run the ingestion of this file (e.g. after a pipeline or prompt change)"
                      aria-label="Re-process"
                    >
                      <RotateCw size={14} className={busyId === row.id ? 'spin' : undefined} />
                    </button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
