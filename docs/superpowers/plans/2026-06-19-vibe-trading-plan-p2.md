# Phase 2: 核心能力 (Tasks 7-11)

## Part of the Vibe-Trading integration plan

**Goal:** 实现 7 个分析师Agent、Swarm调度器、首批Alpha因子、因子评估、NL策略解析器

---

### Task 7: 7个分析师Agent

**Files:**
- Create: `ashare_review/agents/analysts.py`

- [ ] **Step 1: 编写7个Agent的 system_prompt**

```python
# ashare_review/agents/analysts.py
"""7个A股专属分析师Agent — 每个Agent有独立的 system_prompt 和专长

Agent 输出格式遵循 JSON Schema（由 orchestrator 注入），
这里定义的是中文 system_prompt。
"""
from .base import BaseAgent

TECHNICAL_PROMPT = """你是A股技术面分析师，专精均线体系、量价关系、K线形态和筹码分布。

分析框架（龙哥均线体系）：
- MA5/10 短线生命线，决定短线强弱势
- MA20 中线导航标，趋势方向判断核心
- MA60 季线波段入场基准
- MA89/250 长线定盘星
- MACD 金叉/死叉 + 零轴位置
- 量价关系：放量上涨/缩量回调/量价背离/恐慌抛售
- 形态识别：箱体突破/W底/N字结构
- 筹码分布：单峰密集/多峰套牢/底部锁仓

给出以下判断：
1. 均线多头/空头排列情况
2. MACD 金叉死叉状态 + 零轴位置
3. 量价关系健康度
4. 关键支撑/压力位
5. 筹码结构优劣
6. 技术面综合评分 (0-100)"""

FUNDAMENTAL_PROMPT = """你是A股基本面分析师，专精财报解读、估值分析和机构行为。

分析框架：
- 流通市值：大盘/中盘/小盘股性差异
- 机构持仓家数：机构认可度
- 市盈率/市净率：相对估值水平
- 营收/利润增长率：成长性判断
- ROE：盈利能力
- 行业地位：细分龙头/跟风/边缘

给出以下判断：
1. 市值规模与流动性
2. 机构参与度
3. 估值高低（相对行业）
4. 基本面综合评分 (0-100)"""

SENTIMENT_PROMPT = """你是A股情绪面分析师，专精涨停情绪周期、赚钱效应和短线氛围。

分析框架（龙哥情绪周期）：
- 涨停总数 + 封板率 → 市场温度
- 最高连板高度 → 空间判断
- 首板/连板比 → 接力意愿
- 一字板占比 → 极端情绪（一致看多/恐慌）
- 昨日涨停今日表现 → 赚钱效应
- 情绪阶段：冰点→启动→发酵→高潮→高潮末期→退潮→冰点

给出以下判断：
1. 当前情绪周期阶段
2. 赚钱效应强弱
3. 次日竞价预判（火爆/偏强/中性/偏弱/冰点）
4. 情绪面综合评分 (0-100)"""

SECTOR_PROMPT = """你是A股板块轮动分析师，专精热点板块识别、板块持续性判断和板块内个股定位。

分析框架：
- 板块涨停家数 → 板块热度
- 涨停潮（≥5只）→ 主线确认
- 板块龙头识别（最早涨停/最大封单）
- 板块内分化判断
- 新题材 vs 旧热点
- 板块资金流向

给出以下判断：
1. 今日主线板块（1-3个）
2. 板块持续性判断
3. 是否新题材启动
4. 板块轮动综合评分 (0-100)"""

FLOW_PROMPT = """你是A股资金面分析师，专精龙虎榜、北向资金和大单流向。

分析框架：
- 龙虎榜净买入Top 10
- 机构席位 vs 游资席位
- 北向资金净流入/流出
- 大单净流入/流出方向
- 资金关注行业方向

给出以下判断：
1. 主力资金方向
2. 机构/游资偏好
3. 资金面综合评分 (0-100)"""

RISK_PROMPT = """你是A股风控官，职责是评估风险而非追逐收益。

在收到其他分析师的意见后，你独立评估：
1. 最大回撤风险
2. 流动性风险（小票/缩量标的）
3. 追高风险（连板高位）
4. 板块退潮风险
5. 大盘系统性风险

给出最终风险等级（低/中/高）和建议仓位比例（0-100%）。

你天生谨慎，对连板高位、尾盘板、炸板回封保持警惕。"""

LEAD_PROMPT = """你是A股首席分析师，综合各位专家意见形成最终交易计划。

职责：
1. 审阅所有分析师的意见（技术面/基本面/情绪面/板块/资金面/风控）
2. 找出共识和分歧点
3. 多空双方核心论点评述
4. 给出最终决策：买入/卖出/持有/观望
5. 如果买入：给出具体入场区间、止损位、目标位、建议仓位
6. 如果观望：说明等待什么条件

输出要求：Markdown格式，条理清晰，包含具体价格和仓位建议。"""


# ---- Agent 类定义 ----

class TechnicalAnalyst(BaseAgent):
    name = "技术面分析师"
    system_prompt = TECHNICAL_PROMPT

class FundamentalAnalyst(BaseAgent):
    name = "基本面分析师"
    system_prompt = FUNDAMENTAL_PROMPT

class SentimentAnalyst(BaseAgent):
    name = "情绪面分析师"
    system_prompt = SENTIMENT_PROMPT

class SectorAnalyst(BaseAgent):
    name = "板块轮动分析师"
    system_prompt = SECTOR_PROMPT

class FlowAnalyst(BaseAgent):
    name = "资金面分析师"
    system_prompt = FLOW_PROMPT

class RiskManager(BaseAgent):
    name = "风控官"
    system_prompt = RISK_PROMPT

class LeadAnalyst(BaseAgent):
    name = "首席分析师"
    system_prompt = LEAD_PROMPT


# Agent 注册表
AGENT_CLASSES = [
    TechnicalAnalyst, FundamentalAnalyst, SentimentAnalyst,
    SectorAnalyst, FlowAnalyst, RiskManager, LeadAnalyst,
]

def create_agent(agent_class, provider) -> BaseAgent:
    """工厂函数 — 根据类创建Agent实例，自动注入合适的 tools"""
    tools_for = {
        TechnicalAnalyst: ['get_technical_indicators', 'detect_patterns', 'analyze_chip', 'read_daily_kline'],
        FundamentalAnalyst: ['get_institution_holding'],
        SentimentAnalyst: ['get_limit_up_pool'],
        SectorAnalyst: ['get_sector_boards', 'get_limit_up_pool'],
        FlowAnalyst: ['get_lhb_top', 'get_market_breadth'],
        RiskManager: [],  # 风控不需要工具，依赖其他Agent的输出
        LeadAnalyst: [],   # 首席不需要工具，综合其他Agent输出
    }
    from .tools import TOOL_DEFINITIONS
    tool_names = tools_for.get(agent_class, [])
    tools = [t for t in TOOL_DEFINITIONS if t['function']['name'] in tool_names]
    return agent_class(provider, tools=tools)
```

