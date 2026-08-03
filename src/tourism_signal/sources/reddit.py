"""Reddit 数据源：旅游社区早期讨论与群体性反馈。

两种模式：
1. 官方 API（推荐）：.env 配置 REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET（免费档）
   → 使用 OAuth 认证请求，稳定可用
2. 匿名 JSON API（无密钥）：可能被 403 限流，失败时优雅降级不中断流程
"""
from __future__ import annotations

import os
import time
from urllib.parse import quote

import requests

from ..models import NewsItem
from ..utils import md5
from .base import BaseSource

USER_AGENT = "Mozilla/5.0 (compatible; tourism-weak-signal-agent/0.1; research)"


class RedditSource(BaseSource):
    name = "reddit"
    ANON_SEARCH = "https://www.reddit.com/r/{sub}/search.json"
    OAUTH_SEARCH = "https://oauth.reddit.com/r/{sub}/search"

    def __init__(self, settings=None):
        super().__init__(settings)
        self.client_id = self.settings.get("client_id") or os.getenv("REDDIT_CLIENT_ID", "")
        self.client_secret = self.settings.get("client_secret") or os.getenv("REDDIT_CLIENT_SECRET", "")
        self.access_token = None

    def _auth(self) -> bool:
        """申请 OAuth token（client_credentials，免费档）。"""
        if not (self.client_id and self.client_secret):
            return False
        try:
            r = requests.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": USER_AGENT},
                timeout=20,
            )
            self.access_token = r.json().get("access_token")
        except Exception as e:
            self.log.warning("Reddit OAuth 失败，回退匿名模式: %s", e)
            self.access_token = None
        return bool(self.access_token)

    def _request(self, sub: str, query: str, limit: int, sort: str):
        if self.access_token:
            resp = requests.get(
                self.OAUTH_SEARCH.format(sub=sub),
                params={
                    "q": query, "sort": sort, "t": "day",
                    "restrict_sr": "1", "limit": limit,
                },
                headers={
                    "User-Agent": USER_AGENT,
                    "Authorization": f"bearer {self.access_token}",
                },
                timeout=20,
            )
            return resp, False
        url = (
            f"{self.ANON_SEARCH.format(sub=sub)}"
            f"?q={quote(query)}&sort={sort}&t=day&restrict_sr=1&limit={limit}"
        )
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        return resp, True

    def fetch(self) -> list[NewsItem]:
        subreddits = self.settings.get("subreddits", [])
        queries = self.settings.get("queries", [])
        limit = int(self.settings.get("limit", 15))
        sort = self.settings.get("sort", "new")
        out: list[NewsItem] = []

        authed = self._auth()
        if authed:
            self.log.info("Reddit 使用官方 OAuth API")
        else:
            self.log.info("Reddit 未配置密钥，使用匿名模式（可能被限流）")

        for sub in subreddits:
            for q in queries:
                try:
                    resp, anon = self._request(sub, q, limit, sort)
                except requests.RequestException as e:
                    self.log.warning("reddit r/%s '%s' 请求失败: %s", sub, q, e)
                    continue

                if resp.status_code == 403:
                    self.log.warning("reddit r/%s 被限流(403)，跳过后续请求", sub)
                    break
                if resp.status_code != 200:
                    self.log.warning("reddit r/%s 返回 %s", sub, resp.status_code)
                    continue

                try:
                    data = resp.json()
                except ValueError:
                    continue

                children = data.get("data", {}).get("children", [])
                fetched = []
                for c in children:
                    d = c.get("data", {})
                    title = d.get("title", "")
                    if not title:
                        continue
                    selftext = (d.get("selftext") or "").strip()[:500]
                    item = NewsItem(
                        title=title,
                        content=selftext or title,
                        url="https://reddit.com" + d.get("permalink", ""),
                        source=f"reddit:{sub}",
                        published_at=time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(d.get("created_utc", 0))
                        ),
                        language="en",
                        keywords=[q],
                        media=f"reddit r/{sub}",
                        raw={"subreddit": sub, "score": d.get("score", 0), "social": True},
                    )
                    item.item_id = md5(item.url or item.title)
                    fetched.append(item)
                out.extend(fetched)
                self.log.info("reddit r/%s '%s' → %d 条", sub, q, len(fetched))
                time.sleep(1)  # 礼貌限速

        return out
