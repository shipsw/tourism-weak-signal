"""数据源注册表：根据 config.yaml 构建启用的数据源。"""
from __future__ import annotations

import logging

from ..config import Config
from .base import BaseSource
from .domestic import RSSSource
from .google_news import GoogleNewsSource
from .reddit import RedditSource

SOURCE_REGISTRY: dict[str, type[BaseSource]] = {
    "google_news": GoogleNewsSource,
    "reddit": RedditSource,
    "domestic": RSSSource,
}


def build_sources(cfg: Config) -> list[BaseSource]:
    logger = logging.getLogger("tourism_signal.sources")
    out: list[BaseSource] = []
    for name, s in cfg.sources.items():
        if not s.get("enabled", True):
            continue
        cls = SOURCE_REGISTRY.get(name)
        if cls is None:
            logger.warning("未知数据源: %s（忽略）", name)
            continue
        try:
            out.append(cls(s))
        except Exception as e:
            logger.warning("初始化数据源 %s 失败: %s", name, e)
    return out
