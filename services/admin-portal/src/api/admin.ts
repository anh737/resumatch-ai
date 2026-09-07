/**
 * Admin API client (bot-agent), reached through the `/api` proxy:
 *
 *   POST /admin/uploads            multipart {kind, file, category?} -> 202 UploadRow
 *   GET  /admin/uploads            ?kind&status&limit                 -> UploadRow[]
 *   GET  /admin/uploads/stats                                         -> UploadStats
 *   GET  /admin/uploads/{id}                                          -> UploadRow
 *   POST /admin/uploads/{id}/reprocess                                -> 202 UploadRow (re-run ingestion)
 *   GET  /admin/traces             ?limit&page&tags&name              -> TracesResponse
 *   GET  /admin/traces/{trace_id}                                     -> TraceDetail
 *   GET  /health?probe=true                                           -> HealthResponse
 *
 * bot-agent stores the file in MinIO and produces `resume.uploaded` /
 * `job.uploaded` (key = upload id); ai-embeddings reads the file back from
 * MinIO, structures + embeds it into Qdrant and reports on `*.processed`, which
 * moves the row through queued -> processing -> done | failed.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/+$/, '');

export type UploadKind = 'cv' | 'jd';
export type UploadStatus = 'queued' | 'processing' | 'done' | 'failed';

export interface UploadRow {
  id: string;
  kind: UploadKind;
  document_id: string;
  filename: string;
  category: string | null;
  status: UploadStatus;
  points: number | null;
  error: string | null;
  trace_id: string | null;
  trace_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface UploadCounts {
  total: number;
  queued: number;
  processing: number;
  done: number;
  failed: number;
  points: number;
}

export interface UploadStats {
  all: UploadCounts;
  cv: UploadCounts;
  jd: UploadCounts;
  last_upload_at: string | null;
}

export interface TraceRow {
  id: string;
  name: string | null;
  timestamp: string | null;
  session_id: string | null;
  user_id: string | null;
  environment: string | null;
  latency_seconds: number | null;
  total_cost: number | null;
  tags: string[];
  url: string;
}

export interface TracesResponse {
  configured: boolean;
  public_url: string;
  items: TraceRow[];
  page: number;
  limit: number;
  total: number | null;
  total_pages: number | null;
  error: string | null;
}

export interface ObservationRow {
  id: string;
  parent_id: string | null;
  type: string;
  name: string | null;
  start_time: string | null;
  end_time: string | null;
  latency_seconds: number | null;
  model: string | null;
  level: string | null;
  status_message: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  total_cost: number | null;
  input: unknown;
  output: unknown;
}

export interface ScoreRow {
  name: string;
  value: number | string | null;
  comment: string | null;
}

export interface TraceDetail {
  id: string;
  name: string | null;
  timestamp: string | null;
  session_id: string | null;
  user_id: string | null;
  environment: string | null;
  release: string | null;
  latency_seconds: number | null;
  total_cost: number | null;
  tags: string[];
  metadata: Record<string, unknown> | null;
  input: unknown;
  output: unknown;
  url: string;
  observations: ObservationRow[];
  scores: ScoreRow[];
}

export interface HealthResponse {
  status: string;
  app: string;
  postgres?: boolean;
  kafka?: boolean;
  minio?: boolean;
  langfuse?: boolean;
}

export class AdminApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'AdminApiError';
    this.status = status;
  }
}

function query(params: Record<string, string | number | string[] | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === '') continue;
    if (Array.isArray(value)) value.forEach((v) => search.append(key, v));
    else search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') throw err;
    throw new AdminApiError(`Cannot reach the admin API at ${url}. Is the bot-agent service running?`);
  }
  if (!response.ok) throw new AdminApiError(await readErrorBody(response), response.status);
  return (await response.json()) as T;
}

export interface ListUploadsOptions {
  kind?: UploadKind;
  status?: UploadStatus;
  limit?: number;
  signal?: AbortSignal;
}

export function listUploads({ kind, status, limit = 50, signal }: ListUploadsOptions = {}): Promise<UploadRow[]> {
  return request<UploadRow[]>(`/admin/uploads${query({ kind, status, limit })}`, { signal });
}

export function getUpload(id: string, signal?: AbortSignal): Promise<UploadRow> {
  return request<UploadRow>(`/admin/uploads/${encodeURIComponent(id)}`, { signal });
}

/** Re-run the ingestion of a file that is already stored (row goes back to `queued`). */
export function reprocessUpload(id: string): Promise<UploadRow> {
  return request<UploadRow>(`/admin/uploads/${encodeURIComponent(id)}/reprocess`, { method: 'POST' });
}

export function getUploadStats(signal?: AbortSignal): Promise<UploadStats> {
  return request<UploadStats>('/admin/uploads/stats', { signal });
}

export interface ListTracesOptions {
  limit?: number;
  page?: number;
  tags?: string[];
  name?: string;
  signal?: AbortSignal;
}

export function listTraces({ limit = 20, page = 1, tags, name, signal }: ListTracesOptions = {}): Promise<TracesResponse> {
  return request<TracesResponse>(`/admin/traces${query({ limit, page, tags, name })}`, { signal });
}

export function getTrace(id: string, signal?: AbortSignal): Promise<TraceDetail> {
  return request<TraceDetail>(`/admin/traces/${encodeURIComponent(id)}`, { signal });
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>('/health?probe=true', { signal });
}

export interface UploadOptions {
  category?: string;
  onProgress?: (percent: number) => void;
  signal?: AbortSignal;
}

/** Upload one file with progress events (XMLHttpRequest — fetch has no upload progress). */
export function uploadDocument(kind: UploadKind, file: File, { category, onProgress, signal }: UploadOptions = {}): Promise<UploadRow> {
  return new Promise((resolve, reject) => {
    const url = `${API_BASE}/admin/uploads`;
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.responseType = 'text';

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as UploadRow);
        } catch {
          reject(new AdminApiError('The admin API returned an unreadable response.', xhr.status));
        }
      } else {
        reject(new AdminApiError(parseErrorText(xhr.responseText, xhr.status, xhr.statusText), xhr.status));
      }
    };
    xhr.onerror = () => reject(new AdminApiError(`Cannot reach the admin API at ${url}. Is the bot-agent service running?`));
    xhr.ontimeout = () => reject(new AdminApiError('The upload timed out.'));
    xhr.onabort = () => reject(new DOMException('The upload was aborted.', 'AbortError'));
    signal?.addEventListener('abort', () => xhr.abort(), { once: true });

    const body = new FormData();
    body.append('kind', kind);
    body.append('file', file);
    if (category && category.trim()) body.append('category', category.trim());
    xhr.send(body);
  });
}

function parseErrorText(text: string, status: number, statusText: string): string {
  const fallback = `The admin API responded with ${status} ${statusText}`.trim();
  if (!text) return fallback;
  try {
    const data = JSON.parse(text) as Record<string, unknown>;
    const detail = data.detail ?? data.message ?? data.error;
    if (typeof detail === 'string') return detail;
    if (detail !== undefined) return JSON.stringify(detail);
  } catch {
    // not JSON
  }
  return text.length > 300 ? `${text.slice(0, 300)}…` : text;
}

async function readErrorBody(response: Response): Promise<string> {
  try {
    return parseErrorText(await response.text(), response.status, response.statusText);
  } catch {
    return `The admin API responded with ${response.status} ${response.statusText}`.trim();
  }
}
