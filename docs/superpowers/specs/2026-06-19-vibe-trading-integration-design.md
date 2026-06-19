# Vibe-Trading 深度融合设计文档

> 将 HKUDS/Vibe-Trading 核心能力（多智能体LLM分析、Alpha因子库、自然语言策略）融入现有A股复盘选股系统

**日期**: 2026-06-19 | **状态**: 已确认 | **方案**: 渐进式模块叠加

---

## 1. 目标与范围

### 1.1 新增模块

| 模块 | 路径 | 说明 |
|------|------|------|
| 多智能体LLM分析 | `ashare_review/agents/` | 7个A股专属分析师Agent + Swarm调度 |
| Alpha因子库 | `ashare_review/alpha/` | ~100个精选A股因子 + 评估/对比 |
| 自然语言策略 | `ashare_review/nl_strategy/` | NL→策略参数 + 执行 + 回测 |

### 1.2 增强模块

| 模块 | 改动说明 |
|------|---------|
| `web/app.py` | 新增 Chat SSE API + Alpha/Strategy API端点 |
| `web/templates/` | 新增 chat.html / alpha.html / strategies.html |
| `web/static/style.css` | 新增 Agent卡片 / 对话 / 因子卡片样式 |
| `report/daily.py` | 新增 `generate_llm_summary()` LLM综述 |
| `analysis/pick_analysis.py` | 新增多Agent共识分析入口 |

### 1.3 不变模块

`data/`, `screening/`（6策略原封不动），`utils/`, `analysis/indicators.py`, `analysis/pattern.py`, `analysis/volume.py`, `analysis/chip.py`, `analysis/backtest.py`

---

## 2. 多智能体模块 `agents/`

### 2.1 文件结构

```
agents/
  __init__.py
  base.py            # Agent基类 + AgentOpinion/TradingPlan 数据模型
  providers.py       # ClaudeProvider / DeepSeekProvider / OpenAIProvider
  analysts.py        # 7个Analyst类定义
  orchestrator.py    # SwarmOrchestrator: 并行→辩论→共识
  tools.py           # Agent可调用的工具函数封装
```

### 2.2 Agent 定义

| Agent | system_prompt要点 | tools |
|-------|------------------|-------|
| `TechnicalAnalyst` | 均线/量价/形态/筹码专家 | indicators.enrich_all, pattern.detect_*, chip.calc_*, volume.detect_* |
| `FundamentalAnalyst` | 财报/估值/机构数据 | akshare财报接口, institution_holder_count |
| `SentimentAnalyst` | 涨停情绪/封板率/周期 | get_limit_up_pool, 情绪周期判断函数 |
| `SectorAnalyst` | 板块轮动/资金方向 | get_concept_boards, 板块涨停潮分析 |
| `FlowAnalyst` | 龙虎榜/北向/大单流向 | get_lhb, 北向资金接口 |
| `RiskManager` | 风控/仓位/回撤评估 | backtest数据, 波动率计算 |
| `LeadAnalyst` | 辩论综合→最终交易计划 | 可查看所有Agent输出 |

### 2.3 数据模型

```python
@dataclass
class AgentOpinion:
    agent: str           # 分析师名称
    direction: str       # 'bullish' | 'bearish' | 'neutral'
    confidence: float    # 0.0 ~ 1.0
    key_points: list[str]
    risks: list[str]
    score: int           # 0-100

@dataclass
class TradingPlan:
    symbol: str
    name: str
    action: str          # 'buy' | 'sell' | 'hold' | 'watch'
    entry_zone: tuple[float, float]
    stop_loss: float
    targets: list[float]
    position_pct: float  # 建议仓位比例 0.0-1.0
    rationale: str       # Markdown综合理由
    agent_opinions: list[AgentOpinion]
    risk_level: str      # 'low' | 'medium' | 'high'
```

### 2.4 工作流

```
用户输入 → LeadAnalyst拆解任务
                │
    ┌───────────┼───────────┬───────────┬───────────┐
    ▼           ▼           ▼           ▼           ▼
Technical  Fundamental  Sentiment   Sector      Flow
Analyst    Analyst      Analyst     Analyst     Analyst
    │           │           │           │           │
    └───────────┴───────────┴───────────┴───────────┘
                │ 各Agent输出 AgentOpinion
                ▼
         RiskManager 审核
                │
                ▼
         LeadAnalyst 辩论+综合 → TradingPlan
```

- 前6个Agent**并行执行**（asyncio.gather / 线程池）
- RiskManager在收到所有意见后执行
- LeadAnalyst最后综合，收到完整上下文

### 2.5 工具函数 (`tools.py`)

将现有系统能力封装为 Agent 可调用的 function calling 工具：

