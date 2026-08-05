"""Agent1 信息发现：调用所有启用的数据源，去重、时效过滤。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from ..models import NewsItem
from ..sources import build_sources
from ..utils import CN_TZ, dedupe, norm_text, now_cn

logger = logging.getLogger("tourism_signal.agents.discovery")


def _parse_time(s: str):
    """解析发布时间。有明确时区标记（GMT/ISO带时区）按 UTC 解析保留；
    无时区标记的第一时间，说明是东八区本地时间（如热搜榜 `YYYY-MM-DD HH:MM:SS`），按东八区解释。"""
    if not s:
        return None
    s = s.strip()
    # RFC822（Google News 等）：Wed, 29 Jul 2026 21:05:53 GMT（GMT = +0）
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        pass
    # 无时区标记的纯数字时间（热搜榜/部分 RSS 的东八区时间）
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            # 无时区标记 → 视为东八区本地时间（与 now_cn 同一基准），直接挂东八区
            return dt.replace(tzinfo=CN_TZ)
        except ValueError:
            continue
    return None


def _recent(item: NewsItem) -> bool:
    """只保留东八区时间「昨天 00:00 至现在」窗口内的内容。解析失败则保留（尽力而为）。"""
    dt = _parse_time(item.published_at)
    if dt is None:
        return True
    now = now_cn()
    # 窗口起点：东八区昨天 00:00
    start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    dt_cn = dt.astimezone(CN_TZ)  # 统一转到东八区比较
    return start <= dt_cn <= now


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
