"""Google News RSS 数据源（免密钥，中英文关键词搜索 + 社交媒体定向搜索）。

social_queries 使用 site: 语法定向抓取社交平台被 Google News 索引的内容：
Quora / X / Facebook / YouTube / 微博 等（Reddit 不被索引，走 reddit.py 官方 API）。

时效性：Google News RSS 默认按相关性排序，旧内容（甚至数月前）会混入结果。
因此每个查询自动追加 `after:YYYY-MM-DD` 时间窗口（lookback_days 配置），
抓取后再按 published 字段兜底过滤，确保日报内容为最近几天。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import feedparser

from ..models import NewsItem
from ..utils import md5
from .base import BaseSource

USER_AGENT = "Mozilla/5.0 (compatible; tourism-weak-signal-agent/0.1)"
_RFC822 = "%a, %d %b %Y %H:%M:%S %Z"


def _build_query(q: str, lookback_days: int, now: datetime | None = None) -> str:
    """给查询追加 after: 时间窗口，确保结果为最近 N 天内容。"""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    return f"{q} after:{cutoff}".strip()


def _parse_published(s: str) -> datetime | None:
    """解析 Google News 的 RFC822 时间戳（GMT），失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), _RFC822).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class GoogleNewsSource(BaseSource):
    name = "google_news"
    BASE = "https://news.google.com/rss/search"

    def fetch(self) -> list[NewsItem]:
        max_results = int(self.settings.get("max_results", 25))
        lookback_days = int(self.settings.get("lookback_days", 3))
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        out: list[NewsItem] = []

        # (是否社交渠道, 查询列表)
        groups = (
            (False, self.settings.get("queries", [])),
            (True, self.settings.get("social_queries", [])),
        )
        for social, queries in groups:
            for q in queries:
                query = q.get("q", "")
                lang = q.get("lang", "zh-CN")
                if not query:
                    continue
                # 追加时间窗口（Google News 默认按相关性排序，需显式限定最近几天）
                query = _build_query(query, lookback_days)
                url = (
                    f"{self.BASE}?q={quote(query)}&hl={lang}"
                    f"&gl={lang.split('-')[0]}&ceid={lang}"
                )
                try:
                    feed = feedparser.parse(url, agent=USER_AGENT)
                except Exception as e:  # feedparser 通常不抛错，防御性兜底
                    self.log.warning("google_news[%s] '%s' 解析失败: %s", lang, query, e)
                    continue

                prefix = "google_news_social" if social else "google_news"
                fetched = []
                for e in feed.entries[:max_results]:
                    title = e.get("title", "")
                    if not title:
                        continue
                    published = e.get("published") or e.get("published_parsed") or ""
                    published_at = str(published) if isinstance(published, str) else ""
                    # 兜底过滤：丢弃早于时间窗口的内容（after: 是搜索操作符，双保险）
                    pub_dt = _parse_published(published_at)
                    if pub_dt is not None and pub_dt < cutoff:
                        continue
                    content = e.get("summary") or e.get("description") or ""
                    src = e.get("source") or {}
                    item = NewsItem(
                        title=title,
                        content=content,
                        url=e.get("link", ""),
                        source=f"{prefix}:{lang}",
                        published_at=published_at,
                        language="zh" if lang.startswith("zh") else "en",
                        keywords=[query],
                        media=str(src.get("title", "") or "").strip(),
                        raw={
                            "feed": feed.feed.get("title", ""),
                            "media_href": str(src.get("href", "") or ""),
                            "social": social,
                            "lookback_days": lookback_days,
                        },
                    )
                    item.item_id = md5(item.url or item.title)
                    fetched.append(item)
                out.extend(fetched)
                self.log.info(
                    "google_news%s[%s] '%s' → %d 条",
                    "_social" if social else "", lang, query, len(fetched),
                )

        return out
