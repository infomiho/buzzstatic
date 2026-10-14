FROM node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS css-builder

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY src/server/static/input.css src/server/static/input.css
COPY src/server/static/dashboard.js src/server/static/deploy.js src/server/static/site-detail.js src/server/static/
COPY src/server/templates/ src/server/templates/
RUN npx @tailwindcss/cli -i src/server/static/input.css -o src/server/static/style.css --minify

FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS python-base

FROM python-base AS python-builder

COPY --from=ghcr.io/astral-sh/uv:0.11@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 /uv /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN uv sync --frozen --no-dev

FROM python-base

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY --from=python-builder /app /app
COPY --from=css-builder /build/src/server/static/style.css ./src/server/static/style.css

RUN mkdir -p /data/sites

VOLUME /data

EXPOSE 8080 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-m", "server.healthcheck"]

CMD ["python", "-m", "server", "--port", "8080"]