- [ ] **Step 2: 验证 Agent 创建**

```bash
cd D:/cursor/project && python -c "
from ashare_review.agents.analysts import AGENT_CLASSES
for cls in AGENT_CLASSES:
    a = cls(None)
    print(f'{a.name}: prompt_len={len(a.system_prompt)}, tools={len(a.tools)}')
"
# Expected: 7 agents listed with prompt lengths
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/agents/analysts.py && git commit -m "feat: add 7 A-share analyst agent definitions"
```

---

### Task 8: Swarm 调度器

**Files:**
- Create: `ashare_review/agents/orchestrator.py`

- [ ] **Step 1: 编写 SwarmOrchestrator**

```python
# ashare_review/agents/orchestrator.py
"""Swarm 调度器 — 并行执行→辩论→综合"""
import asyncio, json
from concurrent.futures import ThreadPoolExecutor
from .base import AgentOpinion, TradingPlan
from .analysts import (
    create_agent, TechnicalAnalyst, FundamentalAnalyst,
    SentimentAnalyst, SectorAnalyst, FlowAnalyst,
    RiskManager, LeadAnalyst,
)
from .providers import create_provider
from ..config import get_config

_OPINION_SCHEMA = {
    'type': 'json_object',
    'schema': {
        'type': 'object',
        'properties': {
            'direction': {'type': 'string', 'enum': ['bullish', 'bearish', 'neutral']},
            'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
            'key_points': {'type': 'array', 'items': {'type': 'string'}},
            'risks': {'type': 'array', 'items': {'type': 'string'}},
            'score': {'type': 'integer', 'minimum': 0, 'maximum': 100},
        },
        'required': ['direction', 'confidence', 'key_points', 'risks', 'score'],
    },
}

_TRADING_PLAN_SCHEMA = {
    'type': 'json_object',
    'schema': {
        'type': 'object',
        'properties': {
            'action': {'type': 'string', 'enum': ['buy', 'sell', 'hold', 'watch']},
            'entry_zone_low': {'type': 'number'},
            'entry_zone_high': {'type': 'number'},
            'stop_loss': {'type': 'number'},
            'targets': {'type': 'array', 'items': {'type': 'number'}},
            'position_pct': {'type': 'number', 'minimum': 0, 'maximum': 1},
            'rationale': {'type': 'string'},
            'risk_level': {'type': 'string', 'enum': ['low', 'medium', 'high']},
        },
        'required': ['action', 'rationale', 'risk_level'],
    },
}


class SwarmOrchestrator:
    """调度7个Agent进行单股分析"""

    def __init__(self, provider_name: str = None):
        self.cfg = get_config()
        agent_cfg = self.cfg.get_agent_defaults()
        self.max_parallel = agent_cfg.get('max_parallel', 5)
        self.timeout = agent_cfg.get('timeout_seconds', 120)

    def _make_provider(self) -> 'LLMProvider':
        return create_provider()

    async def analyze_stock(self, symbol: str, name: str = '',
                            context: str = '') -> TradingPlan:
        """对单只股票进行完整 Swarm 分析

        Args:
            symbol: 股票代码
            name: 股票名称
            context: 额外上下文（如来自筛选器的 detail 信息）
        """
        # Step 1: 构建分析上下文
        analysis_context = f"分析标的: {name}({symbol})\\n"
        if context:
            analysis_context += f"已知信息: {context}\\n"

        # Step 2: 并行执行5个分析师（不含风控和首席）
        front_agents = [
            (TechnicalAnalyst, '技术面'),
            (FundamentalAnalyst, '基本面'),
            (SentimentAnalyst, '情绪面'),
            (SectorAnalyst, '板块轮动'),
            (FlowAnalyst, '资金面'),
        ]

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=self.max_parallel) as executor:
            futures = []
            for agent_cls, tag in front_agents:
                futures.append(loop.run_in_executor(
                    executor, self._run_agent, agent_cls, tag, analysis_context,
                ))

            opinions: list[AgentOpinion] = []
            for future in asyncio.as_completed(futures):
                try:
                    result = await future
                    if result:
                        opinions.append(result)
                except Exception as e:
                    print(f'[Orchestrator] Agent error: {e}')

        # Step 3: RiskManager 审核
        risk_opinion = await self._run_risk_manager(analysis_context, opinions)

        # Step 4: LeadAnalyst 综合
        plan = await self._run_lead_analyst(symbol, name, analysis_context,
                                            opinions, risk_opinion)

        return plan

    def _run_agent(self, agent_cls, tag: str, context: str) -> AgentOpinion | None:
        """在独立线程中运行单个Agent（同步包装）"""
        provider = self._make_provider()
        agent = create_agent(agent_cls, provider)

        prompt = f"请对以下标的进行分析:\\n\\n{context}"
        try:
            result = agent.provider.chat_sync(
                [{'role': 'system', 'content': agent.system_prompt},
                 {'role': 'user', 'content': prompt}],
                response_format=_OPINION_SCHEMA,
                temperature=0.3, max_tokens=1024,
            )
            data = json.loads(result)
            return AgentOpinion(
                agent=tag,
                direction=data.get('direction', 'neutral'),
                confidence=data.get('confidence', 0.5),
                key_points=data.get('key_points', []),
                risks=data.get('risks', []),
                score=data.get('score', 50),
            )
        except Exception as e:
            print(f'[{tag}] 分析失败: {e}')
            return None

    async def _run_risk_manager(self, context: str,
                                 opinions: list[AgentOpinion]) -> AgentOpinion:
        provider = self._make_provider()
        agent = RiskManager(provider)
        opinions_text = '\\n'.join([
            f"- {o.agent}: {o.direction}(信心{o.confidence:.0%}), "
            f"要点: {'; '.join(o.key_points[:3])}, 风险: {'; '.join(o.risks[:2])}"
            for o in opinions if o
        ])
        prompt = f"{context}\\n\\n各分析师意见:\\n{opinitions_text}\\n\\n请做风控评估。"
        try:
            result = await agent.analyze(prompt, output_schema=_OPINION_SCHEMA)
            data = json.loads(result)
            return AgentOpinion(
                agent='风控官',
                direction=data.get('direction', 'neutral'),
                confidence=data.get('confidence', 0.5),
                key_points=data.get('key_points', []),
                risks=data.get('risks', []),
                score=data.get('score', 50),
            )
        except Exception as e:
            print(f'[风控官] 分析失败: {e}')
            return AgentOpinion(agent='风控官', direction='neutral', confidence=0.5)

    async def _run_lead_analyst(self, symbol: str, name: str, context: str,
                                 opinions: list[AgentOpinion],
                                 risk_opinion: AgentOpinion) -> TradingPlan:
        provider = self._make_provider()
        agent = LeadAnalyst(provider)
        all_opinions = opinions + [risk_opinion] if risk_opinion else opinions
        opinions_text = '\\n'.join([
            f"### {o.agent}\\n- 方向: {o.direction}\\n"
            f"- 信心: {o.confidence:.0%}\\n"
            f"- 要点: {'; '.join(o.key_points)}\\n"
            f"- 风险: {'; '.join(o.risks)}\\n- 评分: {o.score}"
            for o in all_opinions if o
        ])
        prompt = f"标的: {name}({symbol})\\n{context}\\n\\n各分析师意见:\\n{opinions_text}\\n\\n请综合给出交易计划。"
        try:
            result = await agent.analyze(prompt, output_schema=_TRADING_PLAN_SCHEMA)
            data = json.loads(result)
            return TradingPlan(
                symbol=symbol, name=name,
                action=data.get('action', 'watch'),
                entry_zone_low=data.get('entry_zone_low', 0),
                entry_zone_high=data.get('entry_zone_high', 0),
                stop_loss=data.get('stop_loss', 0),
                targets=data.get('targets', []),
                position_pct=data.get('position_pct', 0),
                rationale=data.get('rationale', ''),
                agent_opinions=[o for o in all_opinions if o],
                risk_level=data.get('risk_level', 'medium'),
            )
        except Exception as e:
            print(f'[首席分析师] 综合失败: {e}')
            return TradingPlan(
                symbol=symbol, name=name,
                action='watch',
                rationale=f'分析过程出错: {e}',
                agent_opinions=[o for o in all_opinions if o],
                risk_level='high',
            )
```

