"""数据源抽象基类。新增数据源只需继承 BaseSource 并实现 fetch()。"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import feedparser
import requests

from ..models import NewsItem

DEFAULT_UA = "Mozilla/5.0 (compatible; tourism-weak-signal-agent/0.1)"


def fetch_feed(url: str, timeout: int = 20, agent: str = DEFAULT_UA):
    """带超时地抓取并解析 RSS/Atom。

    feedparser.parse(url) 底层用 urllib，默认没有 socket 超时，服务器不响应时
    会无限挂起，进而拖垮整条流水线（曾导致 CI 跑满 20 分钟被 timeout 取消）。
    这里改用 requests 显式超时取回内容，再交给 feedparser 解析。
    """
    resp = requests.get(url, headers={"User-Agent": agent}, timeout=timeout)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


class BaseSource(ABC):
    name: str = "base"

    def __init__(self, settings: dict[str, Any] | None = None):
        self.settings = settings or {}
        self.log = logging.getLogger(f"tourism_signal.source.{self.name}")

    @abstractmethod
    def fetch(self) -> list[NewsItem]:
        """抓取候选信息。单条失败应跳过而非抛出。"""
        raise NotImplementedError
