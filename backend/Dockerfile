# Backend image: FastAPI API + CLI. Build context is the repository root so the fixture set
# can be copied next to the application (settings resolve fixtures at <repo>/fixtures).
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
# Set BACKEND_EXTRAS="--extra postgres" to include the asyncpg driver.
ARG BACKEND_EXTRAS=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_NATIVE_TLS=true \
    UV_PROJECT_ENVIRONMENT=/srv/.venv \
    PATH="/srv/.venv/bin:${PATH}"

WORKDIR /srv/backend
RUN python3 -m pip install --no-cache-dir --break-system-packages "uv==0.8.17" 2>/dev/null \
    || python3 -m pip install --no-cache-dir "uv==0.8.17"

COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project ${BACKEND_EXTRAS} --python python3.12

COPY backend/app ./app
COPY fixtures /srv/fixtures
RUN uv sync --frozen --no-dev ${BACKEND_EXTRAS} --python python3.12 && mkdir -p /srv/data

COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && (id -u app >/dev/null 2>&1 || useradd --system --home /srv --shell /usr/sbin/nologin app) \
    && chown -R app /srv/data
USER app

ENV DATABASE_URL=sqlite+aiosqlite:////srv/data/arbitrage.db \
    DATA_MODE=fixture \
    PAPER_TRADING_ONLY=true
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
