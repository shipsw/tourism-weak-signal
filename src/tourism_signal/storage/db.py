"""SQLite 存储：候选信息 / 过滤评分结果 / 日报索引。

遵循设计原则：只保存"当日报告 + 候选事件 + 少量历史索引"，
原始数据按 keep_days 定期清理，不建大数据平台。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..models import FilteredCandidate, NewsItem, SignalGroup
from ..utils import now_cn, today_str

logger = logging.getLogger("tourism_signal.storage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  title TEXT,
  content TEXT,
  url TEXT,
  source TEXT,
  media TEXT,
  published_at TEXT,
  language TEXT,
  keywords TEXT,
  raw TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS assessments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id TEXT NOT NULL,
  date TEXT NOT NULL,
  theme TEXT,
  tourism_related INTEGER,
  direction TEXT,
  type TEXT,
  reason TEXT,
  novelty REAL, repetition REAL, diffusion REAL,
  impact REAL, sustainability REAL,
  total REAL, level TEXT, signal_summary TEXT
);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT UNIQUE,
  path TEXT,
  summary TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_date ON candidates(date);
CREATE INDEX IF NOT EXISTS idx_assessments_date ON assessments(date);
"""


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """存量库增量迁移（新增列）。"""
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(candidates)")]
        if "media" not in cols:
            self.conn.execute("ALTER TABLE candidates ADD COLUMN media TEXT")
            self.conn.commit()

    def close(self):
        self.conn.close()

    def save_candidates(self, date: str, items: list[NewsItem]):
        now = now_cn().isoformat()
        rows = [
            (
                it.item_id, date, it.title, it.content, it.url, it.source,
                it.media, it.published_at, it.language,
                json.dumps(it.keywords, ensure_ascii=False),
                json.dumps(it.raw, ensure_ascii=False), now,
            )
            for it in items
        ]
        self.conn.executemany(
            "INSERT OR IGNORE INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        self.conn.commit()

    def save_assessments(self, date: str, filtered: list[FilteredCandidate], groups: list[SignalGroup]):
        rows = []
        for g in groups:
            score = g.score
            for it in g.items:
                f = next((f for f in filtered if f.item.item_id == it.item_id), None)
                rows.append(
                    (
                        it.item_id, date, g.theme,
                        int(f.result.tourism_related) if f else 1,
                        f.result.direction if f else "",
                        f.result.type if f else "",
                        (f.result.reason if f else "") or (score.reason if score else ""),
                        score.novelty if score else 0,
                        score.repetition if score else 0,
                        score.diffusion if score else 0,
                        score.impact if score else 0,
                        score.sustainability if score else 0,
                        score.total if score else 0,
                        score.level if score else "",
                        g.summary,
                    )
                )
        self.conn.executemany(
            "INSERT OR REPLACE INTO assessments "
            "(candidate_id, date, theme, tourism_related, direction, type, reason, "
            "novelty, repetition, diffusion, impact, sustainability, total, level, signal_summary) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()

    def save_report(self, date: str, path: str, summary: str = ""):
        self.conn.execute(
            "INSERT OR REPLACE INTO reports (date, path, summary, created_at) VALUES (?,?,?,?)",
            (date, path, summary, now_cn().isoformat()),
        )
        self.conn.commit()

    def prune(self, keep_days: int):
        cutoff = (now_cn() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        for table in ("candidates", "assessments"):
            self.conn.execute(f"DELETE FROM {table} WHERE date < ?", (cutoff,))
        self.conn.commit()
        logger.info("已清理 %s 之前的原始数据", cutoff)
