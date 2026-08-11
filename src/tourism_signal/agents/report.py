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

REPORT_SYSTEM = """你是文旅部旅游服务质量研究助理，负责撰写《旅游服务质量苗头问题日报》。输出 Markdown，必须严格遵守以下结构（章节标题逐字使用）：

# 旅游服务质量苗头问题日报

**日期**：<日期>

## 一、本期重点关注

<仅评分≥7分的信号。每条格式：>
### 1. <标题>
- **情况**：现象描述（谁、哪里、发生了什么，附数据/案例）
- **共性/扩散**：多来源/多平台/多国游客出现类似问题的证据
- **风险点**：对旅游服务质量与游客体验的潜在影响（如传导至舆论/政策）
- **来源**：链接列表
<如无≥7分信号，此章节写：今日无重点关注苗头。>

## 二、入境游客服务与行为问题

<方向=入境中国的服务质量问题（不文明行为/服务弱项/文化冲突/误解）。每条一行：- <主题>（评分X.X，<类别>）：一句话要点>

## 三、出境游客行为与服务问题

<方向=出境中国的服务质量问题。每条一行：- <主题>（评分X.X，<类别>）：一句话要点>

## 四、其他苗头与政策动态

<其余 4-6 分观察信号（政策变化/双向/其他）。每条一行：- <主题>（评分X.X）：一句话要点>

## 五、境外平台游客体验反馈

<来自 X/YouTube/Facebook/Instagram/小红书 等社交平台的游客体验、抱怨、误解（第一手弱信号）。每条一行：- <主题>（评分X.X）：一句话要点>

规则：
1. 章节标题必须逐字使用上述五个标题；空章节也要保留标题并写"（今日无）"
2. 只有「一、本期重点关注」章节的条目可以使用"重点关注"表述；其余章节一律称"观察信息"
3. 每个条目末尾必须附来源，格式为 Markdown 链接 `[具体媒体名](链接)`；
   媒体名优先使用数据中给出的 media / first_item.media / source_md 里的媒体名，
   严禁使用"来源"或空泛文字占位（如"[来源](...)"中的"来源"二字必须换成真实媒体名）
4. 语言：中文，研究风格，客观克制；聚焦"苗头性问题"而非已成热点
5. 重点关注条目必须包含 情况/共性扩散/风险点/来源 四小节，其余条目保持一行简洁（末尾附来源）

【原文保留要求】针对「五、境外平台游客体验反馈」（X/Instagram 等社交平台的第一手内容）：
- 用户通常无法直接打开原帖，因此**必须同时呈现英文原文与对应的中文翻译**，便于筛选判断
- 当数据提供 original_content（含 title/content/url 的原文）时：先引用英文原文关键句，**紧接着给出准确对应的中文翻译/意译**（不要只给一句笼统的"中文提示"）
- 排版建议：„英文原句…"（译：中文翻译……）——即每条原文后都必有译文
- 若原文是英文，必须同时有英文原句和对应中文翻译，两者缺一不可；不要只给英文、也不要只给中文意译
- 同一组的 multiple 原始帖之间尽量都列出来（多平台、多账号出现类似内容更有价值）"""


EXPERIENCE_CATEGORIES = {"入境不文明行为", "出境不文明行为", "服务环节问题", "消费诚信", "卫生安全", "文化冲突/误解", "体验反馈"}
MAX_REPORT_GROUPS = 15  # 日报信号总数上限（控制输出 token，避免截断）


def _is_focus(g: SignalGroup) -> bool:
    return bool(g.score and g.score.level == "重点关注")


def _in_report(g: SignalGroup) -> bool:
    """只纳入 >3 分信号（≤3 为普通信息，不入日报）。"""
    return bool(g.score and g.score.total > 3)


def _is_social(g: SignalGroup) -> bool:
    return bool(g.stats.get("social"))


