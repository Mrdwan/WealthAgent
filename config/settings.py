import os

# API Keys (set these as environment variables)
TIINGO_API_KEY = os.environ.get("TIINGO_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e4b")  # default to gemma4:e4b
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# main LLM settings
MAIN_MODEL = os.environ.get("MAIN_MODEL", "claude-opus-4-6")  # default to claude-opus-4-6
MAIN_MODEL_API_KEY = os.environ.get("MAIN_MODEL_API_KEY", "")
MAIN_MODEL_API_URL = os.environ.get("MAIN_MODEL_API_URL", "https://api.anthropic.com/v1/messages")


# Database
DB_PATH = os.path.expanduser("./wealthagent.db")

# Paths
LOG_DIR = os.path.expanduser("./logs")

# Portfolio constants
MONTHLY_BUDGET_EUR = os.environ.get("MONTHLY_BUDGET_EUR", 2000)  # default to 2000 EUR/month
LONG_TERM_PCT = os.environ.get("LONG_TERM_PCT", 0.75)
SHORT_TERM_PCT = os.environ.get("SHORT_TERM_PCT", 0.25)
CGT_RATE = os.environ.get("CGT_RATE", 0.33)
ANNUAL_EXEMPTION = os.environ.get("ANNUAL_EXEMPTION", 1270)

# Thresholds
ALERT_DROP_PCT = os.environ.get("ALERT_DROP_PCT", 10)       # flag if stock drops >10% in 30 days
STOP_LOSS_PCT = os.environ.get("STOP_LOSS_PCT", 8)         # exit short-term if drops >8%
DIVIDEND_YIELD_MAX = os.environ.get("DIVIDEND_YIELD_MAX", 2.0)  # deprioritize above this

# RSS feeds for financial news
RSS_FEEDS = [
    # General market
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GOOGL,NVDA&region=US&lang=en-US",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",  # Top news
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",   # Markets
    # Reuters
    "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
    # Finviz
    "https://finviz.com/news_export.ashx?v=1",
]