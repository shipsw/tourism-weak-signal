"""Agent4 日报生成：将弱信号组织为《出入境旅游弱信号日报》(Markdown)。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..llm import LLMClient
from ..models import SignalGroup
from ..utils import today_str

logger = logging.getLogger("tourism_signal.agents.report")

REPORT_SYSTEM = """你是文旅研究助理，负责撰写《出入境旅游弱信号日报》。输出 Markdown，必须严格遵守以下结构（章节标题逐字使用）：

# 出入境旅游弱信号日报

**日期**：<日期>

## 一、重点关注趋势

<仅评分≥7分的信号。每条格式：>
### 1. <标题>
- **发现情况**：...
- **趋势判断**：...
- **关注建议**：...
<如无≥7分信号，此章节写：今日无重点关注信号。>

## 二、境外游客体验反馈

<境外游客来华体验类信号（方向=入境中国）。每条一行：- <主题>（综合评分X.X）：一句话要点>

## 三、中国游客海外旅游动态

<中国游客出境动态（方向=出境中国）。每条一行：- <主题>（综合评分X.X）：一句话要点>

## 四、其他值得关注信息

<其余 4-6 分观察信号（方向=双向/其他/不确定）。每条一行：- <主题>（综合评分X.X）：一句话要点>

## 五、社交媒体信号

<来自 X/Reddit/Quora/Facebook/YouTube/微博/小红书 等社交平台的讨论、单条帖子、个人分享（第一手弱信号）。每条一行：- <主题>（综合评分X.X）：一句话要点>