| 工具名 | 封装对象 | 用途 |
|--------|---------|------|
| `get_technical_indicators` | indicators.enrich_all | 计算全量技术指标 |
| `detect_patterns` | pattern.detect_* | 形态检测 |
| `analyze_chip` | chip.calc_chip_distribution | 筹码分布 |
| `get_limit_up_pool` | AkshareFetcher.get_limit_up_pool | 涨停板数据 |
| `get_sector_boards` | AkshareFetcher.get_concept_boards | 板块行情 |
| `get_lhb_top` | AkshareFetcher.get_lhb | 龙虎榜 |
| `get_institution_holding` | AkshareFetcher.get_institution_holder_count | 机构持仓 |
| `run_screener` | BaseScreener.screen | 执行指定筛选策略 |
| `get_market_breadth` | TdxReader.get_market_breadth | 涨跌家数 |
| `read_daily_kline` | TdxReader.read_daily | 个股日线 |

### 2.6 Provider 配置

`ashare_review/config/llm.yaml`:

```yaml
default_provider: deepseek   # 便宜快速，适合批量分析
providers:
  deepseek:
    model: deepseek-chat
    api_key: ${DEEPSEEK_API_KEY}
    base_url: https://api.deepseek.com/v1
  claude:
    model: claude-sonnet-4-6
    api_key: ${ANTHROPIC_API_KEY}
  openai:
    model: gpt-4o
    api_key: ${OPENAI_API_KEY}
agent_defaults:
  temperature: 0.3
  max_tokens: 2048
```

---

## 3. Alpha 因子库 `alpha/`

### 3.1 文件结构

```
alpha/
  __init__.py
  base.py            # AlphaFactor 基类
  registry.py        # FactorRegistry 注册中心
  evaluator.py       # FactorReport + evaluate_factor + rank_factors
  compare.py         # 多因子对比 + CLI入口

  factors/
    __init__.py
    gtja191/
      __init__.py
      momentum.py      # 动量类 ~12个
      reversal.py      # 反转类 ~10个
      volume_price.py  # 量价类 ~15个
      volatility.py    # 波动类 ~8个
      liquidity.py     # 流动性类 ~5个
    alpha101/
      __init__.py
      trend.py         # 趋势类 ~15个
      mean_reversion.py # 均值回归类 ~15个
    custom/
      __init__.py
      limit_up.py      # 涨停基因因子
      chip.py          # 筹码集中度因子
      auction.py       # 竞价强度因子
      ma_system.py     # 均线排列因子
      volume_cannon.py # 量炮识别因子
```

### 3.2 核心接口

```python
class AlphaFactor(ABC):
    id: str              # 'GTJA_001'
    name: str            # 中文名
    name_en: str         # 英文名
    category: str        # 'momentum'|'reversal'|'volume'|'volatility'|'liquidity'
    horizon: int         # 预测周期(天)，默认5
    zoo: str             # 'gtja191'|'alpha101'|'custom'

    @abstractmethod
    def calculate(self, df: pd.DataFrame) -> pd.Series: ...

class FactorRegistry:
    _factors: dict[str, AlphaFactor]

    def register(self, factor: AlphaFactor): ...
    def get(self, id: str) -> AlphaFactor: ...
    def list_by_zoo(self, zoo: str) -> list[AlphaFactor]: ...
    def list_by_category(self, category: str) -> list[AlphaFactor]: ...
    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame: ...

@dataclass
class FactorReport:
    factor_id: str
    ic_mean: float
    ic_std: float
    ir: float              # IC_mean / IC_std
    ic_positive_ratio: float
    long_ret: float        # 多头年化收益
    long_ret: float
    short_ret: float
    turnover: float        # 日均换手率(因子值)
    stars: int             # 1-5 星级

def evaluate_factor(factor: AlphaFactor, df: pd.DataFrame,
                    forward_period: int = 5) -> FactorReport: ...

def rank_factors(factors: list[AlphaFactor], df: pd.DataFrame,
                 sort_by: str = 'ir') -> list[FactorReport]: ...
```

### 3.3 与筛选器对接

在 `BaseScreener` 中增加可选因子加权：

```python
class BaseScreener:
    def __init__(self, ..., alpha_weights: dict[str, float] = None):
        self.alpha_weights = alpha_weights or {}  # {'GTJA_001': 0.2, ...}
        self.registry = FactorRegistry()

    def _alpha_score(self, code: str, df: pd.DataFrame) -> float:
        """计算加权Alpha因子得分"""
        ...
```

### 3.4 CLI 接口

