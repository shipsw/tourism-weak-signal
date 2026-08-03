"""Agent3 弱信号判断（核心模块）。

流程：
1. 主题抽取：LLM 为每条过滤后的候选打主题标签
2. 聚类：同主题归组，统计客观特征（条数/来源数/语言数/方向数）
3. 五维评分：LLM 基于客观统计 + 内容对每组评分
4. 加权汇总：总分 = Σ(维度分 × 权重)，分级 普通/观察/重点关注

核心思想：单条投诉价值低；多国游客、多平台出现类似体验问题，价值提高。
"""
from __future__ import annotations

import difflib
import json
import logging
from typing import Any

from ..llm import LLMClient
from ..models import FilteredCandidate, NewsItem, SignalGroup, SignalScore
from ..utils import truncate

logger = logging.getLogger("tourism_signal.agents.weak_signal")

THEME_SYSTEM = """你是出入境旅游研究助理。为每条出入境旅游信息抽取一个简短的主题标签（中文，≤12字），并给出一句话摘要。
主题标签应概括"发生了什么"：如「免签政策调整」「外国游客抱怨支付不便」「某航线开通」。
多条信息如果说的是同一件事/同一类问题，应使用相同或高度相近的主题标签。

输出 JSON（必须包含 "themes" 数组）：
{"themes": [{"id": "<输入id>", "theme": "主题标签", "summary": "一句话摘要"}]}"""

SCORE_SYSTEM = """你是出入境旅游弱信号研判专家。对一组相关信息进行五维评分，每维 0-10 分：

1. 新颖性 novelty：是否出现新的趋势、变化或苗头（不是已知热点）
2. 重复性 repetition：是否多个来源出现类似信息（信息量=条数）
3. 扩散性 diffusion：是否跨平台、跨地区、跨国传播
4. 影响性 impact：是否可能影响出入境旅游发展（政策/市场/体验层面）
5. 持续性 sustainability：是否可能持续发展、演变

评分原则：
- 单条孤立投诉价值低（novelty 可高但 repetition/impact 低）
- 多个国家游客、多个平台出现类似体验问题 → repetition/impact 显著提高
- 政策类信号通常 impact 高

我将提供每组信息的客观统计（条数/来源数/语言数）和内容列表。
输出 JSON（必须包含 "groups" 数组，数组长度与输入一致）：
{"groups": [{"theme": "<主题>", "novelty": 0-10, "repetition": 0-10, "diffusion": 0-10, "impact": 0-10, "sustainability": 0-10, "reason": "评分理由（结合证据）"}]}"""


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _extract_themes(llm: LLMClient, cands: list[FilteredCandidate], batch_size: int, max_chars: int) -> dict[str, dict]:
    """返回 {item_id: {"theme":..., "summary":...}}"""
    themes: dict[str, dict] = {}
    for chunk in _chunks(cands, batch_size):
        payload = [
            {"id": c.item.item_id, "title": c.item.title, "content": truncate(c.item.content, max_chars)}
            for c in chunk
        ]
        try:
            data = llm.chat_json(THEME_SYSTEM, json.dumps(payload, ensure_ascii=False))
            tlist = data.get("themes", []) if isinstance(data, dict) else data
            for t in tlist:
                themes[str(t.get("id", ""))] = {
                    "theme": str(t.get("theme", "")).strip(),
                    "summary": str(t.get("summary", "")).strip(),
                }
        except Exception as e:
            logger.warning("主题抽取失败（该批使用标题兜底）: %s", e)
            for c in chunk:
                themes[c.item.item_id] = {"theme": c.item.title[:20], "summary": c.item.title}
    return themes


def _merge_stats(target: SignalGroup, other: SignalGroup):
    """合并两组的客观统计。"""
    target.items.extend(other.items)
    keys = {"sources", "languages", "directions", "types"}
    for k in keys:
        target.stats[k] = sorted(set(target.stats.get(k, [])) | set(other.stats.get(k, [])))
    target.stats["n_items"] = len(target.items)
    target.stats["n_sources"] = len(target.stats["sources"])
    target.stats["n_languages"] = len(target.stats["languages"])
    target.stats["n_directions"] = len(target.stats["directions"])


def _substr_overlap(a: str, b: str, n: int = 4) -> bool:
    """是否包含至少 n 个字符的公共子串（中文主题合并用）。"""
    if len(a) < n or len(b) < n:
        return False
    return any(b[i : i + n] in a for i in range(len(b) - n + 1))


def _merge_similar(groups: list[SignalGroup]) -> list[SignalGroup]:
    """按主题标签相似度合并相近分组（避免同一条新闻被拆成多组）。"""
    merged: list[SignalGroup] = []
    for g in groups:
        placed = False
        for m in merged:
            ratio = difflib.SequenceMatcher(None, g.theme, m.theme).ratio()
            if ratio >= 0.55 or _substr_overlap(g.theme, m.theme):
                _merge_stats(m, g)
                placed = True
                break
        if not placed:
            merged.append(g)
    merged.sort(key=lambda g: g.stats["n_items"], reverse=True)
    return merged