def _media_name(i: dict) -> str:
    """从条目提取展示用媒体名：优先真实媒体（media），其次数据源标签，最后 URL 域名。"""
    m = (i.get("media") or "").strip()
    if m:
        return m
    src = (i.get("source") or "").split(":")
    if src and src[0] in ("google_news", "hotsearch", "serpapi"):
        return src[0]
    return ""


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
        # 观察信息只给最紧凑的信息，但补充可直接引用的媒体来源
        items_md = base["items"]
        first_item = {}
        src_md = ""
        for it in items_md:
            name = _media_name(it)
            url = it.get("url", "") or ""
            if name and url:
                first_item = it
                src_md = f"[{name}]({url})"
                break
        if not src_md and items_md and items_md[0].get("url"):
            first_item = items_md[0]
            src_md = f"[来源]({items_md[0]['url']})"
        # 社交平台（X/Instagram 等）游客体验是用户无法直接打开的一手弱信号：
        # 必须保留原文/完整内容，便于用户筛选。
        original_quotes = []
        for i in g.items[:8]:
            body = (i.content or "").strip()
            if not body:
                continue
            # 去除 HTML 标签，保留干净文本
            import re as _re
            clean = _re.sub(r"<[^>]+>", "", body).strip()
            title = (i.title or "").strip()
            if clean or title:
                original_quotes.append({"title": title, "content": clean, "url": i.url})
        return {
            "theme": g.theme,
            "total": base["total"],
            "level": base["level"],
            "categories": g.stats.get("categories", []),
            "directions": g.stats.get("directions", []),
            "sources": g.stats.get("sources", []),
            "social": bool(g.stats.get("social")),
            "first_item": first_item,
            "media_name": first_item.get("media") or "",
            "source_md": src_md,   # 已拼好的 [媒体名](链接)，供 LLM 直接附在条目末尾
            "original_content": original_quotes,  # 原文（供 X/Instagram 等社交内容引用）
        }
    return base


def _build_user_prompt(groups: list[SignalGroup], filtered_n: int, date: str) -> str:
    """按方向/类别将信号路由到四个章节（仅纳入 >3 分信号）。"""
    groups = [g for g in groups if _in_report(g)]
    groups = sorted(groups, key=lambda g: g.score.total if g.score else 0, reverse=True)[:MAX_REPORT_GROUPS]

    focus = [g for g in groups if _is_focus(g)]
    observe = [g for g in groups if not _is_focus(g)]

    # 境外/社交平台游客体验单独成章（五），避免与新闻类混排
    social = [g for g in observe if _is_social(g)]
    others = [g for g in observe if not _is_social(g)]

    def _dirs(g: SignalGroup) -> list:
        return g.stats.get("directions", [])

    def _cats(g: SignalGroup) -> set:
        return set(g.stats.get("categories", []))

    def _assign(g: SignalGroup) -> str:
        """每个组只归入唯一章节，避免跨章节重复。
        优先级：入境问题 > 出境问题 > 其他/政策。
        """
        dirs = _dirs(g)
        cats = _cats(g)
        if "入境中国" in dirs and (not cats or cats & EXPERIENCE_CATEGORIES):
            return "二、入境游客服务与行为问题"
        if "出境中国" in dirs and "入境中国" not in dirs:
            return "三、出境游客行为与服务问题"
        return "四、其他苗头与政策动态"

    buckets = {"二、入境游客服务与行为问题": [], "三、出境游客行为与服务问题": [], "四、其他苗头与政策动态": []}
    for g in others:
        buckets[_assign(g)].append(g)

    def _sec(name: str, glist: list[SignalGroup], full: bool):
        if not glist:
            return {name: {"note": "（今日无）"}}
        return {name: [_group_payload(g, full) for g in glist]}

    sections = {}
    sections.update(_sec("一、本期重点关注", focus, full=True))
    sections.update(_sec("二、入境游客服务与行为问题", buckets["二、入境游客服务与行为问题"], full=False))
    sections.update(_sec("三、出境游客行为与服务问题", buckets["三、出境游客行为与服务问题"], full=False))
    sections.update(_sec("四、其他苗头与政策动态", buckets["四、其他苗头与政策动态"], full=False))
    sections.update(_sec("五、境外平台游客体验反馈", social, full=False))

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