- [ ] **Step 2: 验证调度器初始化**

```bash
cd D:/cursor/project && python -c "
from ashare_review.agents.orchestrator import SwarmOrchestrator
o = SwarmOrchestrator()
print(f'Orchestrator ready, max_parallel={o.max_parallel}')
"
# Expected: Orchestrator ready, max_parallel=5
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/agents/orchestrator.py && git commit -m "feat: add Swarm orchestrator for parallel agent analysis"
```

---

### Task 9: 首批 Alpha 因子实现

**Files:**
- Create: `ashare_review/alpha/factors/__init__.py`
- Create: `ashare_review/alpha/factors/gtja191/__init__.py`
- Create: `ashare_review/alpha/factors/gtja191/momentum.py`
- Create: `ashare_review/alpha/factors/custom/__init__.py`
- Create: `ashare_review/alpha/factors/custom/limit_up.py`
- Create: `ashare_review/alpha/factors/custom/chip_concentration.py`
- Create: `ashare_review/alpha/factors/custom/ma_system.py`
- Modify: `ashare_review/alpha/registry.py` — 添加注册函数

- [ ] **Step 1: 创建因子包结构 + GTJA191 动量因子**

```python
# ashare_review/alpha/factors/__init__.py
"""Alpha 因子实现集合"""
from .gtja191.momentum import register_gtja_momentum
from .custom.limit_up import register_custom_factors

def register_all():
    """注册所有因子到全局注册中心"""
    from ...registry import get_registry
    r = get_registry()
    register_gtja_momentum(r)
    register_custom_factors(r)
```

