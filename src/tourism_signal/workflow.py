"""LangGraph 工作流：每日弱信号研判流水线。"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .agents import discovery_node, filter_node, report_node, weak_signal_node
from .config import Config
from .utils import today_str


class AgentState(TypedDict, total=False):
    config: Config
    date: str
    candidates: list          # list[NewsItem]
    filtered: list            # list[FilteredCandidate]
    signals: list             # list[SignalGroup]
    report: str
    report_path: str
    stats: dict[str, Any]


def build_graph():
    """构建状态图：信息发现 → 旅游过滤 → 弱信号判断 → 日报生成。"""
    g = StateGraph(AgentState)
    g.add_node("discover", discovery_node)
    g.add_node("filter", filter_node)
    g.add_node("weak_signal", weak_signal_node)
    g.add_node("report", report_node)
    g.add_edge(START, "discover")
    g.add_edge("discover", "filter")
    g.add_edge("filter", "weak_signal")
    g.add_edge("weak_signal", "report")
    g.add_edge("report", END)
    return g.compile()


def run_pipeline(cfg: Config, date: str | None = None) -> dict:
    initial: AgentState = {
        "config": cfg,
        "date": date or today_str(),
        "candidates": [],
        "filtered": [],
        "signals": [],
        "report": "",
        "report_path": "",
        "stats": {},
    }
    app = build_graph()
    return app.invoke(initial)
