FROM ghcr.io/astral-sh/uv:0.12.8-python3.14-trixie-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

USER 65532:65532
EXPOSE 8000

CMD ["uvicorn", "ai_sre_investigation.main:app", "--host", "0.0.0.0", "--port", "8000"]
