from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser
from bs4 import BeautifulSoup

from .models import NewsItem

BBC_BUSINESS_FEED = "http://feeds.bbci.co.uk/news/business/rss.xml"


def clean_html_to_text(html_content: str) -> str:
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, "html.parser")
    for element in soup(["script", "style", "a", "img"]):
        element.decompose()

    return " ".join(soup.get_text(separator=" ").split()).strip()


def fetch_bbc_business_news(limit: int = 10) -> list[NewsItem]:
    parsed = feedparser.parse(BBC_BUSINESS_FEED)
    items: list[NewsItem] = []

    for entry in parsed.entries[:limit]:
        published = entry.get("published_parsed")
        published_at = (
            datetime(*published[:6], tzinfo=UTC)
            if published is not None
            else datetime.now(UTC)
        )
        items.append(
            NewsItem(
                source="BBC Business",
                story_id=entry.get("id", entry.get("link", entry.get("title", ""))),
                title=clean_html_to_text(entry.get("title", "")),
                summary=clean_html_to_text(entry.get("summary", "")),
                published_at=published_at,
            )
        )

    return items


@dataclass(slots=True)
class NewsCache:
    _last_signature: tuple[str, ...] = field(default_factory=tuple)

    def has_changed(self, stories: list[NewsItem]) -> bool:
        signature = tuple(sorted(story.story_id for story in stories))
        if signature != self._last_signature:
            self._last_signature = signature
            return True
        return False
