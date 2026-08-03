@echo off
rem 出入境旅游弱信号每日任务（Windows 任务计划程序）
cd /d "%~dp0.."
set PYTHONPATH=src
python -m tourism_signal.main
