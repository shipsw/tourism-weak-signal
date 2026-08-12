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

REPORT_SYSTEM = """你是文旅部旅游服务质量研究助理，负责撰写《出入境旅游服务质量苗头问题日报》。输出 Markdown，必须严格遵守以下结构与标题层级（各级标题逐字使用）：

# 出入境旅游服务质量苗头问题日报

**日期**：<日期>

## 一、本期重点关注

<仅评分≥7分的信号。每条格式：>
### 1. <标题>
- **情况**：现象描述（谁、哪里、发生了什么，附数据/案例）
- **共性/扩散**：多来源/多平台/多国游客出现类似问题的证据
- **风险点**：对旅游服务质量与游客体验的潜在影响（如传导至舆论/政策）
- **来源**：链接列表
<如无≥7分信号，此章节写：今日无重点关注苗头。>

## 二、入境游服务质量相关

<关注外国游客对中国境内旅游服务质量的评价。内部按 境外数据 → 境内数据 顺序呈现。>
### （一）境外数据
#### （1）个人发布内容
<来自 X/YouTube/Facebook/Instagram/Reddit 等境外社交平台的游客个人发布的评价。每条单独呈现，格式：>
- **〔态度标注〕｜中文概括（一句话）**
  - 态度标注：称赞 / 吐槽 / 中性
  - 中文概括需包括提及的具体省份、城市、景点；一句话
  - 括号内附英文原文关键句
  - 末尾附链接
- 尽力呈现发布者：若数据提供发布者账号（author / handle / @账号），在条目开头或来源处注明，如 `@ChinaDaily` 或 `China Daily`；便于读者判断是个人、媒体还是机构账号
- 排版示例：`- 吐槽｜成都、张家界、上海：游客发布"推荐vs避雷"清单（@traveler2023，"Not every famous China experience is worth the hype..."）`
#### （2）媒体发布
- **官媒海外版**（中国官方媒体海外版：CCTV, CGTN, CNR, People's Daily, China Daily, Global Times, Xinhua 等，名单后续补充）的报道内容**合并提炼成一段话**概括，不单独列条；末尾附上引用的来源链接列表
- **非上述媒体的境外媒体**（如 South China Morning Post 南华早报、海峡时报 等）可**单独呈现**，每条格式参考个人发布但不必标注态度，改为：中文概括 + 要点，末尾附链接
- **务必正确归类**：凡来自官方媒体（China Daily/中国日报网、Xinhua、CGTN 等）及数据标明 publisher=官媒海外版 的内容，一律归入「媒体发布-官媒海外版」，**严禁放入「个人发布内容」**；只有当数据源为普通用户账号且 publisher=个人 时，才归入「个人发布内容」
### （二）境内数据
<来自境内平台/国内媒体的报道（如微博热搜、搜狐、新浪财经等）。合并雷同报道，按主题分段，每条或每段附来源链接。>

## 三、出境游服务质量相关

<关注中国游客出境旅游的评价与安全事件。格式与「二」一致，内部仍按 境外数据 → 境内数据 顺序。>
### （一）境外数据
#### （1）个人发布内容
<境外社交平台中国游客/在华发布者的出境旅游评价。格式同「二（一）1」。>
#### （2）媒体发布
<官媒海外版合并提炼成一段；其他境外媒体可单独呈现。>
### （二）境内数据
<境内平台报道的出境游评价/安全事件。合并雷同报道，附来源。>

## 四、其他苗头与政策动态

<双向/未定向/政策类等不便于归入二、三的信号。每条一行：- <主题>（评分X.X）：一句话要点。>

规则：
1. 章节与大标题必须逐字使用：一、本期重点关注 / 二、入境游服务质量相关 / 三、出境游服务质量相关 / 四、其他苗头与政策动态；以及「二、三」内部的「（一）境外数据」「（二）境内数据」「（1）个人发布内容」「（2）媒体发布」。空章节/子标题也要保留并写"（今日无）"
2. 只有「一、本期重点关注」可用"重点关注"表述；其余一律用"观察信息"
3. 每个条目/段落末尾必须附来源 Markdown 链接 `[具体媒体名](链接)`；
   媒体名优先使用数据中给出的 media / first_item.media / source_md 里的媒体名，
   严禁用"来源"等空泛占位
4. 语言：中文，研究风格，客观克制；聚焦"苗头性问题"而非已成热点
5. 重点关注条目必须含 情况/共性扩散/风险点/来源 四小节；二、三章个人发布条目按第（1）点规定格式

【个人发布原文保留要求】针对「二/三（一）境外数据（1）个人发布内容」及社交平台第一手内容：
- 用户通常无法直接打开原帖，因此**需同时呈现英文原文关键句与对应的中文态度概括**：先用态度标注（称赞/吐槽/中性），再给一句话中文概括（含省份/城市/景点），并在括号内附英文原文关键句
- 若同一分组含多个原始帖，尽量都列出（多平台/多账号出现类似内容更有价值）
- 英文原文关键句可适度截断，但不要丢失关键信息"""


