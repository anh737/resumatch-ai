# admin-portal — React admin UI (CV/JD ingestion + Langfuse observability),
# served by nginx which proxies /api/* to bot-agent.
# Build from services/:  docker compose build admin-portal

# ---- build ------------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Baked into the bundle at build time (browser-side constants).
ARG VITE_API_BASE_URL=/api
ARG VITE_CHAT_URL=http://localhost:3000
# Browser-facing origin of the Langfuse proxy (second nginx listener). Empty =
# derived at runtime: same host as the portal, port 3002.
ARG VITE_LANGFUSE_EMBED_URL=
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_CHAT_URL=$VITE_CHAT_URL \
    VITE_LANGFUSE_EMBED_URL=$VITE_LANGFUSE_EMBED_URL
RUN npm run build

# ---- serve ------------------------------------------------------------------
FROM nginx:1.27-alpine
# The official image runs envsubst on /etc/nginx/templates/*.template at start,
# so the upstream can be changed per environment without rebuilding.
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
ENV API_UPSTREAM=http://bot-agent:8001 \
    LANGFUSE_UPSTREAM=http://langfuse-web:3000 \
    LANGFUSE_PUBLIC_URL=http://localhost:3031 \
    LANGFUSE_FRAME_ANCESTORS="'self' http://localhost:3001 http://127.0.0.1:3001"
# Lets the entrypoint fill NGINX_LOCAL_RESOLVERS from /etc/resolv.conf, which the
# template uses to resolve the upstreams lazily (at request time, not boot).
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
# 80 = portal, 8080 = Langfuse dashboard proxy (embedded by the portal)
EXPOSE 80 8080
