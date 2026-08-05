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
        """社交媒体信号应路由到「五、境外平台游客体验反馈」章节。"""
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
        self.assertIn("五、境外平台游客体验反馈", secs)
        self.assertEqual(secs["五、境外平台游客体验反馈"][0]["theme"], "外国游客支付体验")
        # 社交组不应出现在二/四
        for name in ("二、入境游客服务与行为问题", "四、其他苗头与政策动态"):
            if name in secs:
                themes = [g["theme"] for g in secs[name]] if isinstance(secs[name], list) else []
                self.assertNotIn("外国游客支付体验", themes)

    def test_multi_direction_group_no_duplicate(self):
        """方向同时含入境+出境的组只应归入唯一章节（避免日报重复）。"""
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
        found = [name for name in ("二、入境游客服务与行为问题", "三、出境游客行为与服务问题", "四、其他苗头与政策动态")
                 if name in secs and isinstance(secs[name], list)
                 and any(x["theme"] == "推广中国旅游体验" for x in secs[name])]
        self.assertEqual(len(found), 1, f"该组应在且仅在一个章节，实际出现在 {found}")

    def test_outbound_only_group_to_section3(self):
        """仅出境中国方向的组应归入「三、出境游客行为与服务问题」。"""
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
        self.assertIn("三、出境游客行为与服务问题", secs)
        self.assertEqual(secs["三、出境游客行为与服务问题"][0]["theme"], "中国游客赴日热")


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


if __name__ == "__main__":
    unittest.main()