def _build_url_media_map(groups: list[SignalGroup]) -> dict[str, str]:
    """建立 url → 媒体名 映射，供日报兜底替换[来源]占位用。"""
    m: dict[str, str] = {}
    for g in groups:
        for it in g.items:
            url = (it.url or "").strip()
            name = _media_name({"media": it.media, "source": it.source})
            if url and name:
                m.setdefault(url, name)
    return m


def _fix_source_placeholders(md: str, url_map: dict[str, str]) -> str:
    """兜底修复日报里的来源链接。

    1) 把 LLM 偷懒写的 `[来源](url)` 替换为 `[具体媒体名](url)`。
    2) 当 LLM 把原始 URL 截断（与已收录的完整 URL 匹配其前缀但长度更短）时，
       用数据库里的完整 URL 修正，并确保 markdown 链接闭合。
    """
    import re as _re

    # 已知的完整 URL → 媒体名 映射，按 URL 长度降序，优先匹配更长的完整 URL
    known = sorted(url_map.keys(), key=len, reverse=True)

    def _fix_truncated_url(url: str) -> str:
        """若 url 是某个已知完整 URL 的前缀但长度不足，返回完整 URL；否则原样。"""
        url = url.strip()
        # 去掉可能残留的 ?oc 或尾部不完整字符
        for full in known:
            if len(url) < len(full) and full.startswith(url):
                return full
        return url

    def repl_placeholder(mo: _re.Match):
        url = mo.group(1).strip()
        name = url_map.get(url)
        full = _fix_truncated_url(url)
        if name:
            return f"[{name}]({full})"
        if full != url:
            return f"[来源]({full})"
        return mo.group(0)

    # 先修复 [xxx](url) 且未闭合的链接（缺右括号），URL 可能被截断
    # 匹配 [media](url 直到行尾/`)），含截断但不闭合的情形
    def repl_incomplete(mo: _re.Match):
        label = mo.group(1)
        url = mo.group(2).strip()
        # 去掉尾部可能出现的非 URL 残字（如 `。`、`,`）
        url = url.rstrip('。，,；;:：')
        full = _fix_truncated_url(url)
        # 仅在能定位到已知完整 URL 时才修复（避免误伤微博/百度等含中文的链接）
        if full != url:
            # 正则已消费了链接（含可能的右括号），统一补一个右括号
            return f"[{label}]({full})"
        if not url:
            return mo.group(0)
        # URL 完整且能匹配到已知媒体名：若闭合正常则原样返回
        return mo.group(0)

    # 处理含截断/未闭合链接的段落：形如 [媒体](https://... 可能带/不带右括号
    md = _re.sub(
        r"\[([^]]+)\]\((https?://[^)\s\n]*)(\))?",
        repl_incomplete,
        md,
    )

    # 再修复 [来源](url) 占位（闭合形式）
    md = _re.sub(r"\[来源[0-9]*\]\(([^)]+)\)", repl_placeholder, md)
    return md


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
            f"# 旅游服务质量苗头问题日报\n\n**日期**：{date}\n\n"
            "## 一、本期重点关注\n\n（今日无）\n\n"
            "## 二、入境游客服务与行为问题\n\n（今日无）\n\n"
            "## 三、出境游客行为与服务问题\n\n（今日无）\n\n"
            "## 四、其他苗头与政策动态\n\n（今日无）\n\n"
            "## 五、境外平台游客体验反馈\n\n（今日无）\n"
        )
        path = _save_report(md, Path(cfg.output["report_dir"]), date)
        logger.info("无弱信号，已生成空日报 %s", path)
        return {"report": md, "report_path": str(path), "stats": {**state.get("stats", {}), "report_saved": True}}

    user = _build_user_prompt(groups, filtered_n, date)
    md = llm.chat_text(REPORT_SYSTEM, user, max_tokens=4096)
    # 兜底：确保来源显示为真实媒体名
    md = _fix_source_placeholders(md, _build_url_media_map(groups))
    path = _save_report(md, Path(cfg.output["report_dir"]), date)

    stats = state.get("stats", {})
    stats.update({"report_saved": True})
    logger.info("日报已生成：%s", path)
    return {"report": md, "report_path": str(path), "stats": stats}
