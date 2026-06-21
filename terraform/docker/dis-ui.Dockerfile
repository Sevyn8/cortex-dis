# syntax=docker/dockerfile:1.7
# dis-ui (frontend)
#
# Builds the Vite SPA and serves the static assets with nginx, with SPA
# fallback (all routes -> index.html) so client-side routing works. Cloud Run
# serves this container.
#
# BUILD-TIME vars (Vite bakes import.meta.env.VITE_* into the static JS at build,
# NOT at runtime). The UI reads exactly two, so they MUST be passed as build args
# and baked here; a runtime Cloud Run env var does nothing for an already-built SPA:
#   VITE_DIS_UI_SERVER_MODE      'real' calls dis-ui-server; anything else is fixtures.
#   VITE_DIS_UI_SERVER_BASE_URL  the dis-ui-server base URL (required when mode=real).
# The mode defaults to 'fixture' so a plain, un-parameterized build is never
# accidentally broken-real. The old VITE_API_BASE arg was DEAD (the UI never read it).
#
# Build from services/dis-ui (the frontend package root):
#   docker build -f ../../docker/dis-ui.Dockerfile \
#     --build-arg VITE_DIS_UI_SERVER_MODE=real \
#     --build-arg VITE_DIS_UI_SERVER_BASE_URL=https://dis-ui-server-XXXX.run.app \
#     -t IMAGE .

FROM node:20-slim AS build
WORKDIR /app

# pnpm (the repo uses pnpm for the UI).
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

# Build-time Vite vars (ARG -> ENV -> baked by `vite build`). Mode defaults to
# 'fixture' (safe); staging passes 'real' + the base URL via cloudbuild substitutions.
# VITE_STUB_TOKEN_TENANT / VITE_STUB_TOKEN_OPS are the pre-supplied dev-login persona
# tokens (staging only); pass via .env.local or build-arg, never committed.
ARG VITE_DIS_UI_SERVER_MODE="fixture"
ARG VITE_DIS_UI_SERVER_BASE_URL=""
ARG VITE_STUB_TOKEN_TENANT=""
ARG VITE_STUB_TOKEN_OPS=""
ENV VITE_DIS_UI_SERVER_MODE=${VITE_DIS_UI_SERVER_MODE}
ENV VITE_DIS_UI_SERVER_BASE_URL=${VITE_DIS_UI_SERVER_BASE_URL}
ENV VITE_STUB_TOKEN_TENANT=${VITE_STUB_TOKEN_TENANT}
ENV VITE_STUB_TOKEN_OPS=${VITE_STUB_TOKEN_OPS}
RUN pnpm build

# --- serve ---
FROM nginx:1.27-alpine AS serve

# SPA config: fall back to index.html for unknown routes; Cloud Run sets PORT.
COPY <<'NGINX' /etc/nginx/conf.d/default.conf
server {
  listen       8080;
  server_name  _;
  root   /usr/share/nginx/html;
  index  index.html;

  location / {
    try_files $uri $uri/ /index.html;
  }
}
NGINX

COPY --from=build /app/dist /usr/share/nginx/html

# Cloud Run sends traffic to $PORT (default 8080). nginx listens on 8080 above.
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
