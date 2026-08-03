"""通用工具：JSON 容错解析、日志、日期、去重。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def now_cn() -> datetime:
    """当前时间（Asia/Shanghai 时区）。"""
    return datetime.now(CN_TZ)


def today_str() -> str:
    return now_cn().strftime("%Y-%m-%d")


def parse_json(text: str):
    """容错解析 LLM 返回的 JSON。

    支持：代码围栏包裹、前后杂文本、任意位置的第一个 JSON 值。
    """
    if text is None:
        raise ValueError("文本为空，无法解析 JSON")
    text = str(text).strip()
    # 去掉 markdown 代码围栏
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    decoder = json.JSONDecoder()
    for start in range(len(text)):
        if text[start] in "{[":
            try:
                obj, _ = decoder.raw_decode(text[start:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError(f"无法从文本中解析 JSON: {text[:200]!r}")


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def truncate(text: str, n: int = 500) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


def norm_text(s: str) -> str:
    """归一化文本用于去重/聚类：小写、去空白与标点。"""
    s = (s or "").lower().strip()
    s = re.sub(r"[\s\W_]+", "", s)
    return s


def dedupe(items: list, key_fn) -> list:
    seen, out = set(), []
    for it in items:
        k = key_fn(it)
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return out


def setup_logging(log_dir: str | Path, name: str = "tourism_signal", level: int = logging.INFO) -> logging.Logger:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:  # 避免重复注册
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    fh = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger
