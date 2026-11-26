# API image. Build context is the repo root so seed/ and tools/ (the test oracle) are available;
# the layout mirrors the repo (/app/backend, /app/seed, /app/tools) so relative paths in tests work.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/
# UV_NO_CACHE: otherwise uv leaves ~140 MB of downloaded wheels in /root/.cache/uv inside the image.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    SEED_PATH=/app/seed/seed.json

WORKDIR /app/backend
# Dependencies first (cached layer). Dev deps included: the same image runs the pytest suite.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen

COPY backend/ ./
COPY seed/ /app/seed/
COPY tools/ /app/tools/

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
# Readiness, not just liveness: 200 only while the database answers `SELECT 1`.
HEALTHCHECK --interval=3s --timeout=3s --start-period=20s --retries=20 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=2).status == 200 else 1)"]

# Migrate, seed (idempotent, so safe on every start), then serve.
COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
CMD ["sh", "/usr/local/bin/docker-entrypoint.sh"]
