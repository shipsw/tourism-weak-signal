"""国内平台热搜数据源：拉取 c3in 聚合热搜榜（百度/微博/头条/抖音/快手/B站/贴吧/知乎等）。

数据源：https://static.c3in.com:8443/tops/top_new.html
每个榜单是一个 JSON 数组，字段：title / hotnum / hotdate / ai_tag / extra / url。

用法：从热搜中筛选与"旅游/出入境/服务质量"相关的条目（热搜大部分是娱乐/时政，
必须预筛，避免噪声淹没信号与浪费 LLM 调用）。
"""
from __future__ import annotations

import json
import urllib.request
from urllib.parse import quote

from ..models import NewsItem
from ..utils import md5
from .base import BaseSource

BASE = "https://static.c3in.com:8443/tops/archive/jsons/"
USER_AGENT = "Mozilla/5.0 (compatible; tourism-weak-signal-agent/0.1)"

# 热搜预筛关键词：命中才保留（标题与旅游/出入境/服务质量相关）
HOT_KEYWORDS = [
    "旅游", "游客", "景区", "景点", "入境", "出境", "免签", "签证", "过境",
    "航班", "机场", "航空", "酒店", "民宿", "饭店", "旅行社", "导游", "研学",
    "宰客", "投诉", "服务", "不文明", "体验", "门票", "票价", "五一", "十一",
    "暑期", "避暑", "文旅", "国宝", "熊猫", "高铁", "火车票",
]

# ai_tag / extra 中的辅助命中
HOT_TAG_HITS = ["文旅", "旅游", "社会", "民生"]


class HotSearchSource(BaseSource):
    """聚合热搜榜数据源。"""

    name = "hotsearch"

    def _fetch_json(self, filename: str) -> list[dict]:
        url = BASE + quote(filename)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def _relevant(self, title: str, ai_tag: str, extra: str) -> bool:
        """热搜预筛：旅游/服务质量关键词命中，或标签含文旅/社会/民生。"""
        text = title + " " + (ai_tag or "") + " " + (extra or "")
        return any(k in text for k in HOT_KEYWORDS)

    def fetch(self) -> list[NewsItem]:
        boards = self.settings.get("boards", [])
        out: list[NewsItem] = []
        for b in boards:
            name = b.get("name", "")
            filename = b.get("file", "")
            max_n = int(b.get("max", 50))
            if not filename:
                continue
            try:
                rows = self._fetch_json(filename)
            except Exception as e:
                self.log.warning("热搜榜 %s 拉取失败: %s", name, e)
                continue

            fetched = []
            for r in rows[:max_n]:
                title = str(r.get("title", "") or "").strip()
                if not title or not self._relevant(title, str(r.get("ai_tag", "")), str(r.get("extra", ""))):
                    continue
                hotnum = r.get("hotnum", 0)
                item = NewsItem(
                    title=title,
                    content="",
                    url=str(r.get("url", "") or ""),
                    source=f"hotsearch:{name}",
                    published_at=str(r.get("hotdate", "") or ""),
                    language="zh",
                    keywords=["热搜"],
                    media=str(r.get("topname", "") or ""),
                    raw={
                        "hotnum": hotnum,
                        "toppos": r.get("toppos", 0),
                        "ai_tag": str(r.get("ai_tag", "") or ""),
                        "extra": str(r.get("extra", "") or ""),
                    },
                )
                item.item_id = md5(item.url or (name + title))
                fetched.append(item)
            out.extend(fetched)
            self.log.info("热搜榜 %s → 预筛 %d 条", name, len(fetched))
        return out