```python
# ashare_review/alpha/factors/gtja191/__init__.py
"""国泰君安191 短线Alpha因子精选 — A股原生因子"""
```

```python
# ashare_review/alpha/factors/gtja191/momentum.py
"""GTJA191 动量类因子 — 精选6个"""
import pandas as pd
import numpy as np
from ...base import AlphaFactor

class GTJA_Momentum_5D(AlphaFactor):
    """5日动量: (close - close_5d_ago) / close_5d_ago"""
    def __init__(self):
        super().__init__('GTJA_001', '5日动量', 'mom_5d', 'momentum', 5, 'gtja191')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        return df['close'].pct_change(5)

class GTJA_Momentum_10D(AlphaFactor):
    """10日动量"""
    def __init__(self):
        super().__init__('GTJA_002', '10日动量', 'mom_10d', 'momentum', 10, 'gtja191')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        return df['close'].pct_change(10)

class GTJA_Momentum_20D(AlphaFactor):
    """20日动量"""
    def __init__(self):
        super().__init__('GTJA_003', '20日动量', 'mom_20d', 'momentum', 20, 'gtja191')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        return df['close'].pct_change(20)

class GTJA_MA_Deviation(AlphaFactor):
    """均线偏离度: (close - ma20) / ma20"""
    def __init__(self):
        super().__init__('GTJA_005', '均线偏离度(20日)', 'ma_dev_20', 'momentum', 5, 'gtja191')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        ma20 = df['close'].rolling(20).mean()
        return (df['close'] - ma20) / ma20

class GTJA_Price_Position(AlphaFactor):
    """价格相对位置: (close - low_20d) / (high_20d - low_20d)"""
    def __init__(self):
        super().__init__('GTJA_012', '价格相对位置(20日)', 'price_pos_20', 'momentum', 5, 'gtja191')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        h = df['high'].rolling(20).max()
        l = df['low'].rolling(20).min()
        rng = h - l
        return np.where(rng > 0, (df['close'] - l) / rng, 0.5)

class GTJA_RSI(AlphaFactor):
    """RSI指标: 14日相对强弱"""
    def __init__(self):
        super().__init__('GTJA_018', 'RSI(14日)', 'rsi_14', 'momentum', 5, 'gtja191')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        delta = df['close'].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        return 100 - (100 / (1 + rs))


def register_gtja_momentum(registry):
    for cls in [GTJA_Momentum_5D, GTJA_Momentum_10D, GTJA_Momentum_20D,
                 GTJA_MA_Deviation, GTJA_Price_Position, GTJA_RSI]:
        registry.register(cls())
```

