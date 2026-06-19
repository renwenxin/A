# Phase 4: 复盘增强 + 集成测试 (Tasks 20-22)

## Part of the Vibe-Trading integration plan

**Goal:** LLM复盘综述、Agent分析接入 pick_analysis、因子注册自启动、全链路集成测试

---

### Task 20: 复盘 LLM 综述

**Files:**
- Modify: `ashare_review/report/daily.py` — 新增 `generate_llm_summary()` 方法

- [ ] **Step 1: 在 DailyReport 类中添加 LLM 综述方法**

In `ashare_review/report/daily.py`, add method:

```python
    def build_summary_prompt(self, data: dict) -> str:
        """基于复盘数据构建 LLM 综述 prompt"""
        total = data.get('total_limit_ups', 0)
        sealed = data.get('sealed_count', 0)
        seal_rate = data.get('seal_rate', 0)
        max_consec = data.get('max_consecutive', 0)
        market_up = data.get('market_up', 0)
        market_down = data.get('market_down', 0)
        amount = data.get('total_amount_yi', 0)
        sentiment = data.get('sentiment_node', '')
        auction = data.get('auction_mood', '')

        # 热点板块
        sectors = data.get('hot_sectors', [])
        sector_text = ', '.join([f"{s.get('name', '')}({s.get('count', 0)}只涨停)"
                                for s in sectors[:5]])

        # 连板梯队
        ladder = data.get('ladder', [])
        ladder_text = ', '.join([f"{l.get('consecutive', 0)}板:{l.get('count', 0)}只"
                                for l in ladder[:5]])

        prompt = f"""请基于以下A股今日复盘数据，生成一份简洁的市场综述（Markdown格式，约300字）。

## 市场数据
- 涨停总数: {total}只，封板: {sealed}只，封板率: {seal_rate}%
- 最高连板: {max_consec}板
- 涨跌比: {market_up}:{market_down}
- 成交额: {amount:.0f}亿

## 热点板块
{sector_text}

## 连板梯队
{ladder_text}

## 情绪判断
- 情绪阶段: {sentiment}
- 竞价预判: {auction}

请按以下5个维度输出：

📊 **市场总览**: 今日整体定性（1-2句）

🔥 **热点板块**: 主线板块识别+持续性判断（1-2句）

📈 **情绪周期**: 当前阶段+次日大概率走向（1-2句）

⚡ **竞价预期**: 次日竞价氛围预判+关注方向（1-2句）

🎯 **操作建议**: 仓位建议+关注方向（1-2句）
"""
        return prompt

    def generate_llm_summary(self, trade_date=None) -> str:
        """生成 LLM 市场综述 — 需要 LLM Provider 可用"""
        data = self.generate(trade_date)
        prompt = self.build_summary_prompt(data)

        try:
            from ..agents.providers import create_provider
            provider = create_provider()
            result = provider.chat_sync([
                {'role': 'system', 'content': '你是A股资深复盘分析师，擅长提炼市场要点。'},
                {'role': 'user', 'content': prompt},
            ], temperature=0.3, max_tokens=1024)
            return result
        except Exception as e:
            return f'LLM综述生成失败: {e}\\\n\\n请确认已设置 API Key（DEEPSEEK_API_KEY 或 ANTHROPIC_API_KEY）'
```

- [ ] **Step 2: 在 Web 路由中暴露 LLM 综述**

In `app.py`, modify the `/review` route to optionally generate LLM summary:

```python
@app.route('/review')
def review():
    trade_date = request.args.get('date', None)
    try:
        report = DailyReport(tdx, ak_fetcher).generate(trade_date)
        # 如果请求了 LLM 综述
        if request.args.get('llm') == '1':
            report['llm_summary'] = DailyReport(tdx, ak_fetcher).generate_llm_summary(trade_date)
    except Exception as e:
        import traceback
        traceback.print_exc()
        report = {'date': 'N/A', 'total_limit_ups': 0, 'error': str(e)}
    return render_template('review.html', report=report)
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/report/daily.py ashare_review/web/app.py && git commit -m "feat: add LLM-generated market summary to daily report"
```

---

### Task 21: pick_analysis 接入 Agent 共识

**Files:**
- Modify: `ashare_review/analysis/pick_analysis.py` — 新增 `analyze_with_agents()` 函数

- [ ] **Step 1: 添加 Agent 共识分析入口**

