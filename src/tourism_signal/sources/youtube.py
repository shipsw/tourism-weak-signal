"""YouTube 数据源：游客 Vlog（视频）+ 视频评论（一手体验反馈）。

两阶段抓取：
1. search.list 按查询搜相关视频（返回标题/描述/频道）
2. commentThreads.list 抓取高相关视频的评论（游客一手反馈，价值更高）

配额：每个 API key 每天 10000 单位。
- search.list = 100 单位/次
- commentThreads.list = 1 单位/次
多个 key 通过 .env 的 YOUTUBE_API_KEYS 逗号分隔轮换，规避单 key 日配额。

注意：YouTube 评论接口在部分视频上可能关闭（commentsDisabled），失败则跳过。
"""
from __future__ import annotations

import os
import re

import requests

from ..models import NewsItem
from ..utils import md5
from .base import BaseSource

USER_AGENT = "tourism-weak-signal-agent/0.1"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&").strip()


class YouTubeSource(BaseSource):
    name = "youtube"

    def __init__(self, settings=None):
        super().__init__(settings)
        raw = self.settings.get("api_keys") or os.getenv("YOUTUBE_API_KEYS", "")
        self.api_keys = [k.strip() for k in str(raw).split(",") if k.strip()]
        self._key_idx = 0

    # --- key 轮换 ---
    def _next_key(self) -> str:
        if not self.api_keys:
            return ""
        k = self.api_keys[self._key_idx % len(self.api_keys)]
        self._key_idx += 1
        return k

    def _get(self, path: str, params: dict) -> dict:
        """带 key 轮换的 GET：配额耗尽(403 quotaExceeded)时自动换下一个 key。"""
        if not self.api_keys:
            return {}
        tried = 0
        n = len(self.api_keys)
        while tried < n:
            key = self._next_key()
            p = {**params, "key": key}
            try:
                r = requests.get(f"{YOUTUBE_API}/{path}", params=p, headers={"User-Agent": USER_AGENT}, timeout=25)
            except Exception:
                tried += 1
                continue
            if r.status_code == 200:
                return r.json()
            # 配额/权限问题 → 换 key
            tried += 1
        return {}

    def _search_videos(self, query: str, max_results: int, lookback_days: int) -> list[dict]:
        """搜索视频，返回精简条目列表。"""
        from datetime import datetime, timedelta, timezone

        published_after = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = self._get("search", {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": max_results,
            "order": "date",
            "publishedAfter": published_after,
            "relevanceLanguage": "en",
        })
        out = []
        for it in data.get("items", []):
            vid = it.get("id", {}).get("videoId", "")
            sn = it.get("snippet", {})
            if not vid:
                continue
            out.append({
                "video_id": vid,
                "title": sn.get("title", ""),
                "description": sn.get("description", ""),
                "channel": sn.get("channelTitle", ""),
                "published_at": sn.get("publishedAt", ""),
                "url": f"https://www.youtube.com/watch?v={vid}",
            })
        return out

    def _fetch_comments(self, video_id: str, max_comments: int) -> list[dict]:
        """抓取视频评论（一手游客反馈）。"""
        data = self._get("commentThreads", {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": min(max_comments, 100),
            "order": "relevance",
            "textFormat": "plainText",
        })
        out = []
        for it in data.get("items", []):
            sn = it.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            out.append({
                "author": sn.get("authorDisplayName", ""),
                "text": sn.get("textDisplay", ""),
                "like_count": sn.get("likeCount", 0),
                "published_at": sn.get("publishedAt", ""),
            })
        return out

    def fetch(self) -> list[NewsItem]:
        queries = self.settings.get("queries", [])
        max_results = int(self.settings.get("max_results", 15))
        lookback_days = int(self.settings.get("lookback_days", 7))
        fetch_comments = bool(self.settings.get("fetch_comments", True))
        max_comments = int(self.settings.get("max_comments", 20))
        min_likes = int(self.settings.get("comment_min_likes", 0))

        out: list[NewsItem] = []
        for q in queries:
            query = q.get("q", "")
            if not query:
                continue
            videos = self._search_videos(query, max_results, lookback_days)
            self.log.info("youtube '%s' → %d 视频", query, len(videos))

            for v in videos:
                # 1) 视频本身作为一条
                item = NewsItem(
                    title=v["title"],
                    content=(v["title"] + " " + v["description"])[:1000],
                    url=v["url"],
                    source="youtube:video",
                    published_at=v["published_at"],
                    language="en",
                    keywords=[query],
                    media=v["channel"],
                    raw={"video_id": v["video_id"], "channel": v["channel"], "kind": "video"},
                )
                item.item_id = md5("yt:video:" + v["video_id"])
                out.append(item)

                # 2) 高相关视频的评论（一手反馈）
                if not fetch_comments:
                    continue
                comments = self._fetch_comments(v["video_id"], max_comments)
                for c in comments:
                    if c["like_count"] < min_likes:
                        continue
                    text = _strip_html(c["text"])
                    if len(text) < 15:  # 太短的评论信息量低
                        continue
                    cit = NewsItem(
                        title=f"[评论] {v['title'][:60]}",
                        content=text,
                        url=v["url"],
                        source="youtube:comment",
                        published_at=c["published_at"],
                        language="en",
                        keywords=[query],
                        media=c["author"],  # 评论作者即发布者
                        raw={"video_id": v["video_id"], "channel": v["channel"],
                             "author": c["author"], "like_count": c["like_count"],
                             "video_title": v["title"], "kind": "comment"},
                    )
                    cit.item_id = md5("yt:comment:" + v["video_id"] + ":" + c["author"] + ":" + text[:40])
                    out.append(cit)
            self.log.info("youtube '%s' → 含评论共 %d 条", query, len(videos))

        return out