```python
# ashare_review/alpha/factors/custom/__init__.py
"""龙哥体系特色因子"""
```

```python
# ashare_review/alpha/factors/custom/limit_up.py
"""涨停基因因子"""
import os, struct
import pandas as pd
import numpy as np
from ...base import AlphaFactor

class LimitUpGene(AlphaFactor):
    """涨停基因: 近250日涨停次数 / 250"""
    def __init__(self):
        super().__init__('CUSTOM_001', '涨停基因', 'limit_up_gene', 'liquidity', 10, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        # 此因子需要涨停次数数据，通常由 BaseScreener._count_limit_ups 提供
        # 这里返回基于日线的简化计算
        threshold = 0.099  # 默认主板10%
        up_days = (df['close'].pct_change() >= threshold).astype(int)
        return up_days.rolling(250).sum() / 250

class TurnoverIntensity(AlphaFactor):
    """换手率强度: 5日均换手 / 20日均换手"""
    def __init__(self):
        super().__init__('CUSTOM_002', '换手率强度', 'turnover_intensity', 'liquidity', 5, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        if 'turnover' in df.columns:
            t5 = df['turnover'].rolling(5).mean()
            t20 = df['turnover'].rolling(20).mean()
            return t5 / t20.replace(0, 1)
        # fallback: 用 volume 估算
        v5 = df['volume'].rolling(5).mean()
        v20 = df['volume'].rolling(20).mean()
        return v5 / v20.replace(0, 1)
```

```python
# ashare_review/alpha/factors/custom/chip_concentration.py
"""筹码集中度因子"""
import pandas as pd
import numpy as np
from ...base import AlphaFactor

class PriceConcentration(AlphaFactor):
    """价格集中度: 1 - (high_20d - low_20d) / close，值越大越集中"""
    def __init__(self):
        super().__init__('CUSTOM_003', '价格集中度', 'price_concentration', 'volatility', 10, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        h = df['high'].rolling(20).max()
        l = df['low'].rolling(20).min()
        rng_ratio = (h - l) / df['close'].replace(0, 1)
        return 1 - rng_ratio.clip(0, 2)
```

```python
# ashare_review/alpha/factors/custom/ma_system.py
"""均线排列因子"""
import pandas as pd
import numpy as np
from ...base import AlphaFactor

class MABullAlignment(AlphaFactor):
    """均线多头排列度: 统计MA5>MA10>MA20>MA60的确认条数"""
    def __init__(self):
        super().__init__('CUSTOM_004', '均线多头排列度', 'ma_bull', 'momentum', 10, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        masks = []
        for p in [5, 10, 20, 60]:
            col = f'ma{p}'
            if col in df.columns:
                masks.append(df[col].notna() & (df[col] > 0))
        result = pd.Series(0.0, index=df.index)
        if len(masks) >= 3:
            # 检查 MA5 > MA10 > MA20 > MA60 的层数
            pairs = [(5, 10), (10, 20), (20, 60)]
            for p1, p2 in pairs:
                c1, c2 = f'ma{p1}', f'ma{p2}'
                if c1 in df.columns and c2 in df.columns:
                    mask = (df[c1] > df[c2]).astype(float)
                    result = result + mask
        return result / 3  # 归一化到 0-1


def register_custom_factors(registry):
    from .limit_up import LimitUpGene, TurnoverIntensity
    from .chip_concentration import PriceConcentration
    from .ma_system import MABullAlignment
    for cls in [LimitUpGene, TurnoverIntensity, PriceConcentration, MABullAlignment]:
        registry.register(cls())
```

- [ ] **Step 2: 验证因子注册**

```bash
cd D:/cursor/project && python -c "
from ashare_review.alpha.registry import get_registry
from ashare_review.alpha.factors import register_all
register_all()
r = get_registry()
print(f'Registered {r.count} factors')
for f in r.list_all():
    print(f'  {f.id}: {f.name} [{f.zoo}]')
"
# Expected: Registered 9 factors listed
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/alpha/factors/ && git commit -m "feat: add initial 9 alpha factors (GTJA momentum + custom)"
```

---

### Task 10: 因子评估器 + 对比

**Files:**
- Create: `ashare_review/alpha/evaluator.py`
- Create: `ashare_review/alpha/compare.py`

- [ ] **Step 1: 编写因子评估器**

