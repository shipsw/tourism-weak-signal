"""数据源层：信息获取。"""
from .base import BaseSource
from .google_news import GoogleNewsSource
from .reddit import RedditSource
from .domestic import RSSSource
from .registry import build_sources

__all__ = [
    "BaseSource",
    "GoogleNewsSource",
    "RedditSource",
    "RSSSource",
    "build_sources",
]
