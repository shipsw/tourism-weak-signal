#!/usr/bin/env bash
# 出入境旅游弱信号每日任务（cron）
cd "$(dirname "$0")/.."
export PYTHONPATH=src
python -m tourism_signal.main
