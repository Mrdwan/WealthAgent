FROM python:3.12-slim

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \
    anthropic \
    feedparser \
    finvizfinance \
    pydantic \
    pydantic-settings \
    python-telegram-bot \
    requests \
    schedule \
    yfinance

# Copy application source
COPY src/ /app/
COPY config/ /app/config/

# Ensure runtime directories exist
RUN mkdir -p /app/data /app/logs

ENTRYPOINT ["python", "entrypoint.py"]
