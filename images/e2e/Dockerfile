# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY cli/pyproject.toml cli/pyproject.toml
COPY seeder/pyproject.toml seeder/pyproject.toml
COPY e2e/pyproject.toml e2e/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package prismatic-e2e --no-dev --no-install-workspace

FROM python:3.12-slim
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /app/e2e
COPY --from=builder /app/.venv /app/.venv
COPY e2e/ ./
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
CMD ["pytest"]