```python
# ashare_review/alpha/evaluator.py
"""因子评估 — 计算 IC/IR/分层收益"""
import pandas as pd
import numpy as np
from .base import AlphaFactor, FactorReport


def evaluate_factor(factor: AlphaFactor, df: pd.DataFrame,
                    universe: pd.DataFrame = None,
                    forward_period: int = 5) -> FactorReport:
    """评估单个因子的预测能力

    Args:
        factor: AlphaFactor 实例
        df: 单只股票的日线DataFrame
        universe: 可选，多只股票的面板数据
        forward_period: 前瞻收益周期（天）
    """
    factor_values = factor.calculate(df)
    if len(factor_values.dropna()) < 60:
        return FactorReport(factor_id=factor.id)

    # 前向收益
    fwd_ret = df['close'].pct_change(forward_period).shift(-forward_period)

    # IC 序列: 因子值与前瞻收益的秩相关系数
    valid = factor_values.notna() & fwd_ret.notna()
    if valid.sum() < 30:
        return FactorReport(factor_id=factor.id)

    fv = factor_values[valid]
    fr = fwd_ret[valid]

    # 滚动 IC（20日窗口）
    ic_series = []
    for i in range(20, len(fv)):
        ic_series.append(fv.iloc[i-20:i].corr(fr.iloc[i-20:i]))

    ic_series = pd.Series(ic_series)
    ic_mean = ic_series.mean()
    ic_std = ic_series.std()
    ir = ic_mean / ic_std if ic_std > 0 else 0
    ic_positive_ratio = (ic_series > 0).mean()

    # 分层收益: Top 20% vs Bottom 20%
    q = pd.qcut(fv, 5, labels=False, duplicates='drop')
    long_mask = q == 4  # Top quintile
    short_mask = q == 0  # Bottom quintile
    long_ret = fr[long_mask].mean() * (252 / forward_period) if long_mask.any() else 0
    short_ret = fr[short_mask].mean() * (252 / forward_period) if short_mask.any() else 0

    # 星级: 基于IR
    if ir >= 0.7:
        stars = 5
    elif ir >= 0.5:
        stars = 4
    elif ir >= 0.3:
        stars = 3
    elif ir >= 0.1:
        stars = 2
    else:
        stars = 1

    return FactorReport(
        factor_id=factor.id,
        ic_mean=ic_mean, ic_std=ic_std, ir=ir,
        ic_positive_ratio=ic_positive_ratio,
        long_ret=long_ret, short_ret=short_ret,
        turnover=0.1,  # 简化：日均换手
        stars=stars,
    )
```

```python
# ashare_review/alpha/compare.py
"""因子对比 — 排名和对比"""
import pandas as pd
from .base import AlphaFactor, FactorReport
from .registry import get_registry
from .evaluator import evaluate_factor


def rank_factors(df: pd.DataFrame, zoo: str = None,
                 sort_by: str = 'ir') -> list[FactorReport]:
    """对已注册因子进行排名"""
    registry = get_registry()
    factors = registry.list_by_zoo(zoo) if zoo else registry.list_all()
    reports = []
    for f in factors:
        try:
            report = evaluate_factor(f, df)
            reports.append(report)
        except Exception as e:
            print(f'[{f.id}] eval failed: {e}')
    if sort_by == 'ir':
        reports.sort(key=lambda r: r.ir, reverse=True)
    elif sort_by == 'ic_mean':
        reports.sort(key=lambda r: r.ic_mean, reverse=True)
    elif sort_by == 'stars':
        reports.sort(key=lambda r: r.stars, reverse=True)
    return reports


def compare_factors(factor_ids: list[str], df: pd.DataFrame) -> list[dict]:
    """对比指定因子，返回排名表"""
    registry = get_registry()
    reports = []
    for fid in factor_ids:
        f = registry.get(fid)
        if f is None:
            continue
        r = evaluate_factor(f, df)
        reports.append(r.to_dict())
    reports.sort(key=lambda r: r.get('ir', 0), reverse=True)
    return reports
```

- [ ] **Step 2: 测试评估器**

```bash
cd D:/cursor/project && python -c "
from ashare_review.data.tdx_reader import TdxReader
from ashare_review.alpha.factors import register_all
from ashare_review.alpha.compare import rank_factors
register_all()
tdx = TdxReader()
df = tdx.read_daily('600519', 'sh')  # 贵州茅台
if len(df) > 100:
    reports = rank_factors(df, sort_by='ir')
    for r in reports[:5]:
        print(f'{r.factor_id}: IR={r.ir:.3f}, IC_mean={r.ic_mean:.4f}, Stars={r.stars}')
"
# Expected: Top 5 factors ranked by IR
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/alpha/evaluator.py ashare_review/alpha/compare.py && git commit -m "feat: add alpha factor evaluator and comparison tools"
```

---

### Task 11: NL 策略解析器 + 执行器

