"""数据模型定义。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class NewsItem:
    """候选信息（Agent1 信息发现的输出）。"""

    title: str
    content: str
    url: str
    source: str
    published_at: str = ""
    language: str = ""
    keywords: list[str] = field(default_factory=list)
    media: str = ""           # 真实媒体名（如 澎湃新闻 / Xinhua / SOHU）
    raw: dict[str, Any] = field(default_factory=dict)
    item_id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "NewsItem":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


@dataclass
class FilterResult:
    """服务质量过滤结果（Agent2 的输出）。"""

    keep: bool = False
    direction: str = ""      # 入境中国 | 出境中国 | 双向 | 其他 | 不确定
    category: str = ""       # 入境不文明行为 | 出境不文明行为 | 服务环节问题 | 消费诚信 | 卫生安全 | 文化冲突/误解 | 体验反馈 | 政策 | 其他
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FilterResult":
        return cls(
            keep=bool(d.get("keep", False)),
            direction=str(d.get("direction", "")),
            category=str(d.get("category", "")),
            reason=str(d.get("reason", "")),
        )


@dataclass
class FilteredCandidate:
    """通过过滤的候选 + 过滤结果。"""

    item: NewsItem
    result: FilterResult

    def to_dict(self) -> dict:
        return {"item": self.item.to_dict(), "result": self.result.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "FilteredCandidate":
        return cls(
            item=NewsItem.from_dict(d["item"]),
            result=FilterResult.from_dict(d.get("result", {})),
        )


@dataclass
class SignalScore:
    """弱信号五维评分（Agent3 的输出）。"""

    novelty: float = 0.0        # 新颖性
    repetition: float = 0.0     # 重复性
    diffusion: float = 0.0      # 扩散性
    impact: float = 0.0         # 影响性
    sustainability: float = 0.0 # 持续性
    total: float = 0.0          # 加权总分 0-10
    level: str = "普通信息"      # 普通信息 | 观察信息 | 重点关注
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SignalScore":
        return cls(**{k: d.get(k, 0.0 if k not in ("level", "reason") else "") for k in cls.__dataclass_fields__})


@dataclass
class SignalGroup:
    """弱信号分组：相同/相似主题的信息聚为一组后统一评分。"""

    theme: str                       # 主题标签（中文）
    items: list[NewsItem] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)   # n_items / n_sources / n_languages / n_directions
    score: Optional[SignalScore] = None
    summary: str = ""                # LLM 综合描述

    def to_dict(self) -> dict:
        return {
            "theme": self.theme,
            "items": [i.to_dict() for i in self.items],
            "stats": self.stats,
            "score": self.score.to_dict() if self.score else None,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SignalGroup":
        return cls(
            theme=d.get("theme", ""),
            items=[NewsItem.from_dict(i) for i in d.get("items", [])],
            stats=d.get("stats", {}),
            score=SignalScore.from_dict(d["score"]) if d.get("score") else None,
            summary=d.get("summary", ""),
        )