```python
# 在 pick_analysis.py 末尾添加：

def analyze_with_agents(code: str, strategy: str = 'leader',
                        detail: dict = None) -> dict:
    """使用多Agent共识替代纯规则分析 — 需要 LLM API Key

    返回结构兼容现有的 {tech, suggestion} 格式，
    同时在 suggestion 中注入 agent_opinions。
    """
    # 先做基础技术面分析（不需要LLM，始终可用）
    tech = _tech_analysis(code, None, support_period=10)  # tdx will be created

    # 尝试 Agent 分析
    try:
        from ..agents.orchestrator import SwarmOrchestrator
        from ..data.tdx_reader import TdxReader
        import asyncio, json

        tdx = TdxReader()
        name = ''
        for s in ['sh', 'sz', 'bj']:
            market_dir = tdx._market_dir(s)
            import os
            fpath = os.path.join(market_dir, f'{s}{code}.day')
            if not os.path.exists(fpath):
                continue
            # Try to get name from screener
            from ..web.app import SCREENERS
            if strategy in SCREENERS:
                name = SCREENERS[strategy]._get_name(code)
            if not name:
                name = code
            break

        orch = SwarmOrchestrator()
        context = json.dumps(detail or {}, ensure_ascii=False)
        loop = asyncio.new_event_loop()
        plan = loop.run_until_complete(orch.analyze_stock(code, name, context))
        loop.close()

        # 转为兼容格式
        suggestion = {
            'action': plan.action,
            'entry_zone': [plan.entry_zone_low, plan.entry_zone_high],
            'stop_loss': plan.stop_loss,
            'targets': plan.targets,
            'position_pct': plan.position_pct,
            'risk_level': plan.risk_level,
            'rationale': plan.rationale,
            'agent_opinions': plan.agent_opinions,
            'source': 'agent_consensus',
        }
        return {'tech': tech, 'suggestion': suggestion}
    except Exception as e:
        # Fallback: 如果 Agent 不可用，返回空
        return {'tech': tech, 'suggestion': {
            'action': 'watch',
            'rationale': f'Agent分析不可用: {e}。基础技术面已完成，请查看tech字段。',
            'source': 'rule_based_fallback',
        }}
```

- [ ] **Step 2: 修改现有 `analyze_pick` 为可选的 Agent 增强**

```python
def analyze_pick(code: str, tdx, strategy: str, detail: dict,
                 use_agents: bool = False) -> dict:
    """对单个标的做技术分析
    
    Args:
        use_agents: 是否启用多Agent共识分析（需要LLM API Key）
    """
    support_period = {'auction': 5, 'one_two': 10, 'leader': 10,
                      'breakout': 20, 'institution': 20,
                      'sector_divergence': 10}.get(strategy, 10)
    tech = _tech_analysis(code, tdx, support_period=support_period)
    
    if use_agents:
        agent_result = analyze_with_agents(code, strategy, detail)
        suggestion = agent_result.get('suggestion', {})
    else:
        suggestion = _trading_suggestion(strategy, tech, detail)
    
    return {'tech': tech, 'suggestion': suggestion}
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/analysis/pick_analysis.py && git commit -m "feat: add multi-agent consensus analysis to pick_analysis"
```

---

### Task 22: 自启动因子注册 + 集成测试

**Files:**
- Modify: `ashare_review/alpha/registry.py` — 自动注册默认因子
- Create: `ashare_review/tests/test_agents.py`
- Create: `ashare_review/tests/test_alpha.py`
- Create: `ashare_review/tests/test_nl_strategy.py`

- [ ] **Step 1: 因子自启动注册**

```python
# 在 ashare_review/alpha/registry.py 的 get_registry() 中：
def get_registry() -> FactorRegistry:
    global _registry
    if _registry.count == 0:
        try:
            from .factors import register_all
            register_all(_registry)
        except Exception:
            pass  # 静默失败，不阻塞启动
    return _registry
```

- [ ] **Step 2: 编写测试 — test_agents.py**

