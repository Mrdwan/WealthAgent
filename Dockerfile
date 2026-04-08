FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    requests \
    yfinance \
    feedparser \
    python-telegram-bot \
    schedule \
    finvizfinance \
    pydantic

COPY src/ /app/

CMD ["python", "telegram_bot.py"]