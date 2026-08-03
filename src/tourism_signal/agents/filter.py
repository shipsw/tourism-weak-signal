"""Agent2 旅游相关过滤：判断信息是否属于出入境旅游领域。"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..llm import LLMClient
from ..models import FilteredCandidate, FilterResult, NewsItem
from ..utils import truncate

logger = logging.getLogger("tourism_signal.agents.filter")

SYSTEM_PROMPT = """你是出入境旅游领域的信息分析师。

判断每条信息是否属于「出入境旅游」领域，包括：
- 入境中国旅游（外国游客来华、政策/免签、航线、体验反馈）
- 出境旅游（中国游客海外行为、目的地、体验、航线）
- 相关产业动态（OTA、航司、景区针对出入境游客的变化）

不属于的范围（应过滤）：普通社会新闻、非旅游移民/签证问题、与出入境旅游无关的国内游/本地事件。

输出 JSON（必须包含 "results" 数组，数组长度与输入一致）：
{"results": [{"id": "<输入id>", "tourism_related": true/false, "direction": "入境中国|出境中国|双向|其他|不确定", "type": "游客行为|政策|航线|目的地|体验反馈|产业动态|其他", "reason": "一句话理由"}]}

direction/type 仅在 tourism_related 为 true 时填写，否则填空字符串。"""

# 关键词兜底：LLM 失败时用启发式判断
TOURISM_KEYWORDS = [
    "免签", "签证", "入境", "出境", "过境", "外国游客", "海外游客", "中国游客",
    "来华", "赴华", "出境游", "入境游", "旅游", "航线", "直飞", "航班", "visa",
    "visa-free", "tourism", "tourist", "traveler", "traveller", "inbound", "outbound",
]


def _fallback(item: NewsItem) -> FilterResult:
    text = (item.title + " " + item.content).lower()
    hit = [k for k in TOURISM_KEYWORDS if k.lower() in text]
    if not hit:
        return FilterResult(tourism_related=False, reason="无旅游关键词命中（兜底）")
    direction = ""
    if any(k in text for k in ["入境", "来华", "赴华", "visa-free", "inbound", "tourism china"]):
        direction = "入境中国"
    elif any(k in text for k in ["出境", "中国游客", "outbound"]):
        direction = "出境中国"
    return FilterResult(
        tourism_related=True,
        direction=direction or "不确定",
        type="其他",
        reason=f"命中关键词: {','.join(hit[:3])}（兜底）",
    )


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def filter_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    items: list[NewsItem] = state["candidates"]
    llm = LLMClient(cfg)
    batch_size = int(cfg.llm.get("batch_size", 8))
    max_chars = int(cfg.llm.get("max_content_chars", 500))

    results: dict[str, FilterResult] = {}

    for chunk in _chunks(items, batch_size):
        payload = [
            {
                "id": it.item_id,
                "title": it.title,
                "content": truncate(it.content, max_chars),
            }
            for it in chunk
        ]
        try:
            data = llm.chat_json(SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False))
            rlist = data.get("results", []) if isinstance(data, dict) else data
            for r in rlist:
                results[str(r.get("id", ""))] = FilterResult(
                    tourism_related=str(r.get("tourism_related", False)).lower() in ("true", "1"),
                    direction=str(r.get("direction", "") or ""),
                    type=str(r.get("type", "") or ""),
                    reason=str(r.get("reason", "") or ""),
                )
        except Exception as e:
            logger.warning("LLM 批量过滤失败，使用关键词兜底: %s", e)
            for it in chunk:
                results[it.item_id] = _fallback(it)

    filtered = [
        FilteredCandidate(item=it, result=results.get(it.item_id, _fallback(it)))
        for it in items
        if results.get(it.item_id, _fallback(it)).tourism_related
    ]

    stats = state.get("stats", {})
    stats.update({"filtered_total": len(filtered), "filtered_out": len(items) - len(filtered)})
    logger.info("旅游过滤完成：%d / %d 条通过", len(filtered), len(items))
    return {"filtered": filtered, "stats": stats}