def _group(cands: list[FilteredCandidate], themes: dict[str, dict]) -> list[SignalGroup]:
    """按主题标签聚类，并统计客观特征。"""
    buckets: dict[str, list[FilteredCandidate]] = {}
    for c in cands:
        t = themes.get(c.item.item_id, {}).get("theme", "") or c.item.title[:20]
        buckets.setdefault(t, []).append(c)

    groups: list[SignalGroup] = []
    for theme, members in buckets.items():
        sources = {m.item.source.split(":")[0] for m in members}
        languages = {m.item.language for m in members}
        directions = {m.result.direction for m in members}
        types = {m.result.type for m in members}
        social = any(
            m.item.source.startswith(("google_news_social", "reddit"))
            for m in members
        )
        groups.append(
            SignalGroup(
                theme=theme,
                items=[m.item for m in members],
                stats={
                    "n_items": len(members),
                    "n_sources": len(sources),
                    "sources": sorted(sources),
                    "n_languages": len(languages),
                    "languages": sorted(languages),
                    "n_directions": len(directions),
                    "directions": sorted(directions),
                    "types": sorted(t for t in types if t),
                    "social": social,
                },
            )
        )
    # 信息量大的组优先评分
    groups.sort(key=lambda g: g.stats["n_items"], reverse=True)
    return _merge_similar(groups)


def _score_groups(llm: LLMClient, cfg, groups: list[SignalGroup]) -> list[SignalGroup]:
    weights = cfg.weights
    thresholds = cfg.thresholds
    max_groups = cfg.max_groups
    top = groups[:max_groups]

    payload = []
    for g in top:
        payload.append(
            {
                "theme": g.theme,
                "stats": g.stats,
                "items": [
                    {"title": i.title, "content": truncate(i.content, int(cfg.llm.get("max_content_chars", 500)))}
                    for i in g.items[:5]  # 每组最多看 5 条，控制 token
                ],
            }
        )
    try:
        data = llm.chat_json(SCORE_SYSTEM, json.dumps(payload, ensure_ascii=False))
        glist = data.get("groups", []) if isinstance(data, dict) else data
        scored: dict[str, dict] = {str(g.get("theme", "")): g for g in glist}
    except Exception as e:
        logger.warning("弱信号评分失败，使用启发式: %s", e)
        scored = {}

    for g in top:
        s = scored.get(g.theme, {})
        score = SignalScore(
            novelty=_num(s.get("novelty"), 3.0),
            repetition=_num(s.get("repetition"), min(6.0, g.stats["n_items"] * 2)),
            diffusion=_num(s.get("diffusion"), min(6.0, g.stats["n_sources"] * 1.5 + g.stats["n_languages"])),
            impact=_num(s.get("impact"), 4.0),
            sustainability=_num(s.get("sustainability"), 4.0),
            reason=str(s.get("reason", "") or "启发式评分"),
        )
        score.total = round(
            score.novelty * weights.get("novelty", 0.25)
            + score.repetition * weights.get("repetition", 0.20)
            + score.diffusion * weights.get("diffusion", 0.15)
            + score.impact * weights.get("impact", 0.25)
            + score.sustainability * weights.get("sustainability", 0.15),
            1,
        )
        score.level = _level(score.total, thresholds)
        g.score = score
        g.summary = ""

    # 未进入评分的组也补上启发式分数（信息量小，通常为普通）
    for g in groups[max_groups:]:
        score = SignalScore(
            novelty=3.0, repetition=min(5.0, g.stats["n_items"] * 2),
            diffusion=min(4.0, g.stats["n_sources"] * 1.5),
            impact=3.0, sustainability=3.0, reason="未进入LLM评分，启发式兜底",
        )
        score.total = round(
            score.novelty * weights.get("novelty", 0.25)
            + score.repetition * weights.get("repetition", 0.20)
            + score.diffusion * weights.get("diffusion", 0.15)
            + score.impact * weights.get("impact", 0.25)
            + score.sustainability * weights.get("sustainability", 0.15),
            1,
        )
        score.level = _level(score.total, thresholds)
        g.score = score

    groups.sort(key=lambda g: g.score.total if g.score else 0, reverse=True)
    return groups


def _num(v, default: float) -> float:
    try:
        f = float(v)
        return max(0.0, min(10.0, f))
    except (TypeError, ValueError):
        return default


def _level(total: float, thresholds: dict) -> str:
    if total >= float(thresholds.get("focus", 7)):
        return "重点关注"
    if total > float(thresholds.get("normal", 3)):
        return "观察信息"
    return "普通信息"


def weak_signal_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    filtered: list[FilteredCandidate] = state["filtered"]
    llm = LLMClient(cfg)
    batch_size = int(cfg.llm.get("batch_size", 8))
    max_chars = int(cfg.llm.get("max_content_chars", 500))

    if not filtered:
        logger.info("无候选通过过滤，跳过弱信号判断")
        return {"signals": [], "stats": {**state.get("stats", {}), "signal_groups": 0}}

    themes = _extract_themes(llm, filtered, batch_size, max_chars)
    groups = _group(filtered, themes)
    groups = _score_groups(llm, cfg, groups)

    n_focus = sum(1 for g in groups if g.score and g.score.level == "重点关注")
    n_observe = sum(1 for g in groups if g.score and g.score.level == "观察信息")
    stats = state.get("stats", {})
    stats.update({"signal_groups": len(groups), "focus": n_focus, "observe": n_observe})
    logger.info("弱信号判断完成：%d 组，重点关注 %d，观察 %d", len(groups), n_focus, n_observe)
    return {"signals": groups, "stats": stats}