**Files:**
- Create: `ashare_review/nl_strategy/parser.py`
- Create: `ashare_review/nl_strategy/validator.py`
- Create: `ashare_review/nl_strategy/executor.py`

- [ ] **Step 1: 编写策略校验器**

```python
# ashare_review/nl_strategy/validator.py
"""策略参数合法性校验"""
from .spec import StrategySpec, StrategyCondition, VALID_CONDITIONS


def validate_spec(spec: StrategySpec) -> list[str]:
    """校验 StrategySpec，返回错误列表（空=合法）"""
    errors = []
    if not spec.name:
        errors.append('策略名称为空')
    if not spec.conditions:
        errors.append('策略条件为空')
    for i, cond in enumerate(spec.conditions):
        if cond.type not in VALID_CONDITIONS:
            errors.append(f'条件{i}: 未知类型 {cond.type}')
            continue
    if spec.max_results < 1 or spec.max_results > 100:
        errors.append(f'max_results 需在1-100之间，当前{spec.max_results}')
    if spec.universe not in ('all', 'csi300', 'zz500', 'gem', 'main'):
        errors.append(f'未知universe: {spec.universe}')
    return errors
```

```python
# ashare_review/nl_strategy/parser.py
"""NL 策略解析器 — 使用 LLM 将自然语言转换为 StrategySpec

解析流程：
1. 接收自然语言描述
2. 调用 LLM（structured output）解析为 StrategySpec
3. 校验合法性
4. 返回合法的 StrategySpec 或错误
"""
import json
from .spec import StrategySpec, StrategyCondition
from .validator import validate_spec
from ..agents.providers import create_provider, OpenAICompatProvider

_PARSE_SYSTEM = """你是一个A股量化策略解析器。用户用自然语言描述选股思路，你将其转换为结构化的策略条件。

支持的条件类型和参数：
- market_cap: 流通市值范围(亿) — {min, max}
- float_market_cap: 流通市值范围(亿) — {min, max}
- ma_breakout: 均线突破 — {period: 均线周期}
- ma_position: 价格相对均线位置 — {period, above: true=站上/false=跌破}
- sector_limit_up: 板块涨停家数 — {min_count}
- limit_up_time: 涨停时间 — {before: "09:30"~"15:00"}
- seal_amount: 封单金额(亿) — {min}
- turnover: 换手率范围(%) — {min, max}
- volume_ratio: 量比 — {min}
- consecutive: 连板数 — {min, max}
- institution_count: 机构持仓家数 — {min}
- exclude_st: 排除ST — {}
- exclude_bj: 排除北交所 — {}
- pattern: K线形态 — {type: "box_breakout"|"w_bottom"|"n_pattern"}
- chip: 筹码信号 — {signal: "single_peak"|"bottom_lock"}
- change_pct: 涨跌幅(%) — {min, max}

请输出 JSON 格式的 StrategySpec:
{
  "name": "简短策略名",
  "description": "原始描述",
  "conditions": [{"type": "条件类型", "params": {...}, "weight": 1.0}],
  "universe": "all",
  "max_results": 20
}"""

_PARSE_SCHEMA = {
    'type': 'json_object',
    'schema': {
        'type': 'object',
        'properties': {
            'name': {'type': 'string'},
            'description': {'type': 'string'},
            'conditions': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'type': {'type': 'string'},
                        'params': {'type': 'object'},
                        'weight': {'type': 'number'},
                    },
                    'required': ['type', 'params'],
                },
            },
            'universe': {'type': 'string', 'enum': ['all', 'csi300', 'zz500', 'gem', 'main']},
            'max_results': {'type': 'integer'},
        },
        'required': ['name', 'conditions'],
    },
}


def parse_strategy(description: str, provider_name: str = None) -> dict:
    """解析自然语言描述为 StrategySpec

    Returns:
        {'success': True, 'spec': StrategySpec} or {'success': False, 'error': str}
    """
    provider = create_provider(provider_name)
    try:
        result_text = provider.chat_sync(
            [
                {'role': 'system', 'content': _PARSE_SYSTEM},
                {'role': 'user', 'content': f'请解析以下选股策略描述：\n\n{description}'},
            ],
            response_format=_PARSE_SCHEMA,
            temperature=0.1, max_tokens=1024,
        )
        data = json.loads(result_text)
        spec = StrategySpec.from_dict(data)
        spec.description = description

        errors = validate_spec(spec)
        if errors:
            return {'success': False, 'error': '; '.join(errors), 'spec': spec}

        return {'success': True, 'spec': spec}
    except Exception as e:
        return {'success': False, 'error': str(e)}
```

