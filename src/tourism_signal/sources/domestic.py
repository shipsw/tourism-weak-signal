"""通用 RSS 数据源：用于国内平台/媒体公开 RSS（如人民网、新华网旅游频道等）。

国内平台（头条/抖音/小红书/微博）无公开稳定 API，
MVP 阶段通过 config.yaml 中 domestic.feeds 配置可用 RSS 源接入。
"""
from __future__ import annotations

import feedparser

from ..models import NewsItem
from ..utils import md5
from .base import BaseSource

USER_AGENT = "Mozilla/5.0 (compatible; tourism-weak-signal-agent/0.1)"


class RSSSource(BaseSource):
    name = "domestic"

    def fetch(self) -> list[NewsItem]:
        feeds = self.settings.get("feeds", [])
        out: list[NewsItem] = []
        for f in feeds:
            name = f.get("name", "rss")
            url = f.get("url", "")
            lang = f.get("lang", "zh-CN")
            if not url:
                continue
            try:
                feed = feedparser.parse(url, agent=USER_AGENT)
            except Exception as e:
                self.log.warning("rss '%s' 解析失败: %s", name, e)
                continue
            fetched = []
            for e in feed.entries[: int(f.get("max", 30))]:
                title = e.get("title", "")
                if not title:
                    continue
                item = NewsItem(
                    title=title,
                    content=e.get("summary") or e.get("description") or "",
                    url=e.get("link", ""),
                    source=f"rss:{name}",
                    published_at=e.get("published", ""),
                    language="zh" if lang.startswith("zh") else "en",
                    keywords=[],
                    raw={"feed": feed.feed.get("title", "")},
                )
                item.item_id = md5(item.url or item.title)
                fetched.append(item)
            out.extend(fetched)
            self.log.info("rss '%s' → %d 条", name, len(fetched))
        return out