EXPERIENCE_CATEGORIES = {"入境不文明行为", "出境不文明行为", "服务环节问题", "消费诚信", "卫生安全", "文化冲突/误解", "体验反馈"}
MAX_REPORT_GROUPS = 15  # 日报信号总数上限（控制输出 token，避免截断）

# 官媒海外版名单：其报道内容合并提炼成一段话，不单独列条（可按需扩充）
OFFICIAL_OVERSEAS_MEDIA = {
    "cctv", "cgtn", "cnr", "people's daily", "people daily", "peoples daily",
    "china daily", "global times", "xinhua", "新华社", "中国日报", "央视", "人民日报",
    "china.org.cn", "中国网", "安全部", "chinanews", "中新社", "国务院",
}
# 境内平台源前缀（国内热搜等）→ 归入“境内数据”
DOMESTIC_SOURCE_PREFIXES = ("hotsearch", "weibo", "xiaohongshu")


def _norm_media(name: str) -> str:
    """归一化媒体名用于官媒匹配：小写、去撇号/标点/空白。"""
    import re as _re
    # 去掉撇号（People's -> Peoples），标点与空白都归并
    return _re.sub(r"[\W_]+", " ", (name or "").lower().replace("'", "").replace("’", "")).strip()


def _is_official_media(name: str) -> bool:
    nm = _norm_media(name)
    if not nm:
        return False
    words = nm.split()  # 形如 "chinadaily com cn"
    compact = "".join(words)  # 紧凑无空格: "chinadailycomcn"
    norm_off = [_norm_media(o) for o in OFFICIAL_OVERSEAS_MEDIA if o]
    for o in norm_off:
        if not o:
            continue
        o_words = o.split()
        o_compact = "".join(o_words)
        # 标准命中：名单词/短语出现在媒体名
        if o in nm or nm in o:
            return True
        # 域名命中：中国日报域名 chinadaily.com.cn → 紧凑后含 chinadaily
        # 或媒体名紧凑形式命中名单紧凑形式
        if o_compact and (o_compact in compact or compact in o_compact):
            return True
    return False


def _extract_author(title: str = "", media: str = "") -> tuple[str, str]:
    """从标题/媒体信息尽力提取发布者名 (显示名, handle)。

    目前可解析 Google News 对 X(Twitter) 条目的标题格式：
      `显示名 (@handle) / Posts - x.com`  → (显示名, @handle)
      或其变体 / Highlights、/ Replies。
    其他平台 (Facebook/Instagram/YouTube) 的 Google News 标题通常不含账号名。
    """
    import re as _re
    t = (title or "").strip()
    if not t:
        return ("", "")
    # X 官方条目格式：显示名 (@handle) / Posts|Highlights|Replies
    m = _re.match(r"^(.+?)\s+\(@(\w[A-Za-z0-9_]{1,15})\)\s+/\s+(?:Posts|Highlights|Replies)", t)
    if m:
        return (m.group(1).strip(), "@" + m.group(2))
    return ("", "")


def _classify_source(item_source: str, media: str = "", title: str = "") -> tuple[str, str]:
    """返回 (数据归属, 发布者类别)。

    数据归属：境外 / 境内
    发布者类别：个人 / 官媒海外版 / 其他境外媒体 / 境内媒体

    判定优先级：
    - hotsearch/微博/小红书 源 → 境内
    - google_news / 其他 源 → 媒体发布，按 media 是否官媒区分
    - google_news_social / reddit 源 → 用标题解析发布者账号；若命中官媒名单归“官媒海外版”，
      否则归“个人”（避免媒体官方账号被误当成个人）
    """
    s = item_source or ""
    author_display, _handle = _extract_author(title, media)
    if any(s.startswith(p) for p in DOMESTIC_SOURCE_PREFIXES):
        return ("境内", "境内平台/媒体")
    if s.startswith(("google_news_social", "reddit", "github", "rss")):
        # 社会化源：优先用标题解析出的显示名判断是否官媒/机构
        if author_display and _is_official_media(author_display):
            return ("境外", "官媒海外版")
        return ("境外", "个人")
    if s.startswith("google_news") or s:
        if _is_official_media(media):
            return ("境外", "官媒海外版")
        return ("境外", "其他境外媒体")
    return ("境外", "其他")


