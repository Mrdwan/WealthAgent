FROM python:3.12-slim

WORKDIR /app

# Install system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
 && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Install Python dependencies (cached layer — only re-runs when lock changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY src/ /app/
COPY config/ /app/config/
COPY tests/ /app/tests/

# Ensure runtime directories exist
RUN mkdir -p /app/data /app/logs

# Use the venv Python
ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["python", "entrypoint.py"]
