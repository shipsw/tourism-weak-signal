"""CLI 入口：python -m tourism_signal.main [--date ...] [--stage ...]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 支持未安装包时直接从源码运行
SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from tourism_signal.agents import (  # noqa: E402
    discovery_node,
    filter_node,
    report_node,
    weak_signal_node,
)
from tourism_signal.config import Config  # noqa: E402
from tourism_signal.storage import Database  # noqa: E402
from tourism_signal.utils import setup_logging, today_str  # noqa: E402
from tourism_signal.workflow import run_pipeline  # noqa: E402

STAGE_NODES = {
    "discover": discovery_node,
    "filter": filter_node,
    "weak_signal": weak_signal_node,
    "report": report_node,
}


def _empty_state(cfg: Config, date: str) -> dict:
    return {
        "config": cfg,
        "date": date,
        "candidates": [],
        "filtered": [],
        "signals": [],
        "report": "",
        "report_path": "",
        "stats": {},
    }


def main():
    parser = argparse.ArgumentParser(description="出入境旅游弱信号发现与研判 Agent")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 config/config.yaml）")
    parser.add_argument("--date", default=None, help="运行日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--stage", choices=list(STAGE_NODES), default=None, help="仅运行单个节点（调试）")
    parser.add_argument("--no-store", action="store_true", help="不写入 SQLite")
    args = parser.parse_args()

    cfg = Config(args.config)
    logger = setup_logging(cfg.output["log_dir"])
    date = args.date or today_str()

    if args.stage:
        fn = STAGE_NODES[args.stage]
        logger.info("== 仅运行节点: %s (date=%s) ==", args.stage, date)
        out = fn(_empty_state(cfg, date))
        logger.info("节点输出键: %s", {k: (len(v) if isinstance(v, list) else v) for k, v in out.items()})
        return

    logger.info("== 每日任务开始 date=%s ==", date)
    result = run_pipeline(cfg, date)

    if not args.no_store:
        db = Database(cfg.db_path)
        try:
            db.save_candidates(date, result["candidates"])
            db.save_assessments(date, result["filtered"], result["signals"])
            db.save_report(date, result["report_path"], summary=(result.get("report") or "")[:500])
            db.prune(int(cfg.output.get("keep_days", 30)))
        finally:
            db.close()

    logger.info("== 完成。统计: %s ==", result.get("stats", {}))
    print(f"日报: {result.get('report_path', 'N/A')}")


if __name__ == "__main__":
    main()