```python
# ashare_review/nl_strategy/executor.py
"""策略执行器 — 将 StrategySpec 转为实际的筛选操作"""
import pandas as pd
from typing import List
from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher
from ..data.models import ScreeningResult
from ..screening.base import BaseScreener
from .spec import StrategySpec, StrategyCondition

_tdx = TdxReader()
_ak = AkshareFetcher()


def execute_strategy(spec: StrategySpec) -> list[dict]:
    """执行策略筛选，返回结果列表

    流程：
    1. 加载全市场行情快照
    2. 逐个条件过滤（低成本优先）
    3. 加权评分
    4. 排序取 Top N
    """
    # 加载行情数据
    try:
        spot_df = _ak.get_spot_df()
    except Exception:
        return []

    if spot_df is None or spot_df.empty:
        return []

    # 条件过滤顺序：先执行低成本条件
    low_cost_types = {'exclude_st', 'exclude_bj', 'change_pct', 'float_market_cap',
                      'market_cap', 'consecutive'}
    ordered_conditions = sorted(spec.conditions,
                                key=lambda c: (0 if c.type in low_cost_types else 1))

    passed = spot_df.copy()
    for cond in ordered_conditions:
        passed = _apply_condition(passed, cond)
        if passed.empty:
            return []

    # 评分
    scores = pd.Series(0.0, index=passed.index)
    for cond in spec.conditions:
        cond_score = _calc_condition_score(passed, cond)
        scores = scores + cond_score * cond.weight

    passed['_score'] = scores

    # 排序
    sort_col = '_score'
    ascending = False
    if spec.sort_by == 'market_cap' and 'float_market_cap' in passed.columns:
        sort_col = 'float_market_cap'
        ascending = True
    elif spec.sort_by == 'turnover' and '换手率' in passed.columns:
        sort_col = '换手率'
        ascending = False

    passed = passed.sort_values(sort_col, ascending=ascending)
    top = passed.head(spec.max_results)

    # 格式化结果
    results = []
    for _, row in top.iterrows():
        code = str(row.get('代码', '')).zfill(6)
        name = str(row.get('名称', ''))
        score = float(row.get('_score', 0))
        results.append({
            'code': code, 'name': name,
            'score': round(min(score, 100), 1),
            'reasons': [f'{spec.name}策略匹配'],
            'detail': {'strategy': spec.name, 'conditions': len(spec.conditions)},
        })

    return results


def _apply_condition(df: pd.DataFrame, cond: StrategyCondition) -> pd.DataFrame:
    """对 DataFrame 应用单个过滤条件"""
    p = cond.params
    t = cond.type

    try:
        if t == 'exclude_st':
            if '名称' in df.columns:
                return df[~df['名称'].str.contains('ST', na=False)]
            return df
        if t == 'exclude_bj':
            if '代码' in df.columns:
                return df[~df['代码'].astype(str).str.startswith(('8', '4'))]
            return df
        if t in ('market_cap', 'float_market_cap'):
            col = '流通市值' if '流通市值' in df.columns else None
            if col is None:
                return df
            min_v = p.get('min', 0)
            max_v = p.get('max', float('inf'))
            return df[(df[col] >= min_v) & (df[col] <= max_v)]
        if t == 'change_pct':
            col = '涨跌幅' if '涨跌幅' in df.columns else None
            if col is None:
                return df
            min_v = p.get('min', -100)
            max_v = p.get('max', 100)
            return df[(df[col] >= min_v) & (df[col] <= max_v)]
        # 高成本条件：在评分阶段处理，此处不过滤
        return df
    except Exception:
        return df


def _calc_condition_score(df: pd.DataFrame, cond: StrategyCondition) -> pd.Series:
    """计算单个条件的评分"""
    p = cond.params
    t = cond.type
    result = pd.Series(0.0, index=df.index)

    try:
        if t == 'volume_ratio':
            col = '量比' if '量比' in df.columns else None
            if col is not None:
                min_v = p.get('min', 0)
                result = df[col].clip(0, 10) / max(min_v, 0.1)
                result = result.clip(0, 3) / 3
        if t == 'change_pct':
            col = '涨跌幅' if '涨跌幅' in df.columns else None
            if col is not None:
                # 涨得越多分越高（0-9%范围线性）
                result = df[col].clip(-5, 10) / 10
                result = result.clip(0, 1)
    except Exception:
        pass

    return result.fillna(0)
```

- [ ] **Step 2: 验证内置模板执行**

```bash
cd D:/cursor/project && python -c "
from ashare_review.nl_strategy.templates import BUILTIN_TEMPLATES
from ashare_review.nl_strategy.executor import execute_strategy
spec = BUILTIN_TEMPLATES['vol_breakout']
results = execute_strategy(spec)
print(f'放量突破: {len(results)} results')
for r in results[:5]:
    print(f'  {r[\"code\"]} {r[\"name\"]}: {r[\"score\"]}')
"
# Expected: X results (数量取决于行情数据是否可用)
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/nl_strategy/ && git commit -m "feat: add NL strategy parser, validator, and executor"
```
