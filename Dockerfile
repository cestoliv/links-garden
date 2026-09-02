# Three stages so the final image ships neither Node/node_modules nor uv itself, just a
# venv, the source and the built dashboard, per DESIGN.md's "one image, not two" (api.py
# serves the dashboard from the same origin as the API, so there's no CORS problem to work
# around).

FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /uvx /bin/
WORKDIR /app

# Dependencies first, in their own layer: they change far less often than src/, so this layer
# stays cached across most rebuilds.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
WORKDIR /app

# Just the venv, the source it points back to, and the built dashboard -- no uv/uvx binaries
# and none of the build tooling uv pulled in along the way.
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/src ./src
# frontend/dist sits at the same path relative to WORKDIR as it does relative to the repo root
# in a checkout, which is what lets api.py find it (see _FRONTEND_DIST) without an env var.
COPY --from=frontend /app/frontend/dist ./frontend/dist
ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000
CMD ["garden", "serve", "--host", "0.0.0.0"]