def _direction_label(g: SignalGroup) -> str:
    dirs = g.stats.get("directions", [])
    if "入境中国" in dirs:
        return "入境"
    if "出境中国" in dirs and "入境中国" not in dirs:
        return "出境"
    return "其他/未定向"


def _is_focus(g: SignalGroup) -> bool:
    return bool(g.score and g.score.level == "重点关注")


def _in_report(g: SignalGroup) -> bool:
    """只纳入 >3 分信号（≤3 为普通信息，不入日报）。"""
    return bool(g.score and g.score.total > 3)


def _is_social(g: SignalGroup) -> bool:
    return bool(g.stats.get("social"))


def _media_name(i: dict) -> str:
    """从条目提取展示用媒体名：优先真实媒体（media），其次数据源标签，最后 URL 域名。
    社交源若解析到发布者账号（author/handle），则优先展示账号名（如 China Daily / @ChinaDaily）。"""
    # 对社交平台（X/Instagram/Facebook/YouTube）优先展示发布者账号
    src = (i.get("source") or "").split(":")
    if src and src[0] in ("google_news_social", "reddit"):
        author = (i.get("author") or "").strip()
        if author:
            return author
        return i.get("media") or ""
    m = (i.get("media") or "").strip()
    if m:
        return m
    if src and src[0] in ("google_news", "hotsearch", "serpapi"):
        return src[0]
    return ""


