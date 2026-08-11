"""单元测试：工具函数 / 模型 / 配置 / 评分 / 流水线（LLM 打桩，无需网络与密钥）。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tourism_signal.agents.filter import filter_node
from tourism_signal.agents.report import report_node
from tourism_signal.agents.weak_signal import weak_signal_node, _level, _num
from tourism_signal.config import Config
from tourism_signal.models import (
    FilteredCandidate,
    FilterResult,
    NewsItem,
    SignalGroup,
    SignalScore,
)
from tourism_signal.utils import dedupe, norm_text, parse_json

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SRC_DIR))


def make_item(i: int, title: str = "", source: str = "google_news") -> NewsItem:
    it = NewsItem(
        title=title or f"测试标题 {i}",
        content="外国游客体验中国免签旅游，反馈良好。",
        url=f"https://example.com/{i}",
        source=source,
        published_at="2025-08-01T10:00:00Z",
        language="zh",
        keywords=["免签"],
    )
    it.item_id = f"id-{i}"
    return it


class TestUtils(unittest.TestCase):
    def test_parse_json_plain(self):
        self.assertEqual(parse_json('{"a": 1}'), {"a": 1})

    def test_parse_json_fence(self):
        text = '```json\n{"results": [{"id": "1"}]}\n```'
        self.assertEqual(parse_json(text), {"results": [{"id": "1"}]})

    def test_parse_json_nested_with_text(self):
        text = '说明：结果如下 {"groups": [{"a": {"b": 1}}]} 完毕'
        self.assertEqual(parse_json(text), {"groups": [{"a": {"b": 1}}]})

    def test_parse_json_list(self):
        self.assertEqual(parse_json('[{"id": 1}]'), [{"id": 1}])

    def test_parse_json_invalid(self):
        with self.assertRaises(ValueError):
            parse_json("完全没有json")

    def test_dedupe(self):
        items = [{"k": "a"}, {"k": "a"}, {"k": "b"}]
        out = dedupe(items, key_fn=lambda x: x["k"])
        self.assertEqual(len(out), 2)

    def test_norm_text(self):
        self.assertEqual(norm_text(" China, Visa-free! "), "chinavisafree")


class TestModels(unittest.TestCase):
    def test_newsitem_roundtrip(self):
        it = make_item(1)
        self.assertEqual(NewsItem.from_dict(it.to_dict()), it)

    def test_filterresult_roundtrip(self):
        fr = FilterResult(keep=True, direction="入境中国", category="政策", reason="r")
        self.assertEqual(FilterResult.from_dict(fr.to_dict()), fr)

    def test_signalgroup_roundtrip(self):
        g = SignalGroup(
            theme="免签政策",
            items=[make_item(1)],
            stats={"n_items": 1},
            score=SignalScore(novelty=8, total=7.0, level="重点关注"),
        )
        g2 = SignalGroup.from_dict(g.to_dict())
        self.assertEqual(g2.theme, "免签政策")
        self.assertEqual(g2.score.level, "重点关注")


class TestConfig(unittest.TestCase):
    def test_load(self):
        cfg = Config()
        self.assertTrue(cfg.weights)
        self.assertTrue(cfg.sources)
        self.assertTrue(str(cfg.db_path).endswith("tourism_signal.db"))


class TestScoring(unittest.TestCase):
    def test_level_boundaries(self):
        self.assertEqual(_level(2.0, {"normal": 3, "focus": 7}), "普通信息")
        self.assertEqual(_level(5.0, {"normal": 3, "focus": 7}), "观察信息")
        self.assertEqual(_level(7.0, {"normal": 3, "focus": 7}), "重点关注")

    def test_num_clamp(self):
        self.assertEqual(_num("12", 0), 10.0)
        self.assertEqual(_num("abc", 4), 4.0)

############ / 工具函数测试 ##############

class TestDiscoveryTime(unittest.TestCase):
    """discovery 时区/时间窗口（东八区：昨天 00:00 至今）。"""
    @staticmethod
    def _it(pub):
        n = NewsItem("t", "", "u", "src")
        n.published_at = pub
        return n

    def test_no_tz_parsed_as_cn(self):
        from tourism_signal.agents.discovery import _parse_time
        dt = _parse_time("2026-08-05 08:00:00")
        self.assertIsNotNone(dt)
        # 无时区标记 → 东八区
        self.assertEqual(dt.utcoffset().total_seconds() // 3600, 8)

    def test_gmt_parsed_as_utc(self):
        from tourism_signal.agents.discovery import _parse_time
        dt = _parse_time("Wed, 05 Aug 2026 08:00:00 GMT")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.utcoffset().total_seconds(), 0)

    def test_window_yesterday_to_now(self):
        from tourism_signal.agents.discovery import _recent
        from tourism_signal.utils import now_cn
        from datetime import timedelta
        now = now_cn()
        # 前天（排除）
        self.assertFalse(_recent(self._it((now - timedelta(days=2)).strftime("%Y-%m-%d 12:00:00"))))
        # 昨天 00:00 边界（保留）
        self.assertTrue(_recent(self._it((now - timedelta(days=1)).strftime("%Y-%m-%d 00:00:00"))))
        # 昨天中午（保留）
        self.assertTrue(_recent(self._it((now - timedelta(days=1)).strftime("%Y-%m-%d 12:00:00"))))
        # 解析失败保留
        self.assertTrue(_recent(self._it("")))

    def test_invalid_time_kept(self):
        from tourism_signal.agents.discovery import _parse_time
        self.assertIsNone(_parse_time(""))
        self.assertIsNone(_parse_time("不是日期"))

    def test_fix_source_placeholder(self):
        """日报里 [来源](url) 占位应被替换为 [真实媒体名](url)。"""
        from tourism_signal.agents.report import _fix_source_placeholders
        md = "- 问题（评分5.0）：要点。[来源](https://example.com/a)\n"
        url_map = {"https://example.com/a": "新浪财经"}
        out = _fix_source_placeholders(md, url_map)
        self.assertIn("[新浪财经](https://example.com/a)", out)
        self.assertNotIn("[来源]", out)

    def test_fix_source_placeholder_unmatched_kept(self):
        """匹配不到媒体名的来源占位保留原样（不误删）。"""
        from tourism_signal.agents.report import _fix_source_placeholders
        md = "- 问题：要点。[来源](https://example.com/b)\n"
        out = _fix_source_placeholders(md, {})
        self.assertIn("[来源](https://example.com/b)", out)

    def test_fix_truncated_url(self):
        """LLM 把来源 URL 截断时，应用数据库完整 URL 修复并闭合链接。"""
        from tourism_signal.agents.report import _fix_source_placeholders
        full = "https://news.google.com/rss/articles/CBMiVEFVX3lxTFBoXzE0bm93ZzA5eHV2%2FFullTail"
        url_map = {full: "YouTube"}
        # 未闭合 + 截断（日报在链接处被切断）
        md = "- 上海景点体验：要点。[YouTube](https://news.google.com/rss/articles/CBMiVEFVX3lxTFBoXzE0bm93"
        out = _fix_source_placeholders(md, url_map)
        self.assertIn(f"[YouTube]({full})", out)
        self.assertNotIn("CBMiVEFVX3lxTFBoXzE0bm93ZzA5eHV\n", out)
        # 闭合 + 截断：不应产生双右括号
        md2 = "- 要点。[YouTube](https://news.google.com/rss/articles/CBMiVEFVX3lxTFBoXzE0bm93)"
        out2 = _fix_source_placeholders(md2, url_map)
        self.assertIn("[YouTube](" + full + ")", out2)  # 恰好一个右括号
        self.assertNotIn("" + full + "))", out2)

    def test_fix_truncated_keeps_complete(self):
        """完整 URL 与匹配不到的不应被改动。"""
        from tourism_signal.agents.report import _fix_source_placeholders
        full = "https://example.com/a/complete/url"
        md = f"- 要点。[来源](https://example.com/a/complete/url)\n"
        out = _fix_source_placeholders(md, {full: "媒体A"})
        self.assertIn("[媒体A](https://example.com/a/complete/url)", out)
        # 无关链接不被破坏
        self.assertIn("https://other.com", _fix_source_placeholders("- [X](https://other.com)", {}))

    def test_group_payload_source_md(self):
        """observe 条目应带 source_md（[媒体名](链接)）供 LLM 直接引用。"""
        from tourism_signal.agents.report import _group_payload
        g = SignalGroup(
            theme="问题主题", items=[make_item(1, title="t", source="google_news:zh-CN")],
            stats={"n_items": 1},
            score=SignalScore(novelty=6, total=5.0, level="观察信息"),
        )
        # 给 media 填充媒体名
        g.items[0].media = "澎湃新闻"
        p = _group_payload(g, full=False)
        self.assertEqual(p["media_name"], "澎湃新闻")
        self.assertIn("澎湃新闻", p["source_md"])

    def test_merge_similar(self):
        from tourism_signal.agents.weak_signal import _merge_similar
        g1 = SignalGroup(theme="免签政策促入境游", items=[make_item(1)],
                         stats={"n_items": 1, "sources": ["google_news"], "languages": ["zh"],
                                "directions": ["入境中国"], "categories": ["政策"]})
        g2 = SignalGroup(theme="免签政策助推入境游", items=[make_item(2)],
                         stats={"n_items": 1, "sources": ["reddit"], "languages": ["en"],
                                "directions": ["入境中国"], "categories": ["政策"]})
        g3 = SignalGroup(theme="批发市场成景点", items=[make_item(3)],
                         stats={"n_items": 1, "sources": ["google_news"], "languages": ["zh"],
                                "directions": ["入境中国"], "categories": ["游客行为"]})
        merged = _merge_similar([g1, g2, g3])
        self.assertEqual(len(merged), 2)
        big = max(merged, key=lambda g: g.stats["n_items"])
        self.assertEqual(big.stats["n_items"], 2)
        self.assertEqual(len(big.stats["sources"]), 2)  # 来源统计合并

    def test_social_group_routing(self):
        """社交媒体个人发布应路由到「二/三（一）境外数据（1）个人发布内容」章节。"""
        from tourism_signal.agents.report import _build_user_prompt
        import json as _json
        it = make_item(5, title="X: 在中国旅游用支付宝的体验", source="google_news_social:en-US")
        social_g = SignalGroup(
            theme="外国游客支付体验", items=[it],
            stats={"n_items": 1, "sources": ["google_news_social"], "languages": ["en"],
                   "directions": ["入境中国"], "categories": ["体验反馈"], "social": True},
            score=SignalScore(novelty=8, repetition=2, diffusion=3, impact=5,
                              sustainability=4, total=5.0, level="观察信息"),
        )
        news_g = SignalGroup(
            theme="入境数据发布", items=[make_item(6)],
            stats={"n_items": 1, "sources": ["google_news"], "languages": ["zh"],
                   "directions": ["入境中国"], "categories": ["政策"], "social": False},
            score=SignalScore(novelty=5, repetition=3, diffusion=2, impact=5,
                              sustainability=4, total=4.5, level="观察信息"),
        )
        payload = _json.loads(_build_user_prompt([social_g, news_g], 10, "2026-07-31"))
        secs = payload["sections"]
        inbound = secs["二、入境游服务质量相关"]
        # 社交组 → （一）境外数据（1）个人发布内容
        personal = inbound["（一）境外数据"]["（1）个人发布内容"]
        self.assertTrue(any(x["theme"] == "外国游客支付体验" for x in personal))
        # 新闻组 → （1）媒体发布-其他境外媒体
        media = inbound["（一）境外数据"]["（2）媒体发布"]
        other = media.get("其他境外媒体", [])
        self.assertTrue(any(x["theme"] == "入境数据发布" for x in other))
        # 社交组不应出现在媒体发布里
        for lst in (other, media.get("官媒海外版（合并提炼）", [])):
            self.assertNotIn("外国游客支付体验", [x["theme"] for x in (lst or [])])

    def test_multi_direction_group_no_duplicate(self):
        """方向同时含入境+出境的组只应归入唯一位置（避免日报重复）。"""
        from tourism_signal.agents.report import _build_user_prompt
        import json as _json
        it = make_item(7, title="推广中国旅游体验")
        g = SignalGroup(
            theme="推广中国旅游体验", items=[it],
            stats={"n_items": 1, "sources": ["google_news"], "languages": ["zh"],
                   "directions": ["入境中国", "出境中国"], "categories": ["游客行为", "目的地"],
                   "social": False},
            score=SignalScore(novelty=7, repetition=6, diffusion=5, impact=8,
                              sustainability=6, total=6.5, level="观察信息"),
        )
        payload = _json.loads(_build_user_prompt([g], 10, "2026-07-31"))
        secs = payload["sections"]

        def _collect(o):
            """递归收集所有 group payload 的 theme。"""
            found = []
            if isinstance(o, dict):
                if "theme" in o:
                    found.append(o["theme"])
                for v in o.values():
                    found.extend(_collect(v))
            elif isinstance(o, list):
                for v in o:
                    found.extend(_collect(v))
            return found

        theme_count = _collect(secs).count("推广中国旅游体验")
        self.assertEqual(theme_count, 1, f"该组应恰好出现 1 次，实际 {theme_count}")

    def test_outbound_only_group_to_section3(self):
        """仅出境中国方向的组应归入「三、出境游服务质量相关」。"""
        from tourism_signal.agents.report import _build_user_prompt
        import json as _json
        it = make_item(8, title="中国游客赴日热")
        g = SignalGroup(
            theme="中国游客赴日热", items=[it],
            stats={"n_items": 1, "sources": ["google_news"], "languages": ["zh"],
                   "directions": ["出境中国"], "types": ["游客行为"], "social": False},
            score=SignalScore(novelty=6, repetition=5, diffusion=4, impact=6,
                              sustainability=5, total=5.2, level="观察信息"),
        )
        payload = _json.loads(_build_user_prompt([g], 10, "2026-07-31"))
        secs = payload["sections"]
        outbound = secs["三、出境游服务质量相关"]
        media = outbound["（一）境外数据"]["（2）媒体发布"]
        self.assertTrue(any(x["theme"] == "中国游客赴日热" for x in media.get("其他境外媒体", [])))
        # 不应出现在入境章
        inbound = secs["二、入境游服务质量相关"]
        self.assertNotIn("中国游客赴日热", str(json.dumps(inbound, ensure_ascii=False)))

    def test_safe_url_encodes_utf8_and_space(self):
        """热搜含中文+空格的 URL 应百分号编码，避免 markdown 链接断链。"""
        from tourism_signal.sources.hotsearch import _safe_url
        out = _safe_url("https://s.weibo.com/weibo?q=泰国旅游 失联")
        self.assertEqual(out, "https://s.weibo.com/weibo?q=%E6%B3%B0%E5%9B%BD%E6%97%85%E6%B8%B8%20%E5%A4%B1%E8%81%94")

    def test_safe_url_keeps_encoded_and_plain(self):
        """已编码 URL 与普通 URL 不应被双重编码或破坏。"""
        from tourism_signal.sources.hotsearch import _safe_url
        self.assertEqual(_safe_url("https://m.baidu.com/s?word=%E9%A6%96%E9%A1%B5"), "https://m.baidu.com/s?word=%E9%A6%96%E9%A1%B5")
        self.assertEqual(_safe_url("https://example.com/a/b"), "https://example.com/a/b")
        self.assertEqual(_safe_url(""), "")

    def test_classify_source(self):
        """来源分类：境外/境内 × 个人/官媒/其他境外媒体。"""
        from tourism_signal.agents.report import _classify_source
        self.assertEqual(_classify_source("google_news_social:en-US", "x.com"), ("境外", "个人"))
        self.assertEqual(_classify_source("reddit:travel", ""), ("境外", "个人"))
        self.assertEqual(_classify_source("google_news:en-US", "China Daily"), ("境外", "官媒海外版"))
        self.assertEqual(_classify_source("google_news:en-US", "South China Morning Post"), ("境外", "其他境外媒体"))
        self.assertEqual(_classify_source("hotsearch:微博社会", "微博社会"), ("境内", "境内平台/媒体"))
        # 官媒大小写/带标点也能匹配
        self.assertEqual(_classify_source("google_news:zh-CN", "Xinhua"), ("境外", "官媒海外版"))
        self.assertEqual(_classify_source("google_news:zh-CN", "中国日报"), ("境外", "官媒海外版"))
        # People's Daily 撇号归一化后应命中
        self.assertEqual(_classify_source("google_news:en-US", "People's Daily"), ("境外", "官媒海外版"))
        self.assertEqual(_classify_source("google_news:en-US", "South China Morning Post"), ("境外", "其他境外媒体"))


class TestGoogleNews(unittest.TestCase):
    """Google News 时效性工具（after: 窗口 + 时间解析，纯函数不联网）。"""
    def test_build_query_appends_after(self):
        from datetime import datetime, timezone
        from tourism_signal.sources.google_news import _build_query
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        q = _build_query("China visa-free tourism", 3, now=now)
        self.assertEqual(q, "China visa-free tourism after:2026-07-31")

    def test_build_query_lookback_zero(self):
        from datetime import datetime, timezone
        from tourism_signal.sources.google_news import _build_query
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        q = _build_query("site:weibo.com 免签 入境", 0, now=now)
        self.assertEqual(q, "site:weibo.com 免签 入境 after:2026-08-03")

    def test_parse_published_gmt(self):
        from datetime import datetime, timezone
        from tourism_signal.sources.google_news import _parse_published
        dt = _parse_published("Wed, 29 Jul 2026 21:05:53 GMT")
        self.assertEqual(dt, datetime(2026, 7, 29, 21, 5, 53, tzinfo=timezone.utc))

    def test_parse_published_invalid(self):
        from tourism_signal.sources.google_news import _parse_published
        self.assertIsNone(_parse_published(""))
        self.assertIsNone(_parse_published("不是时间"))


# ==================== 流水线集成测试（LLM 打桩） ====================

class _FakeLLM:
    """按 system prompt 分发：过滤/主题/评分/日报 的桩实现。"""
    def __init__(self, cfg=None):
        pass

    def chat_json(self, system, user):
        payload = json.loads(user)
        if "themes" in system and "groups" not in system:  # 主题抽取
            return {
                "themes": [
                    {"id": p["id"], "theme": "免签政策观察", "summary": p["title"]}
                    for p in payload
                ]
            }
        if "groups" in system:  # 五维评分
            return {
                "groups": [
                    {"theme": p["theme"], "novelty": 9, "repetition": 8,
                     "diffusion": 7, "impact": 9, "sustainability": 8,
                     "reason": "多来源出现，可能影响入境游"}
                    for p in payload
                ]
            }
        # 过滤（keep/results）
        return {
            "results": [
                {"id": p["id"], "keep": True,
                 "direction": "入境中国", "category": "体验反馈", "reason": "测试"}
                for p in payload
            ]
        }

    def chat_text(self, system, user, max_tokens=None):
        return (
            "# 旅游服务质量苗头问题日报\n\n**日期**：2025-08-01\n\n"
            "## 一、本期重点关注\n\n### 1. 免签政策观察\n\n- **情况**：...\n\n"
            "## 二、入境游客服务与行为问题\n\n..."
        )


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = Config()

    def test_full_pipeline_with_stub_llm(self):
        candidates = [make_item(1), make_item(2, source="reddit:travel")]
        state = {"config": self.cfg, "date": "2025-08-01", "candidates": candidates,
                 "filtered": [], "signals": [], "stats": {}}

        with patch("tourism_signal.agents.filter.LLMClient", _FakeLLM):
            out = filter_node(state)
        self.assertEqual(len(out["filtered"]), 2)
        self.assertTrue(all(f.result.keep for f in out["filtered"]))

        st2 = {**state, "filtered": out["filtered"]}
        with patch("tourism_signal.agents.weak_signal.LLMClient", _FakeLLM):
            st3 = weak_signal_node(st2)
        # 两条同主题应聚为一组
        self.assertEqual(len(st3["signals"]), 1)
        g = st3["signals"][0]
        self.assertEqual(g.stats["n_items"], 2)
        self.assertGreaterEqual(g.score.total, 7.0)
        self.assertEqual(g.score.level, "重点关注")

        st4 = {**state, **st3, "stats": {**st3["stats"], "filtered_total": 2}}
        with patch("tourism_signal.agents.report.LLMClient", _FakeLLM):
            with patch("tourism_signal.storage.db.Database.save_report", lambda *a, **k: None):
                with patch("tourism_signal.agents.report.Path.write_text", lambda *a, **k: None):
                    out_r = report_node(st4)
        self.assertIn("本期重点关注", out_r["report"])

    def test_filter_keyword_fallback(self):
        from tourism_signal.agents.filter import _fallback
        it = NewsItem(
            title="某地发生交通事故", content="现场交通管制，请绕行",
            url="https://example.com/x", source="test",
        )
        it.item_id = "x"
        fr = _fallback(it)
        self.assertFalse(fr.keep)

        it2 = make_item(9, title="外国游客抱怨支付不便")
        fr2 = _fallback(it2)
        self.assertTrue(fr2.keep)


class TestVisaScope(unittest.TestCase):
    """非旅游类签证(工作/居留/移民/劳务/侨民/商务)必须被排除；旅游类签证保留。"""
    from tourism_signal.agents.filter import _is_non_tourist_visa

    def _has(self, title, content):
        it = NewsItem(title=title, content=content, url="u", source="google_news:en-US")
        it.item_id = "v"
        return TestVisaScope._is_non_tourist_visa(it)

    def test_exclude_work_visa(self):
        self.assertTrue(self._has("外国人来华工作手续繁琐", "foreign workers face work visa paperwork and permits"))

    def test_exclude_diaspora_visa(self):
        self.assertTrue(self._has("印度侨民签证困扰", "Indian diaspora in China raises concerns over visa issues"))

    def test_exclude_community_resident_visa(self):
        self.assertTrue(self._has("印度社区签证问题", "Indian Community Flags Visa Issues In China"))

    def test_exclude_business_trade_visa(self):
        self.assertTrue(self._has("坦桑尼亚签证办理困难", "Visas to China reveal dependency on Chinese imports"))

    def test_keep_tourist_visa(self):
        self.assertFalse(self._has("外国游客旅游签证简化", "tourist visa simplified for inbound foreign tourists"))

    def test_keep_visa_free(self):
        self.assertFalse(self._has("多国免签入境", "China visa-free entry for travelers"))

    def test_keep_transit_visa_free(self):
        self.assertFalse(self._has("过境免签新政", "China transit visa-free policy for inbound visitors"))

    def test_no_visa_word_not_flagged(self):
        self.assertFalse(self._has("外宾支付不便", "foreign tourists Alipay payment difficulty in hotel"))

    def test_non_tourist_visa_kept_only_if_tourism_keyword(self):
        # 出现明确旅游签证字眼时，即使含 work 也应放行（交给 LLM 细判，硬规则从宽）
        self.assertFalse(self._has("旅游签证与工作签证对比", "tourist visa vs work visa for China"))


class TestOriginalContent(unittest.TestCase):
    """X/Instagram 等社交原文应在日报 payload 中保留，供 LLM 引录。"""

    def test_social_group_payload_has_original(self):
        from tourism_signal.agents.report import _group_payload
        it = make_item(10, title="X: visiting China", source="google_news_social:en-US")
        it.content = "<p>So you want to visit China, traveler... it's a continent, like Europe.</p>"
        it.url = "https://x.com/user/status/123"
        g = SignalGroup(
            theme="中国旅游劝告", items=[it],
            stats={"n_items": 1, "sources": ["google_news_social"], "languages": ["en"],
                   "directions": ["入境中国"], "categories": ["体验反馈"], "social": True},
            score=SignalScore(novelty=8, total=5.0, level="观察信息"),
        )
        p = _group_payload(g, full=False)
        self.assertEqual(p["social"], True)
        oc = p["original_content"]
        self.assertTrue(oc, "社交组应包含 original_content")
        self.assertIn("continent, like Europe", oc[0]["content"])
        # HTML 标签应被剥掉
        self.assertNotIn("<p>", oc[0]["content"])

    def test_social_missing_content_no_crash(self):
        from tourism_signal.agents.report import _group_payload
        it = make_item(11, title="X: theme", source="google_news_social:en-US")
        it.content = ""
        g = SignalGroup(
            theme="主题", items=[it],
            stats={"n_items": 1, "sources": ["google_news_social"], "languages": ["en"],
                   "directions": ["入境中国"], "categories": ["体验反馈"], "social": True},
            score=SignalScore(novelty=8, total=5.0, level="观察信息"),
        )
        p = _group_payload(g, full=False)
        self.assertEqual(p["social"], True)
        self.assertIsInstance(p["original_content"], list)


if __name__ == "__main__":
    unittest.main()
