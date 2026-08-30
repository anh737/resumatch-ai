# front-end

ChatGPT-style chat UI for the resume-scan assistant. Vite + React 19 +
TypeScript, no CSS framework (plain CSS with light/dark theme tokens).

## Folder layout

```
front-end/
├── index.html
├── vite.config.ts        # dev server + /api proxy -> ai-agents
├── Dockerfile            # node build -> nginx (see nginx/)
├── nginx/default.conf.template
└── src/
    ├── App.tsx           # app state: conversations, streaming, sidebar
    ├── api/chat.ts       # API client (SSE / text / JSON)
    ├── store/            # localStorage-backed conversation store
    ├── hooks/            # theme
    ├── components/       # Sidebar, Header, ChatView, Composer, Markdown, ...
    ├── styles/global.css # theme tokens + all styles
    └── utils/
```

## Run (dev)

```bash
npm install
cp .env.example .env      # optional; defaults work for local dev
npm run dev               # http://localhost:3000
```

The dev server proxies `/api/*` to `http://localhost:8000` (ai-agents) and
strips the `/api` prefix, so the browser calls `/api/chat` and the backend
receives `/chat`. Change the target with `VITE_PROXY_TARGET` in `.env`.

Other scripts: `npm run build` (type-check + production bundle in `dist/`),
`npm run preview`, `npm run typecheck`.

## Backend contract

One user turn is two steps (`src/api/chat.ts`):

1. The UI opens the answer stream first, so no token can be missed:

   ```
   WS /api/ws/chat/{conversation_id}          -> ai-agents
   ```

2. Then it submits the turn (bot-agent stores it, loads the last 10 turns of
   history, and forwards everything to ai-agents over Kafka):

   ```
   POST /api/chat                             -> bot-agent
   Content-Type: application/json

   { "conversation_id": "client-generated-id", "message": "latest message" }
   ```

   bot-agent replies `202 {"conversation_id", "message_id", "status": "queued"}`.

The answer arrives on the WebSocket as JSON events, one per frame:

| Event                                                        | Meaning                                     |
|--------------------------------------------------------------|---------------------------------------------|
| `{"type":"tool_call","tool":"retrieval_cv","arguments":{…}}` | agent progress (ignored for now)            |
| `{"content":"token"}`                                        | answer text, repeated                       |
| `{"type":"suggestions","suggestions":[…]}`                   | follow-up prompts, shown as clickable chips |
| `{"type":"done"}`                                            | turn finished                               |
| `{"type":"error","message":"..."}`                           | turn failed                                 |

bot-agent also exposes the persisted history (`GET /api/conversations`,
`GET /api/conversations/{id}/messages`); the UI currently still keeps its own
copy in `localStorage`.

## Docker

```bash
docker build -t resume-scan-front-end .
docker run -p 3000:80 \
  -e API_UPSTREAM=http://host.docker.internal:8001 \
  -e WS_UPSTREAM=http://host.docker.internal:8000 \
  resume-scan-front-end
```

nginx serves the static bundle and proxies `/api/*` to `API_UPSTREAM`
(bot-agent, default `http://bot-agent:8001`) and `/api/ws/*` to `WS_UPSTREAM`
(ai-agents, default `http://ai-agents:8000`) with buffering disabled so
streaming works. The service is also declared in `../docker-compose.yml`.

## Features

- Sidebar with date-grouped chat history (rename / delete / search), collapsible; drawer on mobile
- Streaming responses with stop, regenerate, and retry-on-error
- Markdown rendering with GFM tables, syntax-highlighted code blocks and copy buttons
- Light / dark theme (follows the OS, toggle in the header)
- Chats persisted in `localStorage` (no account required)
