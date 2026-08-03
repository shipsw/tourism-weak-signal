"""数据源抽象基类。新增数据源只需继承 BaseSource 并实现 fetch()。"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from ..models import NewsItem


class BaseSource(ABC):
    name: str = "base"

    def __init__(self, settings: dict[str, Any] | None = None):
        self.settings = settings or {}
        self.log = logging.getLogger(f"tourism_signal.source.{self.name}")

    @abstractmethod
    def fetch(self) -> list[NewsItem]:
        """抓取候选信息。单条失败应跳过而非抛出。"""
        raise NotImplementedError