```python
# ashare_review/tests/test_agents.py
"""Agent 模块单元测试 — 不需要真实 LLM API Key"""
import pytest
from ashare_review.agents.base import AgentOpinion, TradingPlan, BaseAgent
from ashare_review.agents.providers import create_provider, OpenAICompatProvider
from ashare_review.agents.tools import execute_tool, TOOL_DEFINITIONS
from ashare_review.agents.analysts import AGENT_CLASSES, create_agent


class TestAgentModels:
    def test_opinion_creation(self):
        o = AgentOpinion(agent='测试师', direction='bullish',
                         confidence=0.8, key_points=['要点1', '要点2'],
                         risks=['风险1'], score=75)
        assert o.direction == 'bullish'
        assert o.confidence == 0.8
        assert len(o.key_points) == 2

    def test_trading_plan_to_dict(self):
        plan = TradingPlan(symbol='000001', name='测试', action='buy',
                           entry_zone_low=10.0, entry_zone_high=10.5,
                           stop_loss=9.5, targets=[12.0],
                           position_pct=0.3, risk_level='medium',
                           rationale='测试理由')
        d = plan.to_dict()
        assert d['symbol'] == '000001'
        assert d['action'] == 'buy'
        assert len(d['entry_zone']) == 2


class TestProviders:
    def test_create_provider_no_key(self):
        """创建 Provider 即使没有 API Key 也不崩溃"""
        p = create_provider('deepseek')
        assert p.model == 'deepseek-chat'

    def test_tool_definitions_valid(self):
        """所有工具定义符合 OpenAI function-calling schema"""
        for tool in TOOL_DEFINITIONS:
            assert tool['type'] == 'function'
            assert 'name' in tool['function']
            assert 'parameters' in tool['function']


class TestAgents:
    def test_all_agents_created(self):
        """所有7个Agent可以创建"""
        assert len(AGENT_CLASSES) == 7
        for cls in AGENT_CLASSES:
            agent = cls(None)
            assert len(agent.name) > 0
            assert len(agent.system_prompt) > 100

    def test_create_agent_with_provider(self):
        provider = create_provider('deepseek')
        agent = create_agent(AGENT_CLASSES[0], provider)
        assert agent.name == AGENT_CLASSES[0].name


class TestTools:
    def test_market_breadth(self):
        result = execute_tool('get_market_breadth', {})
        assert 'up' in result or 'error' in result

    def test_unknown_tool(self):
        result = execute_tool('nonexistent', {})
        assert 'Unknown tool' in result
```

```python
# ashare_review/tests/test_alpha.py
"""Alpha 因子模块单元测试"""
import pytest
import pandas as pd
import numpy as np
from ashare_review.alpha.base import AlphaFactor, FactorReport
from ashare_review.alpha.registry import FactorRegistry, get_registry
from ashare_review.alpha.factors.gtja191.momentum import (
    GTJA_Momentum_5D, GTJA_MA_Deviation, GTJA_RSI, register_gtja_momentum,
)
from ashare_review.alpha.factors.custom.limit_up import LimitUpGene
from ashare_review.alpha.factors.custom.ma_system import MABullAlignment


def make_test_df(n=200):
    """生成合成日线数据"""
    np.random.seed(42)
    dates = pd.date_range('2025-01-01', periods=n, freq='B')
    close = 10 + np.cumsum(np.random.randn(n) * 0.2) + 5
    df = pd.DataFrame({
        'open': close - np.random.rand(n) * 0.5,
        'high': close + np.random.rand(n) * 0.8,
        'low': close - np.random.rand(n) * 0.8,
        'close': close,
        'volume': np.random.randint(10000, 100000, n),
    }, index=dates)
    return df


class TestFactorRegistry:
    def test_register_and_retrieve(self):
        r = FactorRegistry()
        f = GTJA_Momentum_5D()
        r.register(f)
        assert r.count == 1
        assert r.get('GTJA_001') is f

    def test_list_by_zoo(self):
        r = FactorRegistry()
        register_gtja_momentum(r)
        gtja = r.list_by_zoo('gtja191')
        assert len(gtja) >= 6
        for f in gtja:
            assert f.zoo == 'gtja191'

    def test_global_registry_auto_loads(self):
        r = get_registry()
        assert r.count >= 9  # Default registration on first call


class TestFactors:
    def test_momentum_5d(self):
        df = make_test_df()
        f = GTJA_Momentum_5D()
        series = f.calculate(df)
        assert len(series) == len(df)
        assert series.iloc[-1] is not None

    def test_ma_deviation(self):
        df = make_test_df()
        f = GTJA_MA_Deviation()
        series = f.calculate(df)
        assert len(series) == len(df)
        # 前19个应该是NaN
        assert pd.isna(series.iloc[0])

    def test_rsi_range(self):
        df = make_test_df()
        f = GTJA_RSI()
        series = f.calculate(df)
        valid = series.dropna()
        if len(valid) > 0:
            assert valid.min() >= 0
            assert valid.max() <= 100

    def test_limit_up_gene(self):
        df = make_test_df()
        f = LimitUpGene()
        series = f.calculate(df)
        valid = series.dropna()
        assert valid.min() >= 0
        assert valid.max() <= 1

    def test_ma_bull_alignment(self):
        df = make_test_df()
        # 需要提前计算均线
        for p in [5, 10, 20, 60]:
            df[f'ma{p}'] = df['close'].rolling(p).mean()
        f = MABullAlignment()
        series = f.calculate(df)
        valid = series.dropna()
        assert valid.max() <= 1.0
```

