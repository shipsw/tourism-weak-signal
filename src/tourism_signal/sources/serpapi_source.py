"""SerpApi 数据源：真实 Google 搜索，抓取社媒（X/Instagram/Facebook 等）与新闻。

设计要点（沿用既有实测经验）：
- 每日预算控制：SerpApi 按次计费，用 daily_budget 限制每天最多搜索次数，
  超出则跳过（避免超额）。
- 用量记录：写入 outputs/data/serpapi_usage.json（按日期累计），跨天自动重置。
- 状态写入：写入 outputs/data/serpapi_status.json 供网页展示剩余额度。
- 容错：任何查询失败只 warning，不中断流水线。

引擎：
- google_news：支持 site: 语法，可覆盖 X 等平台（实测 twitter/tiktok 引擎已下线）
- google：网页搜索
- bing：备用网页搜索

配置示例：
  serpapi:
    enabled: true
    engine: google_news          # google_news | google | bing
    daily_budget: 5              # 每天最多搜索次数
    max_results: 15
    queries:
      - { lang: "en-US", q: "site:x.com China travel experience" }
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..models import NewsItem
from ..utils import md5
from .base import BaseSource

USER_AGENT = "Mozilla/5.0 (compatible; tourism-weak-signal-agent/0.1)"
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
ROOT = Path(__file__).resolve().parents[3]  # 项目根


def _parse_google_news(r: dict) -> tuple[str, str, str, str, str]:
    """google_news 引擎结果 → (title, link, snippet, date, source_name)。"""
    return (
        r.get("title", "") or "",
        r.get("link", "") or "",
        r.get("snippet", "") or "",
        r.get("iso_date", "") or r.get("date", "") or "",
        r.get("source", {}).get("name", "") if isinstance(r.get("source"), dict) else (r.get("source") or ""),
    )


def _parse_youtube(r: dict) -> tuple[str, str, str, str, str]:
    ch = r.get("channel", {})
    return (
        r.get("title", "") or "",
        r.get("link", "") or "",
        r.get("description", "") or "",
        r.get("published_date", "") or "",
        ch.get("name", "") if isinstance(ch, dict) else "",
    )


def _parse_bing(r: dict) -> tuple[str, str, str, str, str]:
    return (
        r.get("title", "") or "",
        r.get("link", "") or "",
        r.get("snippet", "") or "",
        "",
        r.get("displayed_link", "") or "",
    )


def _parse_google(r: dict) -> tuple[str, str, str, str, str]:
    """google 网页搜索 → (title, link, snippet, date, source_name)。"""
    return (
        r.get("title", "") or "",
        r.get("link", "") or "",
        r.get("snippet", "") or "",
        r.get("date", "") or "",
        r.get("source", "") or r.get("displayed_link", "") or "",
    )


_PARSERS = {"google_news": _parse_google_news, "google": _parse_google,
            "youtube": _parse_youtube, "bing": _parse_bing}
_RESULT_KEYS = {"google_news": "news_results", "google": "organic_results",
                "youtube": "video_results", "bing": "organic_results"}


class SerpApiSource(BaseSource):
    name = "serpapi"

    def __init__(self, settings=None):
        super().__init__(settings)
        self.api_key = self.settings.get("api_key") or os.getenv("SERPAPI_API_KEY", "")
        self.daily_budget = int(self.settings.get("daily_budget", 3))
        self.usage_file = ROOT / "outputs/data/serpapi_usage.json"
        self.status_file = ROOT / "outputs/data/serpapi_status.json"

    # ---------- 用量记录（防超支）----------
    def _read_usage(self) -> dict:
        try:
            data = json.loads(self.usage_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_usage(self, usage: dict) -> None:
        try:
            self.usage_file.parent.mkdir(parents=True, exist_ok=True)
            self.usage_file.write_text(json.dumps(usage, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            self.log.warning("SerpAPI 用量记录失败: %s", e)

    def _write_status(self, used: int, success: int, failed: int, errors: list) -> None:
        try:
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            self.status_file.write_text(json.dumps({
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "used": used, "budget": self.daily_budget,
                "remaining": max(0, self.daily_budget - used),
                "success": success, "failed": failed, "errors": errors[:5],
            }, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:
            self.log.warning("SerpAPI 状态写入失败: %s", e)

    # ---------- 搜索 ----------
    def _search(self, engine: str, query: str, lang: str, max_results: int) -> tuple[list, str]:
        params = {"engine": engine, "q": query, "api_key": self.api_key}
        if engine == "google_news":
            params["hl"] = "zh-cn" if lang.startswith("zh") else "en"
            params["gl"] = "cn" if lang.startswith("zh") else "us"
        elif engine == "google":
            params["hl"] = "zh-cn" if lang.startswith("zh") else "en"
            params["gl"] = "cn" if lang.startswith("zh") else "us"
            params["num"] = min(max_results, 20)
        elif engine == "youtube":
            params["hl"] = "zh-CN" if lang.startswith("zh") else "en"
        else:
            params["count"] = min(max_results, 20)
        for attempt in range(2):
            try:
                r = requests.get(SERPAPI_ENDPOINT, params=params,
                                 headers={"User-Agent": USER_AGENT}, timeout=30)
                data = r.json()
                if "error" in data:
                    return [], str(data["error"])[:150]
                key = _RESULT_KEYS.get(engine, "organic_results")
                return data.get(key, []) or [], ""
            except Exception as e:
                if attempt == 0:
                    time.sleep(1)
                    continue
                return [], str(e)[:150]
        return [], "无响应"

    def fetch(self) -> list[NewsItem]:
        if not self.api_key:
            self.log.warning("SerpAPI 未配置 API Key，跳过（.env 的 SERPAPI_API_KEY）")
            return []
        engines = self.settings.get("engines") or [self.settings.get("engine", "google_news")]
        if isinstance(engines, str):
            engines = [engines]
        daily_max = self.daily_budget
        max_results = int(self.settings.get("max_results", 15))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        usage = self._read_usage()
        if usage.get("date") != today:
            usage = {"date": today, "count": 0}
        used = int(usage.get("count", 0))

        out: list[NewsItem] = []
        success = failed = 0
        errors: list[str] = []
        for engine in engines:
            if engine not in _PARSERS:
                self.log.warning("SerpAPI 未知引擎: %s", engine)
                continue
            for q in self.settings.get("queries", []):
                if used >= daily_max:
                    self.log.warning("SerpAPI 当日预算已用尽（%d/%d），跳过剩余查询", used, daily_max)
                    break
                query = q.get("q", "")
                lang = q.get("lang", "en-US")
                if not query:
                    continue
                used += 1
                results, err = self._search(engine, query, lang, max_results)
                if err:
                    failed += 1
                    errors.append(f"{engine} '{query}': {err}")
                    self.log.warning("SerpAPI %s '%s' 失败（已跳过）: %s", engine, query, err)
                    continue
                success += 1
                parser = _PARSERS[engine]
                fetched = []
                for r in results[:max_results]:
                    title, link, snippet, date, srcname = parser(r)
                    if not title or not link:
                        continue
                    item = NewsItem(
                        title=title,
                        content=(title + " " + snippet)[:1000],
                        url=link,
                        source="serpapi:web",
                        published_at=date or "",
                        language="zh" if lang.startswith("zh") else "en",
                        keywords=[query],
                        media=srcname or _domain_of(link),
                        raw={"engine": engine, "snippet": snippet, "query": query,
                             "source_url": srcname, "social": True},
                    )
                    item.item_id = md5("serpapi:" + link)
                    fetched.append(item)
                out.extend(fetched)
                self.log.info("SerpAPI %s '%s' → %d 条", engine, query, len(fetched))
                time.sleep(0.5)
            if used >= daily_max:
                break

        usage["count"] = used
        self._save_usage(usage)
        self._write_status(used, success, failed, errors)
        return out


def _domain_of(url: str) -> str:
    try:
        from urllib.parse import urlsplit
        host = urlsplit(url).netloc
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""
