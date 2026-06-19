# Vibe-Trading 深度融合实现计划 — 总览

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 Vibe-Trading 核心能力融入现有 ashare_review A股系统

**Architecture:** 渐进式模块叠加 — 新增 agents/alpha/nl_strategy 三个独立包，现有代码零破坏

**Tech Stack:** Python 3.11+, Flask SSE, httpx, asyncio, ThreadPoolExecutor, pandas

---

## 文件地图

| 任务 | 操作 | 文件路径 | 职责 |
|------|------|---------|------|
| **Phase 1: 基础设施** | → `docs/superpowers/plans/2026-06-19-vibe-trading-plan-p1.md` |
| T1 | Create | `ashare_review/config/__init__.py` | 配置包入口 |
| T1 | Create | `ashare_review/config/loader.py` | YAML + 环境变量加载 |
| T1 | Create | `ashare_review/config/defaults.py` | 默认配置常量 |
| T2 | Create | `ashare_review/agents/__init__.py` | Agent包入口 |
| T2 | Create | `ashare_review/agents/base.py` | AgentOpinion/TradingPlan/BaseAgent |
| T2 | Create | `ashare_review/agents/providers.py` | LLMProvider + OpenAICompat + create_provider |
| T3 | Create | `ashare_review/agents/tools.py` | 9个function-calling工具定义+执行 |
| T4 | Create | `ashare_review/alpha/__init__.py` | Alpha包入口 |
| T4 | Create | `ashare_review/alpha/base.py` | AlphaFactor基类 + FactorReport |
| T4 | Create | `ashare_review/alpha/registry.py` | FactorRegistry注册中心 |
| T5 | Create | `ashare_review/nl_strategy/__init__.py` | NL策略包入口 |
| T5 | Create | `ashare_review/nl_strategy/spec.py` | StrategyCondition/StrategySpec |
| T5 | Create | `ashare_review/nl_strategy/templates.py` | 5个内置策略模板 |
| T6 | Modify | `ashare_review/web/app.py` | SSE基础设施(task队列/事件/stream) |
| **Phase 2: 核心能力** | → `docs/superpowers/plans/2026-06-19-vibe-trading-plan-p2.md` |
| T7 | Create | `ashare_review/agents/analysts.py` | 7个Agent system_prompt + 类定义 |
| T8 | Create | `ashare_review/agents/orchestrator.py` | SwarmOrchestrator并行→辩论→综合 |
| T9 | Create | `ashare_review/alpha/factors/__init__.py` | 因子收集+register_all |
| T9 | Create | `ashare_review/alpha/factors/gtja191/__init__.py` | GTJA因子包 |
| T9 | Create | `ashare_review/alpha/factors/gtja191/momentum.py` | 6个动量类因子 |
| T9 | Create | `ashare_review/alpha/factors/custom/__init__.py` | 龙哥特色因子包 |
| T9 | Create | `ashare_review/alpha/factors/custom/limit_up.py` | 涨停基因+换手率强度 |
| T9 | Create | `ashare_review/alpha/factors/custom/chip_concentration.py` | 筹码集中度因子 |
| T9 | Create | `ashare_review/alpha/factors/custom/ma_system.py` | 均线多头排列度 |
| T10 | Create | `ashare_review/alpha/evaluator.py` | evaluate_factor (IC/IR/分层) |
| T10 | Create | `ashare_review/alpha/compare.py` | rank_factors + compare_factors |
| T11 | Create | `ashare_review/nl_strategy/validator.py` | validate_spec参数校验 |
| T11 | Create | `ashare_review/nl_strategy/parser.py` | LLM驱动的NL→StrategySpec |
| T11 | Create | `ashare_review/nl_strategy/executor.py` | execute_strategy条件过滤+评分 |
| **Phase 3: Web界面** | → `docs/superpowers/plans/2026-06-19-vibe-trading-plan-p3.md` |
| T12 | Modify | `ashare_review/web/app.py` | `/api/chat`, `/api/chat/stream/`, `/api/agent/analyze` |
| T13 | Modify | `ashare_review/web/app.py` | Alpha API(3端点) + Strategy API(4端点) |
| T14 | Create | `ashare_review/web/templates/chat.html` | Chat页面+SSE流式JS |
| T14 | Modify | `ashare_review/web/app.py` | `/chat` 路由 |
| T15 | Create | `ashare_review/web/templates/alpha.html` | 因子库页面 |
| T15 | Create | `ashare_review/web/templates/strategies.html` | 策略编辑器页面 |
| T15 | Modify | `ashare_review/web/app.py` | `/alpha`, `/strategies` 路由 |
| T16 | Modify | `ashare_review/web/templates/base.html` | 导航添加3个新链接 |
| T17 | Modify | `ashare_review/web/templates/screening.html` | AI分析按钮+弹窗+JS |
| T18 | Modify | `ashare_review/web/templates/stock_detail.html` | AI多空观点区块 |
| T18 | Modify | `ashare_review/web/templates/review.html` | AI综述按钮+JS |
| T19 | Modify | `ashare_review/web/static/style.css` | 追加~200行新样式 |
| **Phase 4: 集成测试** | → `docs/superpowers/plans/2026-06-19-vibe-trading-plan-p4.md` |
| T20 | Modify | `ashare_review/report/daily.py` | build_summary_prompt + generate_llm_summary |
| T21 | Modify | `ashare_review/analysis/pick_analysis.py` | analyze_with_agents + use_agents参数 |
| T22 | Modify | `ashare_review/alpha/registry.py` | get_registry自启动注册 |
| T22 | Create | `ashare_review/tests/test_agents.py` | Agent/Provider/Tool单元测试 |
| T22 | Create | `ashare_review/tests/test_alpha.py` | FactorRegistry/Factor单元测试 |
| T22 | Create | `ashare_review/tests/test_nl_strategy.py` | Spec/Template/Validator单元测试 |

## 文件统计

- **新增文件**: 30个
- **修改文件**: 8个 (app.py, base.html, screening.html, stock_detail.html, review.html, style.css, daily.py, pick_analysis.py)
- **不变模块**: data/, screening/(6策略), utils/, analysis/indicators/pattern/volume/chip/backtest

## 执行顺序

Phase 1 → Phase 2 → Phase 3 → Phase 4，每个 Phase 内的 Task 顺序执行（有依赖），Phase 间不可跳过。

## 风险控制

- 所有新增模块用 `config/features.yaml` 开关控制
- LLM 调用超时 120s + try/catch 包裹
- API Key 缺失时 Provider 可创建但不发送请求
- 因子注册失败不阻塞应用启动
- 所有模板沿用现有 Jinja2 + 原生 JS，不引入新框架
