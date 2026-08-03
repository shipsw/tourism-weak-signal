"""Agent1 信息发现：调用所有启用的数据源，去重、时效过滤。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from ..models import NewsItem
from ..sources import build_sources
from ..utils import dedupe, norm_text, now_cn

logger = logging.getLogger("tourism_signal.agents.discovery")

RECENT_WINDOW_DAYS = 7  # 只保留近 7 天（尽力解析，解析失败则保留）


def _parse_time(s: str):
    if not s:
        return None
    s = s.strip()
    # RFC822（Google News 等）：Wed, 29 Jul 2026 21:05:53 GMT
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        pass
    # ISO/纯数字（热搜榜等）：2026-08-03 17:51:00 或 2026-08-03T17:51:00
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _recent(item: NewsItem) -> bool:
    dt = _parse_time(item.published_at)
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= now_cn() - timedelta(days=RECENT_WINDOW_DAYS)


def discovery_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    items: list[NewsItem] = []

    for src in build_sources(cfg):
        try:
            fetched = src.fetch()
            items.extend(fetched)
        except Exception as e:
            logger.exception("数据源 %s 失败（已跳过）: %s", getattr(src, "name", "?"), e)

    # 1) URL/标题去重
    items = dedupe(items, key_fn=lambda i: i.item_id or norm_text(i.title))
    # 2) 时效过滤（尽力而为）
    items = [i for i in items if _recent(i)]
    items.sort(key=lambda i: i.published_at, reverse=True)

    stats = state.get("stats", {})
    stats.update({"candidates_total": len(items)})
    logger.info("信息发现完成：候选 %d 条", len(items))
    return {"candidates": items, "stats": stats}