规则：
1. 章节标题必须逐字使用上述五个标题；空章节也要保留标题并写"（今日无）"
2. 只有「一、重点关注趋势」章节的条目可以使用"重点关注"表述；其余章节一律称"观察信息"，不得用"重点关注"字样
3. 内容必须基于我提供的数据，不得编造；每个条目附来源媒体与链接（Markdown 链接格式）
4. 语言：中文，研究风格，客观克制，不夸大
5. 重点关注条目必须包含 发现情况/趋势判断/关注建议 三小节，其余条目保持一行简洁"""


EXPERIENCE_TYPES = {"体验反馈", "游客行为"}
MAX_REPORT_GROUPS = 15  # 日报信号总数上限（控制输出 token，避免截断）


def _is_focus(g: SignalGroup) -> bool:
    return bool(g.score and g.score.level == "重点关注")


def _in_report(g: SignalGroup) -> bool:
    """只纳入 >3 分信号（≤3 为普通信息，不入日报）。"""
    return bool(g.score and g.score.total > 3)


def _is_social(g: SignalGroup) -> bool:
    return bool(g.stats.get("social"))


def _group_payload(g: SignalGroup, full: bool) -> dict:
    base = {
        "theme": g.theme,
        "total": g.score.total if g.score else 0,
        "level": g.score.level if g.score else "",
        "score_detail": g.score.to_dict() if g.score else {},
        "stats": g.stats,
        "items": [
            {"title": i.title, "url": i.url, "source": i.source, "media": i.media}
            for i in g.items[:8]
        ],
    }
    if not full:
        # 观察信息只给最紧凑的信息
        return {
            "theme": g.theme,
            "total": base["total"],
            "level": base["level"],
            "sources": g.stats.get("sources", []),
            "first_item": base["items"][0] if base["items"] else {},
        }
    return base


def _build_user_prompt(groups: list[SignalGroup], filtered_n: int, date: str) -> str:
    """按方向/类型将信号路由到四个章节（仅纳入 >3 分信号）。"""
    groups = [g for g in groups if _in_report(g)]
    groups = sorted(groups, key=lambda g: g.score.total if g.score else 0, reverse=True)[:MAX_REPORT_GROUPS]

    focus = [g for g in groups if _is_focus(g)]
    observe = [g for g in groups if not _is_focus(g)]

    # 社交媒体信号单独成章（五），避免与新闻类混排
    social = [g for g in observe if _is_social(g)]
    others = [g for g in observe if not _is_social(g)]

    def _dirs(g: SignalGroup) -> list:
        return g.stats.get("directions", [])

    def _types(g: SignalGroup) -> set:
        return set(g.stats.get("types", []))

    def _assign(g: SignalGroup) -> str:
        """每个组只归入唯一章节，避免跨章节重复。
        优先级：入境体验/行为 > 出境动态 > 入境政策/其他。
        """
        dirs = _dirs(g)
        types = _types(g)
        if "入境中国" in dirs and (not types or types & EXPERIENCE_TYPES):
            return "二、境外游客体验反馈"
        if "出境中国" in dirs and "入境中国" not in dirs:
            return "三、中国游客海外旅游动态"
        return "四、其他值得关注信息"

    buckets = {"二、境外游客体验反馈": [], "三、中国游客海外旅游动态": [], "四、其他值得关注信息": []}
    for g in others:
        buckets[_assign(g)].append(g)

    def _sec(name: str, glist: list[SignalGroup], full: bool):
        if not glist:
            return {name: {"note": "（今日无）"}}
        return {name: [_group_payload(g, full) for g in glist]}

    sections = {}
    sections.update(_sec("一、重点关注趋势", focus, full=True))
    sections.update(_sec("二、境外游客体验反馈", buckets["二、境外游客体验反馈"], full=False))
    sections.update(_sec("三、中国游客海外旅游动态", buckets["三、中国游客海外旅游动态"], full=False))
    sections.update(_sec("四、其他值得关注信息", buckets["四、其他值得关注信息"], full=False))
    sections.update(_sec("五、社交媒体信号", social, full=False))

    payload = {
        "date": date,
        "overview": f"今日通过过滤的候选 {filtered_n} 条，弱信号分组 {len(groups)} 组，其中重点关注 {len(focus)} 组、观察 {len(observe)} 组。",
        "sections": sections,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _save_report(md: str, report_dir: Path, date: str) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{date}.md"
    path.write_text(md, encoding="utf-8")

    # 更新索引 README.md
    index = report_dir / "README.md"
    lines = ["# 出入境旅游弱信号日报索引", ""]
    for p in sorted(report_dir.glob("????-??-??.md"), reverse=True):
        lines.append(f"- [{p.stem}]({p.name})")
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def report_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    groups: list[SignalGroup] = state["signals"]
    date = state["date"] or today_str()
    filtered_n = state.get("stats", {}).get("filtered_total", len(state.get("filtered", [])))
    llm = LLMClient(cfg)

    focus = [g for g in groups if _is_focus(g)]
    observe = [g for g in groups if g.score and g.score.total > 3]
    if not focus and not observe:
        md = (
            f"# 出入境旅游弱信号日报\n\n**日期**：{date}\n\n"
            "## 一、重点关注趋势\n\n（今日无）\n\n"
            "## 二、境外游客体验反馈\n\n（今日无）\n\n"
            "## 三、中国游客海外旅游动态\n\n（今日无）\n\n"
            "## 四、其他值得关注信息\n\n（今日无）\n\n"
            "## 五、社交媒体信号\n\n（今日无）\n"
        )
        path = _save_report(md, Path(cfg.output["report_dir"]), date)
        logger.info("无弱信号，已生成空日报 %s", path)
        return {"report": md, "report_path": str(path), "stats": {**state.get("stats", {}), "report_saved": True}}

    user = _build_user_prompt(groups, filtered_n, date)
    md = llm.chat_text(REPORT_SYSTEM, user, max_tokens=4096)
    path = _save_report(md, Path(cfg.output["report_dir"]), date)

    stats = state.get("stats", {})
    stats.update({"report_saved": True})
    logger.info("日报已生成：%s", path)
    return {"report": md, "report_path": str(path), "stats": stats}
