"""Agent2 服务质量过滤：判断信息是否属于旅游服务质量问题苗头。

客户为文旅部研究部门，已有舆情团队报热点。本层只保留：
- 政策类（热点中的政策变化，可能影响服务质量/管理）
- 服务质量负面信息（不文明行为/投诉/服务弱项/文化冲突/误解）
- 出入境游客体验反馈（境外 X/YouTube/Facebook/Instagram 等）

排除：纯营销推广、娱乐八卦、与旅游服务质量无关的社会新闻、已成型的舆情热点。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..llm import LLMClient
from ..models import FilteredCandidate, FilterResult, NewsItem
from ..utils import truncate

logger = logging.getLogger("tourism_signal.agents.filter")

SYSTEM_PROMPT = """你是旅游服务质量研究分析师，服务于文旅部研究部门。

判断每条信息是否属于需要报送的「旅游服务质量苗头问题」，范围包括：
1. 出入境旅游服务质量问题（重点）：入境游客不文明行为（公共场所失范/住宿干扰/消费诚信/公共卫生）、出境游客不文明行为、服务环节缺陷（交通/住宿/餐饮/景区/OTA）、宰客、文化冲突、误解与隐性不满
2. 政策类信息：旅游/出入境/免签/服务质量相关政策变化（可能影响服务质量与游客行为）
3. 游客体验反馈：境外（X/YouTube/Facebook/Instagram/Reddit）游客对中国旅游的体验、抱怨、困惑

不属于的范围（应过滤）：
- 纯营销/推广/种草（无问题性）
- 娱乐八卦、明星、与旅游服务质量无关的社会新闻
- 已成型的舆情热点（已被主流媒体广泛报道多日的事件——这类由舆情团队负责）
- 与旅游服务质量无关的经济/时政新闻

输出 JSON（必须包含 "results" 数组，数组长度与输入一致）：
{"results": [{"id": "<输入id>", "keep": true/false, "category": "入境不文明行为|出境不文明行为|服务环节问题|消费诚信|卫生安全|文化冲突/误解|体验反馈|政策|其他", "direction": "入境中国|出境中国|双向|其他|不确定", "reason": "一句话理由"}]}

category/direction 仅在 keep 为 true 时填写，否则填空字符串。"""

# 关键词兜底：LLM 失败时用启发式判断
SERVICE_KEYWORDS = [
    "不文明", "投诉", "宰客", "服务", "体验", "素质", "卫生", "安全", "纠纷",
    "乱扔", "喧哗", "插队", "吸烟", "无礼", "歧视", "冲突", "误解", "抱怨",
    "退款", "乱收费", "强制消费", "黑导游", "景区", "游客", "入境", "出境",
    "免签", "签证", "外国游客", "中国游客", "旅游", "酒店", "民宿", "旅行社",
    "complaint", "rude", "unfriendly", "scam", "overcharge", "dirty", "unsafe",
    "culture shock", "tourist", "travel", "visa", "hotel", "service",
]


def _fallback(item: NewsItem) -> FilterResult:
    text = (item.title + " " + item.content).lower()
    hit = [k for k in SERVICE_KEYWORDS if k.lower() in text]
    if not hit:
        return FilterResult(keep=False, reason="无服务质量关键词命中（兜底）")
    direction = ""
    if any(k in text for k in ["入境", "来华", "赴华", "外国游客", "visa-free", "inbound", "tourism china"]):
        direction = "入境中国"
    elif any(k in text for k in ["出境", "中国游客", "outbound"]):
        direction = "出境中国"
    return FilterResult(
        keep=True,
        direction=direction or "不确定",
        category="其他",
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
                    keep=str(r.get("keep", False)).lower() in ("true", "1"),
                    direction=str(r.get("direction", "") or ""),
                    category=str(r.get("category", "") or ""),
                    reason=str(r.get("reason", "") or ""),
                )
        except Exception as e:
            logger.warning("LLM 批量过滤失败，使用关键词兜底: %s", e)
            for it in chunk:
                results[it.item_id] = _fallback(it)

    filtered = [
        FilteredCandidate(item=it, result=results.get(it.item_id, _fallback(it)))
        for it in items
        if results.get(it.item_id, _fallback(it)).keep
    ]

    stats = state.get("stats", {})
    stats.update({"filtered_total": len(filtered), "filtered_out": len(items) - len(filtered)})
    logger.info("服务质量过滤完成：%d / %d 条通过", len(filtered), len(items))
    return {"filtered": filtered, "stats": stats}
