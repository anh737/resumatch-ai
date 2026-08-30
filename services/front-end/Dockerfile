# ---- build ------------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Baked into the bundle at build time (browser-side constant).
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# ---- serve ------------------------------------------------------------------
FROM nginx:1.27-alpine
# The official image runs envsubst on /etc/nginx/templates/*.template at start,
# so the upstreams can be changed per environment without rebuilding.
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
ENV API_UPSTREAM=http://bot-agent:8001
ENV WS_UPSTREAM=http://ai-agents:8000
# Lets the entrypoint fill NGINX_LOCAL_RESOLVERS from /etc/resolv.conf, which the
# template uses to resolve API_UPSTREAM lazily (at request time, not boot).
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
EXPOSE 80
