"""News fetcher — pulls articles from RSS feeds and stores in news_articles table.

Fetches from all configured RSS_FEEDS, deduplicates by URL, and stores new
articles for downstream LLM extraction.

CLI usage (inside the container):
    python -m news_fetcher
"""

import logging
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser

from config.settings import settings
from db import NewsArticle, db_conn, get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_MAX_PER_FEED = 20


def _parse_published_at(entry: feedparser.FeedParserDict) -> datetime | None:
    """Extract and parse the published timestamp from an RSS entry."""
    # feedparser provides published_parsed as a time.struct_time
    if entry.get("published_parsed"):
        try:
            import time
            ts = entry.published_parsed
            return datetime.fromtimestamp(time.mktime(ts), tz=timezone.utc)
        except Exception:
            pass

    # Fallback: parse the raw published string
    raw = entry.get("published", "") or entry.get("updated", "")
    if raw:
        try:
            return parsedate_to_datetime(raw)
        except Exception:
            pass

    return None


def _existing_urls() -> set[str]:
    """Return the set of URLs already stored in news_articles."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT url FROM news_articles").fetchall()
    finally:
        conn.close()
    return {row["url"] for row in rows}


def fetch_feed(feed_url: str) -> int:
    """Fetch a single RSS feed and store new articles.

    Returns the count of new articles added.
    """
    log.info("Fetching feed: %s", feed_url)
    try:
        parsed = feedparser.parse(feed_url)
    except Exception as exc:
        log.error("Failed to parse feed %s: %s", feed_url, exc)
        return 0

    if parsed.bozo and not parsed.entries:
        log.warning("Feed %s returned bozo error: %s", feed_url, parsed.bozo_exception)
        return 0

    existing = _existing_urls()
    source = parsed.feed.get("title") or feed_url
    added = 0

    for entry in parsed.entries[:_MAX_PER_FEED]:
        url = entry.get("link") or entry.get("id") or ""
        if not url or url in existing:
            continue

        title = entry.get("title", "")
        # First 1000 chars of summary/description
        snippet_raw = entry.get("summary", "") or entry.get("description", "")
        content_snippet = snippet_raw[:1000] if snippet_raw else None

        published_at = _parse_published_at(entry)

        article = NewsArticle(
            url=url,
            title=title or None,
            source=source,
            published_at=published_at,
            fetched_at=datetime.now(tz=timezone.utc),
            content_snippet=content_snippet,
            processed=0,
        )

        try:
            with db_conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO news_articles"
                    " (url, title, source, published_at, fetched_at, content_snippet, processed)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        article.url,
                        article.title,
                        article.source,
                        article.published_at.isoformat() if article.published_at else None,
                        article.fetched_at.isoformat() if article.fetched_at else None,
                        article.content_snippet,
                        article.processed,
                    ),
                )
            existing.add(url)
            added += 1
        except Exception as exc:
            log.error("Failed to store article %s: %s", url, exc)

    log.info("Feed %s: %d new articles", source, added)
    return added


def fetch_all_feeds() -> int:
    """Fetch all configured RSS feeds.

    Returns the total count of new articles added across all feeds.
    """
    feeds = settings.rss_feeds
    log.info("Fetching %d RSS feeds", len(feeds))
    total = 0
    for url in feeds:
        try:
            total += fetch_feed(url)
        except Exception as exc:
            log.error("Unexpected error fetching %s: %s", url, exc)
    log.info("Total new articles added: %d", total)
    return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Fetch all feeds and print summary."""
    count = fetch_all_feeds()
    print(f"Done. {count} new article(s) added.")


if __name__ == "__main__":
    main()
