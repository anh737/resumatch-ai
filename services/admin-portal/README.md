# admin-portal

Operator UI of resume-scan (Vite + React 19 + TypeScript, served by nginx). It
is a separate app from the chat `front-end` and talks only to `bot-agent`
through the `/api` proxy — no secret ever reaches the browser.

| Page | What it does |
|---|---|
| **Overview** | Upload counters (`GET /admin/uploads/stats`), dependency health (`GET /health?probe=true`: PostgreSQL, Kafka, MinIO, Langfuse), the ingestion pipeline, latest uploads and traces. |
| **Upload CV** | Drop one or many resumes (PDF / DOCX / TXT / MD, ≤ 20 MB). Optional `category` (payload field, default `INFORMATION-TECHNOLOGY`). Live table of CV uploads with status, point count, error, trace, and a **re-process** button that re-runs the ingestion of a stored file (e.g. after a prompt change). |
| **Upload JD** | Same for job descriptions (`jd_jobs` collection: semantic chunks + field points). |
| **Observability** | Langfuse integration through bot-agent: recent traces (all / ingestion / chat, name filter, paging), page cost + average latency, and an in-app **trace drawer** — trace I/O, metadata, the observation tree (spans, generations with model / tokens / cost, tools, errors) and scores. |
| **Langfuse dashboard** | The full Langfuse UI **embedded in the portal** (`#/langfuse`, `#/langfuse?path=/project/…/traces/…`). Every Langfuse link in the portal (trace rows, the drawer's "Open in Langfuse") opens here instead of a new tab. |

## Data flow

```
admin-portal ──POST /api/admin/uploads──▶ bot-agent ──▶ MinIO  (resumes/ | jobs/  <document_id>/<filename>)
                                                   └──▶ Kafka  resume.uploaded | job.uploaded   (key = upload_id)
                                                                     │
                                                                     ▼
                                       ai-embeddings: MinIO get_object ─▶ extract ─▶ gpt-4.1 structure
                                                      ─▶ (JD) semantic chunks ─▶ embed ─▶ Qdrant upsert
                                                                     │
                                                                     ▼
bot-agent ◀── Kafka resume.processed | job.processed ── { status: processing | done | failed, points, trace_id }
   │
   └── admin-portal polls GET /api/admin/uploads every 4 s while something is queued / processing
```

Each ingestion is one Langfuse trace (`ingest-cv` / `ingest-jd`, tag
`ingestion`); the table's **Details** button opens it in the drawer, the gauge
icon opens it in the embedded Langfuse dashboard.

## Embedded Langfuse (port 3002)

Langfuse answers with `X-Frame-Options: SAMEORIGIN` and a CSP containing
`frame-ancestors 'none'`, so `http://localhost:3031` cannot be put in an
iframe. The portal's nginx therefore runs a **second listener** (container
`:8080`, published as `:3002`) that reverse-proxies `langfuse-web:3000` at its
root, drops those two headers and sets
`Content-Security-Policy: frame-ancestors 'self' <portal origin>`
(`LANGFUSE_FRAME_ANCESTORS`). Redirects to Langfuse's own `NEXTAUTH_URL`
(`LANGFUSE_PUBLIC_URL`) are rewritten back onto the proxy. A sub-path such as
`/langfuse/` is not possible because Next.js emits absolute `/_next`, `/api`
and `/project` paths.

Browser cookies are scoped by host, not port, so a Langfuse session signed in
on `:3031` is valid on `:3002` — the frame is usually authenticated right away.
If you sign in *inside* the frame, NextAuth may bounce to `:3031` once; press
**Reload** in the toolbar.

The page computes the proxy origin as `<portal host>:3002` at runtime; set the
`VITE_LANGFUSE_EMBED_URL` build arg to override it (see `.env.example`).

## Run (dev)

```bash
npm install
cp .env.example .env        # VITE_PROXY_TARGET = bot-agent (default http://localhost:8001)
npm run dev                 # http://localhost:3001  (proxies /api/* -> bot-agent)
```

bot-agent needs its Langfuse keys (`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
in `services/bot-agent/.env`) for the Observability page; without them the page
explains what to configure and the rest of the portal keeps working.

## Build / Docker

```bash
npm run build               # tsc --noEmit && vite build  -> dist/
cd .. && docker compose up -d --build admin-portal    # http://localhost:3001 (+ :3002 Langfuse proxy)
```

The image (`Dockerfile`) builds the bundle with node and serves it with nginx;
`nginx/default.conf.template` proxies `/api/*` to `API_UPSTREAM`
(`http://bot-agent:8001` in compose) with a 25 MB body limit for uploads, and
serves the embedded Langfuse proxy on `:8080` (`LANGFUSE_UPSTREAM`,
`LANGFUSE_PUBLIC_URL`, `LANGFUSE_FRAME_ANCESTORS`). Build args:
`VITE_API_BASE_URL` (default `/api`), `VITE_CHAT_URL` (sidebar link to the chat
UI), `VITE_LANGFUSE_EMBED_URL` (proxy origin; empty = runtime default).

## Layout

```
src/
├── App.tsx              shell: sidebar + header + hash routes (#/overview, #/upload/cv, #/upload/jd, #/traces[/<id>], #/langfuse[?path=])
├── api/admin.ts         bot-agent client (uploads with XHR progress, status, stats, traces, health)
├── pages/               OverviewPage, UploadPage (kind = cv | jd), TracesPage, LangfusePage (iframe)
├── components/          Sidebar, Header, Dropzone, UploadsTable, StatusBadge, TraceDrawer
├── hooks/               useHashRoute, usePolling, useTheme
├── utils/format.ts      dates, durations, cost, bytes, JSON pretty-print
├── utils/langfuse.ts    embedded-dashboard origin + link rewriting
└── styles/global.css    same design tokens as front-end (light / dark)
```
