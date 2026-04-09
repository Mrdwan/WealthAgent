FROM python:3.12-slim

WORKDIR /app

# Install system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
 && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir \
    anthropic \
    feedparser \
    finvizfinance \
    lxml \
    pydantic \
    pydantic-settings \
    python-telegram-bot \
    requests \
    schedule \
    yfinance

# Copy application source
COPY src/ /app/
COPY config/ /app/config/
COPY tests/ /app/tests/

# Ensure runtime directories exist
RUN mkdir -p /app/data /app/logs

ENTRYPOINT ["python", "entrypoint.py"]