```python
# ashare_review/tests/test_nl_strategy.py
"""自然语言策略模块单元测试"""
import pytest
from ashare_review.nl_strategy.spec import StrategySpec, StrategyCondition, VALID_CONDITIONS
from ashare_review.nl_strategy.templates import BUILTIN_TEMPLATES
from ashare_review.nl_strategy.validator import validate_spec


class TestStrategySpec:
    def test_creation(self):
        cond = StrategyCondition('volume_ratio', {'min': 1.5}, weight=1.0)
        spec = StrategySpec(name='测试', description='测试描述',
                           conditions=[cond], max_results=15)
        assert spec.name == '测试'
        assert len(spec.conditions) == 1

    def test_to_dict_and_back(self):
        spec = StrategySpec(
            name='测试策略',
            conditions=[
                StrategyCondition('ma_breakout', {'period': 20}),
                StrategyCondition('exclude_st', {}),
            ],
            universe='all', max_results=20,
        )
        d = spec.to_dict()
        restored = StrategySpec.from_dict(d)
        assert restored.name == '测试策略'
        assert len(restored.conditions) == 2


class TestTemplates:
    def test_all_templates_valid(self):
        assert len(BUILTIN_TEMPLATES) == 5
        for tid, spec in BUILTIN_TEMPLATES.items():
            errors = validate_spec(spec)
            assert errors == [], f'{tid}: {errors}'

    def test_template_conditions(self):
        vol = BUILTIN_TEMPLATES['vol_breakout']
        types = [c.type for c in vol.conditions]
        assert 'ma_breakout' in types
        assert 'volume_ratio' in types
        assert 'exclude_st' in types


class TestValidator:
    def test_valid_spec(self):
        spec = BUILTIN_TEMPLATES['auction_surge']
        assert validate_spec(spec) == []

    def test_empty_conditions(self):
        spec = StrategySpec(name='空', conditions=[])
        errors = validate_spec(spec)
        assert len(errors) > 0

    def test_unknown_condition_type(self):
        spec = StrategySpec(name='错误',
                           conditions=[StrategyCondition('not_exist', {})])
        errors = validate_spec(spec)
        assert len(errors) > 0
```

- [ ] **Step 2: 运行测试**

```bash
cd D:/cursor/project && python -m pytest ashare_review/tests/test_agents.py ashare_review/tests/test_alpha.py ashare_review/tests/test_nl_strategy.py -v
# Expected: All tests PASS
```

- [ ] **Step 3: 运行现有测试确保无回归**

```bash
cd D:/cursor/project && python -m pytest ashare_review/tests/ -v
# Expected: All tests PASS (new + existing)
```

- [ ] **Step 4: Commit**

```bash
cd D:/cursor/project && git add ashare_review/alpha/registry.py ashare_review/tests/ && git commit -m "feat: add auto-registration for alpha factors and comprehensive tests"
```

---

## 验证清单

全部任务完成后，运行以下检查：

```bash
# 1. 所有测试通过
cd D:/cursor/project && python -m pytest ashare_review/tests/ -v

# 2. Flask 应用可启动
cd D:/cursor/project && python -c "from ashare_review.web.app import app; print('App OK, routes:', len(app.url_map._rules))"

# 3. 配置加载
cd D:/cursor/project && python -c "from ashare_review.config import get_config; print('Config OK')"

# 4. 因子注册
cd D:/cursor/project && python -c "from ashare_review.alpha.registry import get_registry; print(f'Factors: {get_registry().count}')"

# 5. 内置策略模板
cd D:/cursor/project && python -c "from ashare_review.nl_strategy.templates import BUILTIN_TEMPLATES; print(f'Templates: {len(BUILTIN_TEMPLATES)}')"

# 6. Agent 定义
cd D:/cursor/project && python -c "from ashare_review.agents.analysts import AGENT_CLASSES; print(f'Agents: {len(AGENT_CLASSES)}')"
```