```bash
# 对中证500股票池评估GTJA191因子
python -m ashare_review.alpha.bench --zoo gtja191 --universe zz500 --period 2024-2025

# 对比指定因子
python -m ashare_review.alpha.compare GTJA_001 GTJA_005 GTJA_012 --sort ir

# 列出所有因子
python -m ashare_review.alpha.list --zoo custom
```

---

## 4. 自然语言策略 `nl_strategy/`

### 4.1 文件结构

```
nl_strategy/
  __init__.py
  spec.py            # StrategyCondition / StrategySpec 数据模型
  parser.py          # NlStrategyParser: LLM驱动的NL→StrategySpec
  executor.py        # StrategyExecutor: 执行筛选
  templates.py       # 内置5个策略模板
  validator.py       # 参数合法性校验
```

### 4.2 数据模型

```python
@dataclass
class StrategyCondition:
    type: str           # 条件类型
    params: dict        # 类型特化参数
    weight: float = 1.0

# 支持的条件类型:
# - 'market_cap': {min, max}           流通市值范围(亿)
# - 'ma_breakout': {period}            均线突破(放量确认)
# - 'ma_position': {period, above}     价格站上/跌破均线
# - 'sector_limit_up': {min_count}     板块涨停家数
# - 'limit_up_time': {before}          涨停时间早于
# - 'seal_amount': {min}               封单金额(亿)
# - 'turnover': {min, max}             换手率范围
# - 'volume_ratio': {min}              量比
# - 'consecutive': {min, max}          连板数
# - 'institution_count': {min}         机构持仓家数
# - 'float_market_cap': {min, max}     流通市值
# - 'exclude_st': {}                   排除ST
# - 'exclude_bj': {}                   排除北交所
# - 'pattern': {type}                  形态: box_breakout/w_bottom/n_pattern
# - 'chip': {signal}                   筹码信号: single_peak/bottom_lock

@dataclass
class StrategySpec:
    name: str
    description: str     # 原始NL描述
    conditions: list[StrategyCondition]
    universe: str        # 'all' | 'csi300' | 'zz500' | 'gem' | 'main'
    max_results: int = 20
    sort_by: str = 'score'  # 'score' | 'market_cap' | 'turnover'
```

### 4.3 内置模板 (`templates.py`)

| ID | 名称 | 核心条件 | 适用场景 |
|----|------|---------|---------|
| `vol_breakout` | 放量突破 | 放量突破20日高点 + 均线多头 | 趋势启动 |
| `shrink_pullback` | 缩量回调 | 缩量2天 + 守住MA10 | 低吸买点 |
| `auction_surge` | 竞价异动 | 高开3% + 竞价量>前日3倍 | 竞价抢筹 |
| `sector_leader` | 板块共振 | 板块涨停≥3 + 个股放量领涨 | 板块龙头 |
| `inst_quality` | 机构加持 | 机构持仓>100家 + PE<行业均值 | 中线布局 |

### 4.4 解析流程 (`parser.py`)

```
NL输入 → LLM (structured output)
            │
            ▼
       StrategySpec (JSON Schema约束)
            │
            ▼
       validator.validate(spec)
       - 检查条件类型是否合法
       - 检查参数范围
       - 检查互斥条件
            │
            ▼
       StrategySpec (合法)
```

### 4.5 执行流程 (`executor.py`)

```
StrategySpec
    │
    ▼
executor.execute()
    │
    ├── 加载全市场spot数据
    ├── 逐个条件过滤
    │   ├── 低成本条件优先 (exclude_st, market_cap)
    │   └── 高成本条件后置 (pattern, chip)
    ├── 加权评分
    ├── 排序取Top N
    │
    ▼
list[ScreeningResult]
    │
    ▼
(可选) executor.backtest(spec, days=60) → 回测报告
```

---

## 5. Web UI 增强

### 5.1 新增页面

| 路由 | 模板 | 说明 |
|------|------|------|
| `/chat` | `chat.html` | AI对话投研，SSE流式多Agent输出 |
| `/alpha` | `alpha.html` | 因子库浏览器 + 对比排名 |
| `/strategies` | `strategies.html` | NL策略编辑器 + 模板 |

### 5.2 新增 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chat` | POST | 发送消息，返回 task_id |
| `/api/chat/stream/<task_id>` | GET | SSE流式接收Agent结果 |
| `/api/chat/history` | GET | 对话历史 |
| `/api/alpha/list` | GET | 因子列表(filter: ?zoo=gtja191) |
| `/api/alpha/eval` | POST | 评估因子(body: {factor_id, code, days}) |
| `/api/alpha/compare` | POST | 多因子对比(body: {factor_ids, universe}) |
| `/api/strategy/parse` | POST | NL解析(body: {description}) |
| `/api/strategy/execute` | POST | 执行策略(body: StrategySpec) |
| `/api/strategy/backtest` | POST | 回测策略(body: StrategySpec + days) |
| `/api/strategy/templates` | GET | 获取内置模板列表 |
| `/api/agent/analyze` | POST | 单股Agent分析(body: {code, strategy}) |

