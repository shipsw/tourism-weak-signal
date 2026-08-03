"""配置加载：YAML + .env 合并。"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"


class Config:
    def __init__(self, path: str | Path | None = None):
        load_dotenv(PROJECT_ROOT / ".env")
        self.path = Path(path) if path else DEFAULT_CONFIG
        with open(self.path, encoding="utf-8") as f:
            self.data = yaml.safe_load(f) or {}

    # ---- LLM ----
    @property
    def llm(self) -> dict:
        llm = dict(self.data.get("llm", {}))
        # .env 优先级高于 yaml
        if os.getenv("LLM_BASE_URL"):
            llm["base_url"] = os.getenv("LLM_BASE_URL")
        if os.getenv("LLM_API_KEY"):
            llm["api_key"] = os.getenv("LLM_API_KEY")
        if os.getenv("LLM_MODEL"):
            llm["model"] = os.getenv("LLM_MODEL")
        return llm

    # ---- 数据源 ----
    @property
    def sources(self) -> dict:
        return self.data.get("sources", {})

    # ---- 评分 ----
    @property
    def scoring(self) -> dict:
        return self.data.get("scoring", {})

    @property
    def weights(self) -> dict:
        return self.scoring.get("weights", {})

    @property
    def thresholds(self) -> dict:
        return self.scoring.get("thresholds", {})

    @property
    def max_groups(self) -> int:
        return int(self.scoring.get("max_groups", 20))

    # ---- 输出 ----
    @property
    def output(self) -> dict:
        out = dict(self.data.get("output", {}))
        # 相对路径基于项目根目录
        for k in ("data_dir", "report_dir", "log_dir"):
            v = out.get(k)
            if v and not Path(v).is_absolute():
                out[k] = PROJECT_ROOT / v
        return out

    @property
    def db_path(self) -> Path:
        return Path(self.output["data_dir"]) / "tourism_signal.db"