def _group_payload(g: SignalGroup, full: bool) -> dict:
    # 计算来源分类（依据本组代表性 items 的 source/media）
    main_origin = "境外"
    main_pub = "其他"
    for it in g.items[:5]:
        origin, pub = _classify_source(it.source, it.media, it.title)
        main_origin, main_pub = origin, pub
        break
    base = {
        "theme": g.theme,
        "total": g.score.total if g.score else 0,
        "level": g.score.level if g.score else "",
        "score_detail": g.score.to_dict() if g.score else {},
        "stats": g.stats,
        "direction": _direction_label(g),
        "data_origin": main_origin,   # 境外 / 境内
        "publisher": main_pub,        # 个人 / 官媒海外版 / 其他境外媒体 / 境内平台/媒体
        "items": [
            {"title": i.title, "url": i.url, "source": i.source, "media": i.media, "author": _extract_author(i.title, i.media)[0], "origin": _classify_source(i.source, i.media, i.title)[0]}
            for i in g.items[:5]
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
        for i in g.items[:5]:
            body = (i.content or "").strip()
            # 去除 HTML 标签，保留干净文本
            import re as _re
            clean = _re.sub(r"<[^>]+>", "", body).strip()
            title = (i.title or "").strip()
            if clean or title:
                author_display, handle = _extract_author(i.title, i.media)
                original_quotes.append({
                    "title": title,
                    "content": clean,
                    "url": i.url,
                    "author": author_display,
                    "handle": handle,
                    "platform": i.media or (_media_name({"source": i.source, "media": i.media, "author": author_display})),
                })
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
            "original_content": original_quotes,  # 原文（供 X/Instagram 等社交内容引用，含发布者账号）
        }
    return base


def _build_user_prompt(groups: list[SignalGroup], filtered_n: int, date: str) -> str:
    """按 方向 × 数据来源 路由信号到新的层级章节（仅纳入 >3 分信号）。

    新结构：
      一、本期重点关注（≥7 分）
      二、入境游服务质量相关：（一）境外数据 [（1）个人发布 ｜（2）媒体发布] ｜（二）境内数据
      三、出境游服务质量相关：同理
      四、其他苗头与政策动态（双向/政策/未定向兜底）
    """
    groups = [g for g in groups if _in_report(g)]
    groups = sorted(groups, key=lambda g: g.score.total if g.score else 0, reverse=True)[:MAX_REPORT_GROUPS]

    focus = [g for g in groups if _is_focus(g)]
    observe = [g for g in groups if not _is_focus(g)]

    def _dirs(g: SignalGroup) -> list:
        return g.stats.get("directions", [])

    def _assign_direction(g: SignalGroup) -> str:
        dirs = _dirs(g)
        if "入境中国" in dirs:
            return "入境"
        if "出境中国" in dirs and "入境中国" not in dirs:
            return "出境"
        return "其他"

    def _classify_bucket(g: SignalGroup) -> tuple[str, str]:
        """返回 (direction, origin)。"""
        origin = "境外"
        for it in g.items[:5]:
            o, _ = _classify_source(it.source, it.media, it.title)
            origin = o
            if o == "境内":
                break
        return (_assign_direction(g), origin)

    def _group_publisher(g: SignalGroup) -> str:
        """确定整组的发布者类别：若任一 item 命中官媒/境内，则整组按媒体归属处理，
        避免把官方媒体（如 China Daily X 账号）与普通游客个人混为一谈。"""
        pubs = []
        for it in g.items[:8]:
            _, pub = _classify_source(it.source, it.media, it.title)
            pubs.append(pub)
        # 优先级：境内 > 官媒 > 其他境外媒体 > 个人
        for pref in ("境内平台/媒体", "官媒海外版", "其他境外媒体"):
            if pref in pubs:
                return pref
        return "个人"

    # 观察信号分桶：按键 (direction, origin)
    buckets: dict[tuple[str, str], list[SignalGroup]] = {}
    for g in observe:
        dirn, origin = _classify_bucket(g)
        buckets.setdefault((dirn, origin), []).append(g)

    def _nested(official: list, other_media: list, personal: list, domestic: list) -> dict:
        """构建一个方向（入境/出境）的子结构。"""
        overseas_children: dict = {}
        if personal:
            overseas_children["（1）个人发布内容"] = [_group_payload(g, full=False) for g in personal]
        media_sub: dict = {}
        if official:
            media_sub["官媒海外版（合并提炼）"] = [_group_payload(g, full=False) for g in official]
        if other_media:
            media_sub["其他境外媒体"] = [_group_payload(g, full=False) for g in other_media]
        if media_sub:
            overseas_children["（2）媒体发布"] = media_sub

        return {
            "（一）境外数据": overseas_children if overseas_children else {"note": "（今日无）"},
            "（二）境内数据": [_group_payload(g, full=False) for g in domestic] if domestic else {"note": "（今日无）"},
        }

    # 入境 / 出境 各自的四类
    inbound = {"personal": [], "official": [], "other_media": [], "domestic": []}
    outbound = {"personal": [], "official": [], "other_media": [], "domestic": []}
    others = []  # 双向/未定向 → 四、
    for g in observe:
        dirn, origin = _classify_bucket(g)
        if dirn == "其他":
            others.append(g)
            continue
        bucket = inbound if dirn == "入境" else outbound
        pub = _group_publisher(g)
        if origin == "境内":
            bucket["domestic"].append(g)
        elif pub == "官媒海外版":
            bucket["official"].append(g)
        elif pub in ("其他境外媒体",):
            bucket["other_media"].append(g)
        else:
            bucket["personal"].append(g)

    sections = {}
    sections["一、本期重点关注"] = [_group_payload(g, full=True) for g in focus] if focus else {"note": "（今日无）"}
    sections["二、入境游服务质量相关"] = _nested(inbound["official"], inbound["other_media"], inbound["personal"], inbound["domestic"])
    sections["三、出境游服务质量相关"] = _nested(outbound["official"], outbound["other_media"], outbound["personal"], outbound["domestic"])
    sections["四、其他苗头与政策动态"] = [_group_payload(g, full=False) for g in others] if others else {"note": "（今日无）"}

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
            f"# 出入境旅游服务质量苗头问题日报\n\n**日期**：{date}\n\n"
            "## 一、本期重点关注\n\n（今日无）\n\n"
            "## 二、入境游服务质量相关\n\n### （一）境外数据\n\n#### （1）个人发布内容\n\n（今日无）\n\n#### （2）媒体发布\n\n（今日无）\n\n### （二）境内数据\n\n（今日无）\n\n"
            "## 三、出境游服务质量相关\n\n### （一）境外数据\n\n#### （1）个人发布内容\n\n（今日无）\n\n#### （2）媒体发布\n\n（今日无）\n\n### （二）境内数据\n\n（今日无）\n\n"
            "## 四、其他苗头与政策动态\n\n（今日无）\n"
        )
        path = _save_report(md, Path(cfg.output["report_dir"]), date)
        logger.info("无弱信号，已生成空日报 %s", path)
        return {"report": md, "report_path": str(path), "stats": {**state.get("stats", {}), "report_saved": True}}

    user = _build_user_prompt(groups, filtered_n, date)
    # max_tokens 增大到 8192：新版日报含重点关注四小节/多条个人发布原文/媒体合并段落，
    # 4096 易在生成 三/四 章节前被截断
    md = llm.chat_text(REPORT_SYSTEM, user, max_tokens=8192)
    # 兜底：确保来源显示为真实媒体名
    md = _fix_source_placeholders(md, _build_url_media_map(groups))
    path = _save_report(md, Path(cfg.output["report_dir"]), date)

    stats = state.get("stats", {})
    stats.update({"report_saved": True})
    logger.info("日报已生成：%s", path)
    return {"report": md, "report_path": str(path), "stats": stats}