### 5.3 现有页面增强

- **`screening.html`**: 每个结果新增 `🤖 AI分析` 按钮，点击触发Agent并行分析弹窗
- **`stock_detail.html`**: 底部新增 "AI多空观点" 区块，显示各Agent意见
- **`review.html`**: 顶部新增 "AI市场综述" 区块，LLM生成当日复盘要点

### 5.4 技术选型

- **流式推送**: Flask SSE (Server-Sent Events)，单线程内用 `asyncio` 管理Agent并行
- **Agent并行**: `concurrent.futures.ThreadPoolExecutor`（LLM调用是IO密集）
- **对话持久化**: SQLite（复用现有 `cache.db`，新增 `chat_history` 表）
- **前端**: 原生 EventSource + 渐进式渲染Agent卡片（无需引入WebSocket框架）

---

## 6. 复盘报告增强 `report/daily.py`

### 6.1 新增方法

```python
class DailyReport:
    # 现有方法不变
    def generate(self, trade_date) -> dict: ...

    # 新增
    async def generate_llm_summary(self, trade_date) -> str:
        """LLM生成Markdown格式市场综述"""
        data = self.generate(trade_date)
        prompt = _build_summary_prompt(data)
        return await llm.chat(prompt)
```

### 6.2 LLM 综述覆盖维度

| 维度 | 输入数据 | 输出 |
|------|---------|------|
| 📊 市场总览 | 涨停总数/封板率/成交额/涨跌比 | 一句话定性 + 对比昨日 |
| 🔥 热点板块 | 行业涨停分布/涨停潮板块 | 主线板块识别 + 持续性判断 |
| 📈 情绪周期 | 情绪节点/一字板比重/最高连板 | 当前阶段 + 次日预判 |
| ⚡ 竞价预期 | 今日强势股/弱转强候选 | 次日竞价大概率氛围 |
| 🎯 操作建议 | 综合以上 | 建议仓位 + 关注方向 |

### 6.3 不改动

- `_analyze_sector_tide()` — 板块涨停潮分析保留
- `_judge_sentiment_node()` — 情绪周期判断保留
- `_find_weak_to_strong_candidates()` — 弱转强候选池保留
- LLM 作为"上层叙事"补充，不替代现有规则判断

---

## 7. 实现阶段

### Phase 1: 基础设施 (预计 3-4 小时)

- `agents/base.py` + `providers.py` — Agent基类和LLM Provider
- `agents/tools.py` — 工具函数封装
- `alpha/base.py` + `registry.py` — 因子基类和注册中心
- `nl_strategy/spec.py` + `templates.py` — 数据模型和内置模板
- 配置文件 `config/llm.yaml`
- Web API 框架（Flask SSE基础）

### Phase 2: 核心能力 (预计 4-5 小时)

- `agents/analysts.py` — 7个Agent实现
- `agents/orchestrator.py` — Swarm调度
- `alpha/factors/` — 首批~30个核心因子实现（gtja191 15个 + alpha101 10个 + custom 5个）
- `alpha/evaluator.py` + `compare.py` — 评估和对比
- `nl_strategy/parser.py` + `executor.py` — NL解析和执行

### Phase 3: Web 界面 (预计 3-4 小时)

- `/chat` 页面 + SSE流式渲染
- `/alpha` 因子库页面
- `/strategies` 策略编辑器页面
- 现有页面增强（AI按钮/多空面板/综述）

### Phase 4: 复盘增强 + 集成测试 (预计 2-3 小时)

- `report/daily.py` LLM综述
- Agent分析接入 `pick_analysis.py`
- 全链路集成测试
- CLI入口（`/chat` 兜底命令等）

---

## 8. 风险与注意事项

1. **LLM调用成本**: DeepSeek作为默认Provider（极低成本），高端分析用Claude。批量场景限制并发
2. **A股数据合规**: 因子计算不涉及未来函数，回测需严格对齐交易日历
3. **SSE兼容性**: Flask单线程模型对SSE支持有限，大数据量时需考虑用 `gevent` 或拆分
4. **本地数据依赖**: Alpha因子计算依赖TDX本地数据，离线场景仍需能工作
5. **渐进上线**: 每个模块独立可开关，在 `ashare_review/config/features.yaml` 中控制：
   ```yaml
   features:
     agents:
       enabled: true
       default_provider: deepseek
     alpha:
       enabled: true
     nl_strategy:
       enabled: true
   ```
