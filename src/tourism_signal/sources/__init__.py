"""数据源层：信息获取。"""
from .base import BaseSource
from .google_news import GoogleNewsSource
from .reddit import RedditSource
from .domestic import RSSSource
from .hotsearch import HotSearchSource
from .youtube import YouTubeSource
from .serpapi_source import SerpApiSource
from .registry import build_sources

__all__ = [
    "BaseSource",
    "GoogleNewsSource",
    "RedditSource",
    "RSSSource",
    "HotSearchSource",
    "YouTubeSource",
    "SerpApiSource",
    "build_sources",
]
