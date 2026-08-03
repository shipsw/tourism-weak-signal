# 旅游服务质量苗头问题研判 Agent

> Tourism Service Quality Weak Signal Intelligence Agent

从全球网络信息中，自动发现**尚未形成热点、但具有趋势性与苗头性**的出入境旅游相关信息，为文旅研究人员提供每日观察素材。核心是 **AI 研究助理**，而非舆情监测系统。

## 快速开始

```bash
# 1. 安装依赖
python -m pip install -r requirements.txt

# 2. 配置 LLM（任选：OpenAI / DeepSeek / Moonshot / Qwen / Ollama 本地）
cp .env.example .env
#    编辑 .env，填入 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL

# 3. 运行每日任务
cd src
python -m tourism_signal.main

# 4. 查看日报
#    outputs/reports/YYYY-MM-DD.md
```

Windows 定时任务：使用 `scripts/run_daily.bat` 注册到任务计划程序；Linux/Mac 用 `scripts/run_daily.sh` + cron。

**GitHub Actions 部署**（推荐，零服务器成本）：仓库根目录已含 `.github/workflows/daily_report.yml`，
推送到 GitHub 后按以下步骤启用：

1. 仓库 **Settings → Secrets and variables → Actions** 添加 Secrets：
   - `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`（必填，与 `.env` 相同值）
   - `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`（可选）
2. 默认每天 **北京时间 07:00** 自动运行（`cron: 0 23 * * *` UTC，可改 `schedule`）；
   也可在 **Actions 页手动触发**（workflow_dispatch）补跑。
3. 每次运行后，SQLite 数据库与日报 Markdown 会自动 commit 回仓库
   （`outputs/data`、`outputs/reports`），即轻量数据仓库——历史日报可在仓库直接浏览，
   下一次运行自动读取历史数据做趋势比较。
4. 查看最新日报：仓库 `outputs/reports/YYYY-MM-DD.md`，或 Actions 运行日志。

## 流水线

```
每日任务 → [信息发现] → [旅游过滤] → [弱信号判断] → [日报生成]
           GoogleNews/Reddit   LLM 领域判断   五维评分+分组    Markdown 日报
           /RSS(可扩展)                        SQLite 存储
```

四个 Agent 由 LangGraph 状态图编排（`src/tourism_signal/workflow.py`），线性流程、节点可插拔。

## 弱信号五维评分

| 维度 | 权重 | 含义 |
|------|------|------|
| 新颖性 novelty | 0.25 | 新趋势/新变化 |
| 重复性 repetition | 0.20 | 多来源出现类似信息 |
| 扩散性 diffusion | 0.15 | 跨平台/跨地区传播 |
| 影响性 impact | 0.25 | 对出入境旅游发展的影响 |
| 持续性 sustainability | 0.15 | 是否可能持续演变 |

总分分级：≤3 普通（不入日报）｜ 4-6 观察（入「其他值得关注」）｜ ≥7 重点关注（入「重点关注趋势」）。

核心思想：**单条投诉价值低；多国游客、多平台出现类似体验问题，价值提高。**

## 目录结构

```
├── config/config.yaml      # 数据源 / 关键词 / 评分权重 / 输出路径
├── docs/技术设计方案.md      # 详细技术方案
├── src/tourism_signal/
│   ├── agents/             # 四个 Agent（发现/过滤/弱信号/日报）
│   ├── sources/            # 数据源（GoogleNews / Reddit / RSS，可扩展）
│   ├── storage/            # SQLite（候选/评分/报告索引）
│   ├── llm.py              # OpenAI 兼容 LLM 封装（JSON 容错）
│   ├── workflow.py         # LangGraph 状态图
│   └── main.py             # CLI 入口
├── outputs/                # data(SQLite) / reports(日报) / logs
├── scripts/                # 每日任务脚本
└── tests/                  # 单元测试（LLM 打桩，无需密钥）
```

## 测试

```bash
cd tests
python -m unittest test_pipeline -v
```

## 设计原则

- **轻量**：不做全网采集、不建大数据仓库、不做热度排行，只存"当日报告 + 候选事件 + 少量历史索引"。
- **可解释**：每条弱信号附五维评分与理由，日报可溯源到来源链接。
- **低成本**：免费 RSS / 公开 API + LLM 批量调用。

## 路线图

- [x] MVP 骨架：流水线 / 数据源 / LLM 封装 / SQLite / 日报
- [ ] 阶段一：稳定日报生成（数据源扩充、评分校准）
- [ ] 阶段二：历史趋势比较、国家画像、事件追踪
- [ ] 阶段三：出入境旅游趋势知识库

详细设计见 [`docs/技术设计方案.md`](docs/技术设计方案.md)。
