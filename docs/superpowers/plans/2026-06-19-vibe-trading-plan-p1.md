# Phase 1: 基础设施 (Tasks 1-6)

> Part of the Vibe-Trading integration plan. See spec at `docs/superpowers/specs/2026-06-19-vibe-trading-integration-design.md`

**Goal:** 搭建 config / agents / alpha / nl_strategy 四个新包的基础骨架

---

### Task 1: 配置层

**Files:**
- Create: `ashare_review/config/__init__.py`
- Create: `ashare_review/config/loader.py`
- Create: `ashare_review/config/defaults.py`

- [ ] **Step 1: 创建 config 包入口**

```python
# ashare_review/config/__init__.py
"""配置管理 — 功能开关 & LLM Provider. 自动从环境变量读取 API Key."""
from .loader import ConfigLoader

_config = None

def get_config() -> 'ConfigLoader':
    global _config
    if _config is None:
        _config = ConfigLoader()
    return _config
```

- [ ] **Step 2: 创建默认配置常量 + 配置加载器**

```python
# ashare_review/config/defaults.py
"""默认配置常量：不需要 YAML 文件也能运行，所有值有合理默认"""
from dataclasses import dataclass

@dataclass
class LLMProviderConfig:
    model: str
    api_key_env: str = ''
    base_url: str = ''
    timeout: int = 60

@dataclass
class FeatureConfig:
    agents: bool = True
    alpha: bool = True
    nl_strategy: bool = True

DEFAULT_PROVIDERS = {
    'deepseek': LLMProviderConfig(
        model='deepseek-chat',
        api_key_env='DEEPSEEK_API_KEY',
        base_url='https://api.deepseek.com/v1',
    ),
    'claude': LLMProviderConfig(
        model='claude-sonnet-4-6',
        api_key_env='ANTHROPIC_API_KEY',
    ),
}

AGENT_DEFAULTS = {
    'default_provider': 'deepseek',
    'temperature': 0.3,
    'max_tokens': 2048,
    'max_parallel': 5,
    'timeout_seconds': 120,
}
```

```python
# ashare_review/config/loader.py
"""配置加载器 — 无外部依赖，启动零失败"""
import os
from .defaults import (
    FeatureConfig, LLMProviderConfig,
    DEFAULT_PROVIDERS, AGENT_DEFAULTS,
)

class ConfigLoader:
    def __init__(self):
        self.features = FeatureConfig()
        self.providers: dict[str, LLMProviderConfig] = {}
        self._load()

    def _load(self):
        for name, cfg in DEFAULT_PROVIDERS.items():
            api_key = os.getenv(cfg.api_key_env, '')
            self.providers[name] = LLMProviderConfig(
                model=cfg.model,
                api_key_env=cfg.api_key_env,
                base_url=cfg.base_url,
                timeout=cfg.timeout,
                _api_key=api_key,  # cached resolved value
            )

    def get_api_key(self, provider: str) -> str:
        p = self.providers.get(provider)
        if p is None:
            return ''
        return os.getenv(p.api_key_env, '')

    def get_agent_defaults(self) -> dict:
        return dict(AGENT_DEFAULTS)

    @property
    def default_provider(self) -> str:
        return AGENT_DEFAULTS['default_provider']

    @property
    def default_model(self) -> str:
        return self.providers.get(self.default_provider, DEFAULT_PROVIDERS['deepseek']).model
```

- [ ] **Step 3: 验证配置加载**

```bash
cd D:/cursor/project && python -c "from ashare_review.config import get_config; c = get_config(); print(f'Provider: {c.default_provider}, Model: {c.default_model}')"
# Expected: Provider: deepseek, Model: deepseek-chat
```

- [ ] **Step 4: Commit**

```bash
cd D:/cursor/project && git add ashare_review/config/ && git commit -m "feat: add config layer for features and LLM providers"
```

---

### Task 2: Agent 数据模型 + Provider 抽象

**Files:**
- Create: `ashare_review/agents/__init__.py`
- Create: `ashare_review/agents/base.py`
- Create: `ashare_review/agents/providers.py`

- [ ] **Step 1: 创建数据模型**

```python
# ashare_review/agents/__init__.py
"""多智能体LLM分析 — 7个A股专属分析师 + Swarm调度"""
from .base import AgentOpinion, TradingPlan, BaseAgent
from .providers import LLMProvider, create_provider
```

```python
# ashare_review/agents/base.py
"""Agent 数据模型 + 基类"""
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import AsyncIterator

@dataclass
class AgentOpinion:
    agent: str          # 分析师名称
    direction: str      # 'bullish' | 'bearish' | 'neutral'
    confidence: float   # 0.0 ~ 1.0
    key_points: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    score: int = 0      # 0-100

@dataclass
class TradingPlan:
    symbol: str
    name: str = ''
    action: str = ''     # 'buy' | 'sell' | 'hold' | 'watch'
    entry_zone_low: float = 0.0
    entry_zone_high: float = 0.0
    stop_loss: float = 0.0
    targets: list[float] = field(default_factory=list)
    position_pct: float = 0.0
    rationale: str = ''  # Markdown
    agent_opinions: list[AgentOpinion] = field(default_factory=list)
    risk_level: str = 'medium'

    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol, 'name': self.name,
            'action': self.action,
            'entry_zone': [self.entry_zone_low, self.entry_zone_high],
            'stop_loss': self.stop_loss, 'targets': self.targets,
            'position_pct': self.position_pct,
            'rationale': self.rationale,
            'agent_opinions': [
                {'agent': o.agent, 'direction': o.direction,
                 'confidence': o.confidence, 'key_points': o.key_points,
                 'risks': o.risks, 'score': o.score}
                for o in self.agent_opinions
            ],
            'risk_level': self.risk_level,
        }


class BaseAgent(ABC):
    """Agent 基类：每个分析师继承此类，实现 system_prompt + analyze"""

    def __init__(self, provider: 'LLMProvider', tools: list[dict] = None):
        self.provider = provider
        self.tools = tools or []

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def system_prompt(self) -> str: ...

    async def analyze(self, context: str, output_schema: dict = None) -> str:
        """发送任务给LLM，支持structured output"""
        messages = [
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': context},
        ]
        kwargs = {}
        if output_schema:
            kwargs['response_format'] = {'type': 'json_object', 'schema': output_schema}
        if self.tools:
            kwargs['tools'] = self.tools
        return await self.provider.chat(messages, **kwargs)

    async def analyze_stream(self, context: str) -> AsyncIterator[str]:
        messages = [
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': context},
        ]
        async for chunk in self.provider.chat_stream(messages):
            yield chunk
```

- [ ] **Step 2: 创建 LLM Provider 抽象 + DeepSeek 实现**

```python
# ashare_review/agents/providers.py
"""LLM Provider 抽象层 — 支持 Claude / DeepSeek / OpenAI"""
import json, os, httpx
from abc import ABC, abstractmethod
from typing import AsyncIterator

class LLMProvider(ABC):
    def __init__(self, model: str, api_key: str, base_url: str = '', timeout: int = 60):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    @abstractmethod
    async def chat(self, messages: list[dict], **kwargs) -> str: ...

    @abstractmethod
    async def chat_stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]: ...

    @staticmethod
    def _build_headers(api_key: str) -> dict:
        return {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容 API（DeepSeek, OpenAI, 大部分国产模型都用这个格式）"""

    async def chat(self, messages: list[dict], **kwargs) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions" if self.base_url else \
              "https://api.deepseek.com/v1/chat/completions"
        body = {
            'model': self.model,
            'messages': messages,
            'temperature': kwargs.get('temperature', 0.3),
            'max_tokens': kwargs.get('max_tokens', 2048),
        }
        if 'response_format' in kwargs:
            body['response_format'] = kwargs['response_format']
        if 'tools' in kwargs:
            body['tools'] = kwargs['tools']

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=body,
                                     headers=self._build_headers(self.api_key))
            resp.raise_for_status()
            data = resp.json()
            return data['choices'][0]['message']['content']

    async def chat_stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        url = f"{self.base_url.rstrip('/')}/chat/completions" if self.base_url else \
              "https://api.deepseek.com/v1/chat/completions"
        body = {
            'model': self.model, 'messages': messages,
            'temperature': kwargs.get('temperature', 0.3),
            'max_tokens': kwargs.get('max_tokens', 2048),
            'stream': True,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream('POST', url, json=body,
                                      headers=self._build_headers(self.api_key)) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith('data: '):
                        data_str = line[6:]
                        if data_str == '[DONE]':
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data['choices'][0].get('delta', {})
                            content = delta.get('content', '')
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue

    def chat_sync(self, messages: list[dict], **kwargs) -> str:
        """同步包装器 — orchestrator 在线程池中使用"""
        import asyncio
        return asyncio.run(self.chat(messages, **kwargs))


from ..config import get_config

def create_provider(provider_name: str = None) -> LLMProvider:
    """工厂函数：从配置加载 Provider"""
    cfg = get_config()
    name = provider_name or cfg.default_provider
    defaults = cfg.get_agent_defaults()
    api_key = cfg.get_api_key(name)
    provider_cfg = cfg.providers.get(name)

    if provider_cfg is None:
        from ..config.defaults import DEFAULT_PROVIDERS
        provider_cfg = DEFAULT_PROVIDERS.get(name, DEFAULT_PROVIDERS['deepseek'])

    return OpenAICompatProvider(
        model=provider_cfg.model,
        api_key=api_key,
        base_url=provider_cfg.base_url,
        timeout=provider_cfg.timeout,
    )
```

- [ ] **Step 3: 验证 Provider**

```bash
cd D:/cursor/project && python -c "
import asyncio
from ashare_review.agents.providers import create_provider
p = create_provider('deepseek')
print(f'Model: {p.model}, Has key: {bool(p.api_key)}')
"
# Expected: Model: deepseek-chat, Has key: True/False (取决于是否设了环境变量)
```

- [ ] **Step 4: Commit**

```bash
cd D:/cursor/project && git add ashare_review/agents/ && git commit -m "feat: add agent data models and LLM provider abstraction"
```

---

### Task 3: Agent 工具函数封装

**Files:**
- Create: `ashare_review/agents/tools.py`

- [ ] **Step 1: 编写工具函数定义**

```python
# ashare_review/agents/tools.py
"""Agent 可调用的 function-calling 工具定义 + 执行器

将现有系统能力封装为 LLM 可调用的 JSON Schema 工具。
每个工具包含 name / description / parameters(JSON Schema) / execute 函数。
"""
import json
from typing import Any
from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher
from ..analysis.indicators import enrich_all
from ..analysis.pattern import detect_box_breakout, detect_w_bottom, detect_n_pattern
from ..analysis.volume import detect_volume_cannon, detect_volume_breakout
from ..analysis.chip import calc_chip_distribution, detect_chip_patterns

_tdx = TdxReader()
_ak = AkshareFetcher()

TOOL_DEFINITIONS = [
    {
        'type': 'function',
        'function': {
            'name': 'get_technical_indicators',
            'description': '计算股票全部技术指标：MA5/10/20/60/89/250, MACD, 量比, 振幅',
            'parameters': {
                'type': 'object',
                'properties': {
                    'code': {'type': 'string', 'description': '6位股票代码'},
                },
                'required': ['code'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'detect_patterns',
            'description': '检测K线形态：箱体突破、W底、N字结构',
            'parameters': {
                'type': 'object',
                'properties': {
                    'code': {'type': 'string', 'description': '6位股票代码'},
                },
                'required': ['code'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'analyze_chip',
            'description': '分析筹码分布：筹码峰形态、成本支撑/压力、获利盘占比',
            'parameters': {
                'type': 'object',
                'properties': {
                    'code': {'type': 'string', 'description': '6位股票代码'},
                },
                'required': ['code'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_limit_up_pool',
            'description': '获取今日涨停板完整列表(含涨停时间/封单/连板数)',
            'parameters': {
                'type': 'object',
                'properties': {},
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_sector_boards',
            'description': '获取今日概念/行业板块行情排名',
            'parameters': {
                'type': 'object',
                'properties': {},
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_lhb_top',
            'description': '获取今日龙虎榜Top 10净买入标的',
            'parameters': {
                'type': 'object',
                'properties': {},
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_institution_holding',
            'description': '获取最新季度机构(基金)持仓家数',
            'parameters': {
                'type': 'object',
                'properties': {},
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_market_breadth',
            'description': '获取全市场涨跌家数统计',
            'parameters': {
                'type': 'object',
                'properties': {},
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'read_daily_kline',
            'description': '读取个股完整日线数据(历史K线)',
            'parameters': {
                'type': 'object',
                'properties': {
                    'code': {'type': 'string', 'description': '6位股票代码'},
                },
                'required': ['code'],
            },
        },
    },
]


def _load_df(code: str):
    """加载个股日线 DataFrame"""
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith('8') or code.startswith('4'):
        market = 'bj'
    return _tdx.read_daily(code, market)


def execute_tool(name: str, arguments: dict) -> str:
    """执行工具函数，返回 JSON 字符串"""

    if name == 'get_technical_indicators':
        code = arguments['code']
        df = _load_df(code)
        if df.empty or len(df) < 10:
            return json.dumps({'error': f'{code} 数据不足'}, ensure_ascii=False)
        df = enrich_all(df)
        latest = df.iloc[-1]
        return json.dumps({
            'ma5': round(float(latest.get('ma5', 0)), 2),
            'ma10': round(float(latest.get('ma10', 0)), 2),
            'ma20': round(float(latest.get('ma20', 0)), 2),
            'ma60': round(float(latest.get('ma60', 0)), 2),
            'ma89': round(float(latest.get('ma89', 0)), 2),
            'ma250': round(float(latest.get('ma250', 0)), 2),
            'macd_dif': round(float(latest.get('macd_dif', 0)), 3),
            'macd_dea': round(float(latest.get('macd_dea', 0)), 3),
            'macd_bar': round(float(latest.get('macd_bar', 0)), 3),
            'volume_ratio': round(float(latest.get('volume_ratio', 1)), 1),
            'close': round(float(latest['close']), 2),
            'macd_golden': bool(latest.get('macd_dif', 0) > latest.get('macd_dea', 0)),
        }, ensure_ascii=False)

    if name == 'detect_patterns':
        code = arguments['code']
        df = _load_df(code)
        if df.empty or len(df) < 60:
            return json.dumps({'patterns': []})
        if 'ma5' not in df.columns:
            df = enrich_all(df)
        box = detect_box_breakout(df)
        w_bottom = detect_w_bottom(df)
        n_pattern = detect_n_pattern(df)
        patterns = []
        if box.get('detected'):
            patterns.append({'type': 'box_breakout', 'detail': box})
        if w_bottom.get('detected'):
            patterns.append({'type': 'w_bottom', 'detail': w_bottom})
        if n_pattern.get('detected'):
            patterns.append({'type': 'n_pattern', 'detail': n_pattern})
        return json.dumps({'patterns': patterns}, ensure_ascii=False)

    if name == 'analyze_chip':
        code = arguments['code']
        df = _load_df(code)
        if df.empty or len(df) < 60:
            return json.dumps({'error': '数据不足'}, ensure_ascii=False)
        chip = calc_chip_distribution(df)
        patterns = detect_chip_patterns(df)
        return json.dumps({
            'avg_cost': round(float(chip.get('avg_cost', 0)), 2),
            'profit_ratio': round(float(chip.get('profit_ratio', 0)), 1),
            'support': round(float(chip.get('support', 0)), 2),
            'pressure': round(float(chip.get('pressure', 0)), 2),
            'patterns': patterns,
            'interpretation': _chip_interpretation(patterns),
        }, ensure_ascii=False)

    if name == 'get_limit_up_pool':
        try:
            limit_ups = _ak.get_limit_up_pool()
            data = [{
                'code': lu.code, 'name': lu.name,
                'limit_up_time': lu.limit_up_time,
                'consecutive': lu.consecutive,
                'is_first': lu.is_first, 'is_seal': lu.is_seal,
                'board_type': lu.board_type,
                'turnover_yi': round(lu.turnover / 10000, 1),
                'seal_amount_yi': round(lu.seal_amount / 10000, 1),
            } for lu in limit_ups[:30]]
            return json.dumps({'count': len(limit_ups), 'top30': data}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({'error': str(e)}, ensure_ascii=False)

    if name == 'get_sector_boards':
        try:
            boards = _ak.get_concept_boards()
            data = [{'name': b.get('name', ''), 'change_pct': b.get('change_pct', 0)}
                    for b in boards[:10]]
            return json.dumps({'top10': data}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({'error': str(e)}, ensure_ascii=False)

    if name == 'get_lhb_top':
        try:
            lhb_list = _ak.get_lhb()
            data = [{
                'code': l.code, 'name': l.name,
                'net_amount_yi': round(l.net_amount / 10000, 1),
                'reason': l.reason,
            } for l in lhb_list[:10]]
            return json.dumps({'top10': data}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({'error': str(e)}, ensure_ascii=False)

    if name == 'get_institution_holding':
        try:
            holders = _ak.get_institution_holder_count()
            if holders is None or holders.empty:
                return json.dumps({'count': 0})
            return json.dumps({'count': len(holders), 'note': '当前季度基金持仓数据'}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({'error': str(e)}, ensure_ascii=False)

    if name == 'get_market_breadth':
        up_count, down_count = _tdx.get_market_breadth()
        return json.dumps({'up': up_count, 'down': down_count,
                           'ratio': f'{up_count}:{down_count}'}, ensure_ascii=False)

    if name == 'read_daily_kline':
        code = arguments['code']
        df = _load_df(code)
        if df.empty:
            return json.dumps({'error': f'{code} 无数据'}, ensure_ascii=False)
        recent = df.tail(20)
        bars = [{
            'date': str(row.get('trade_date', '')),
            'open': round(float(row['open']), 2),
            'high': round(float(row['high']), 2),
            'low': round(float(row['low']), 2),
            'close': round(float(row['close']), 2),
            'volume': int(row['volume']),
        } for _, row in recent.iterrows()]
        return json.dumps({'code': code, 'bars': bars}, ensure_ascii=False)

    return json.dumps({'error': f'Unknown tool: {name}'}, ensure_ascii=False)


def _chip_interpretation(patterns: list[dict]) -> str:
    """将筹码形态翻译为人类可读的解读"""
    if not patterns:
        return '无明确筹码信号'
    for p in patterns:
        signal = p.get('signal', '')
        name = p.get('name', '')
        if signal == 'buy':
            return f'✅ {name} — 买入信号，筹码集中度高'
        elif signal == 'sell':
            return f'⚠️ {name} — 卖出信号，筹码松动'
        elif signal == 'hold':
            return f'📌 {name} — 持有信号，底部锁仓良好'
        elif signal == 'watch':
            return f'👀 {name} — 观望信号，多峰套牢'
    return '信号不明'
```

- [ ] **Step 2: 验证工具函数**

```bash
cd D:/cursor/project && python -c "
from ashare_review.agents.tools import execute_tool
r = execute_tool('get_market_breadth', {})
print(r)
"
# Expected: {'up': ..., 'down': ..., 'ratio': '...'}  (取决于本地数据)
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/agents/tools.py && git commit -m "feat: add agent function-calling tool wrappers"
```

---

### Task 4: Alpha 因子基类 + 注册中心

**Files:**
- Create: `ashare_review/alpha/__init__.py`
- Create: `ashare_review/alpha/base.py`
- Create: `ashare_review/alpha/registry.py`

- [ ] **Step 1: 创建 AlphaFactor 基类**

```python
# ashare_review/alpha/__init__.py
"""Alpha 因子库 — A股精选量化因子"""
from .base import AlphaFactor
from .registry import FactorRegistry
```

```python
# ashare_review/alpha/base.py
"""Alpha 因子基类"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import pandas as pd


class AlphaFactor(ABC):
    """量化 Alpha 因子 — 输入日线 DataFrame，输出因子值 Series"""

    def __init__(self, id: str, name: str, name_en: str = '',
                 category: str = '', horizon: int = 5, zoo: str = 'custom'):
        self.id = id
        self.name = name
        self.name_en = name_en or id.lower()
        self.category = category       # 'momentum'|'reversal'|'volume'|'volatility'|'liquidity'
        self.horizon = horizon         # 预测周期（天）
        self.zoo = zoo                 # 'gtja191'|'alpha101'|'custom'

    @abstractmethod
    def calculate(self, df: pd.DataFrame) -> pd.Series:
        """输入日线DataFrame（至少含 open/high/low/close/volume），输出因子值序列"""
        ...

    def __repr__(self):
        return f'AlphaFactor({self.id}: {self.name})'


@dataclass
class FactorReport:
    factor_id: str
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ir: float = 0.0           # Information Ratio = ic_mean / ic_std
    ic_positive_ratio: float = 0.0
    long_ret: float = 0.0     # 多头年化收益
    short_ret: float = 0.0    # 空头年化收益
    turnover: float = 0.0     # 日均换手
    stars: int = 0            # 1-5

    def to_dict(self) -> dict:
        return {
            'factor_id': self.factor_id,
            'ic_mean': round(self.ic_mean, 4),
            'ic_std': round(self.ic_std, 4),
            'ir': round(self.ir, 3),
            'ic_positive_ratio': round(self.ic_positive_ratio, 3),
            'long_ret': round(self.long_ret, 4),
            'short_ret': round(self.short_ret, 4),
            'turnover': round(self.turnover, 4),
            'stars': self.stars,
        }
```

```python
# ashare_review/alpha/registry.py
"""因子注册中心 — 统一管理所有 Alpha 因子"""
import pandas as pd
from .base import AlphaFactor


class FactorRegistry:
    """因子注册中心：注册、查询、批量计算"""

    def __init__(self):
        self._factors: dict[str, AlphaFactor] = {}

    def register(self, factor: AlphaFactor):
        """注册因子（同名覆盖）"""
        self._factors[factor.id] = factor
        return self

    def register_many(self, factors: list[AlphaFactor]):
        for f in factors:
            self.register(f)
        return self

    def get(self, id: str) -> AlphaFactor | None:
        return self._factors.get(id)

    def list_all(self) -> list[AlphaFactor]:
        return list(self._factors.values())

    def list_by_zoo(self, zoo: str) -> list[AlphaFactor]:
        return [f for f in self._factors.values() if f.zoo == zoo]

    def list_by_category(self, category: str) -> list[AlphaFactor]:
        return [f for f in self._factors.values() if f.category == category]

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """对所有已注册因子在给定 DataFrame 上计算，返回因子值矩阵"""
        result = pd.DataFrame(index=df.index)
        for f in self._factors.values():
            try:
                result[f.id] = f.calculate(df)
            except Exception:
                result[f.id] = 0.0
        return result

    @property
    def count(self) -> int:
        return len(self._factors)

    def summary(self) -> list[dict]:
        """返回所有因子元数据摘要"""
        return [{
            'id': f.id, 'name': f.name, 'category': f.category,
            'zoo': f.zoo, 'horizon': f.horizon,
        } for f in self._factors.values()]


# 全局单例
_registry = FactorRegistry()

def get_registry() -> FactorRegistry:
    return _registry
```

- [ ] **Step 2: 验证注册中心**

```bash
cd D:/cursor/project && python -c "
from ashare_review.alpha import FactorRegistry
r = FactorRegistry()
print(f'Registry with {r.count} factors')
print(r.summary())
"
# Expected: Registry with 0 factors, []
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/alpha/ && git commit -m "feat: add alpha factor base class and registry"
```

---

### Task 5: 自然语言策略 — 数据模型 + 模板

**Files:**
- Create: `ashare_review/nl_strategy/__init__.py`
- Create: `ashare_review/nl_strategy/spec.py`
- Create: `ashare_review/nl_strategy/templates.py`

- [ ] **Step 1: 创建 StrategySpec 数据模型**

```python
# ashare_review/nl_strategy/__init__.py
"""自然语言策略 — NL描述 → 可执行筛选"""
from .spec import StrategyCondition, StrategySpec
from .templates import BUILTIN_TEMPLATES
```

```python
# ashare_review/nl_strategy/spec.py
"""策略数据模型"""
from dataclasses import dataclass, field

# 支持的条件类型及其参数 schema
VALID_CONDITIONS = {
    'market_cap': {'min': float, 'max': float},
    'ma_breakout': {'period': int},
    'ma_position': {'period': int, 'above': bool},
    'sector_limit_up': {'min_count': int},
    'limit_up_time': {'before': str},
    'seal_amount': {'min': float},
    'turnover': {'min': float, 'max': float},
    'volume_ratio': {'min': float},
    'consecutive': {'min': int, 'max': int},
    'institution_count': {'min': int},
    'float_market_cap': {'min': float, 'max': float},
    'exclude_st': {},
    'exclude_bj': {},
    'pattern': {'type': str},
    'chip': {'signal': str},
    'change_pct': {'min': float, 'max': float},
}


@dataclass
class StrategyCondition:
    type: str
    params: dict = field(default_factory=dict)
    weight: float = 1.0


@dataclass
class StrategySpec:
    name: str = ''
    description: str = ''
    conditions: list[StrategyCondition] = field(default_factory=list)
    universe: str = 'all'
    max_results: int = 20
    sort_by: str = 'score'

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'description': self.description,
            'conditions': [{'type': c.type, 'params': c.params, 'weight': c.weight}
                          for c in self.conditions],
            'universe': self.universe,
            'max_results': self.max_results,
            'sort_by': self.sort_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'StrategySpec':
        conditions = [StrategyCondition(
            type=c['type'], params=c.get('params', {}),
            weight=c.get('weight', 1.0),
        ) for c in d.get('conditions', [])]
        return cls(
            name=d.get('name', ''), description=d.get('description', ''),
            conditions=conditions, universe=d.get('universe', 'all'),
            max_results=d.get('max_results', 20), sort_by=d.get('sort_by', 'score'),
        )
```

```python
# ashare_review/nl_strategy/templates.py
"""内置策略模板 — 无需 LLM 即可使用的预定义策略"""
from .spec import StrategySpec, StrategyCondition

BUILTIN_TEMPLATES: dict[str, StrategySpec] = {
    'vol_breakout': StrategySpec(
        name='放量突破',
        description='放量突破20日高点，均线多头排列，趋势启动信号',
        conditions=[
            StrategyCondition('ma_breakout', {'period': 20}),
            StrategyCondition('volume_ratio', {'min': 1.5}),
            StrategyCondition('float_market_cap', {'min': 20, 'max': 500}),
            StrategyCondition('exclude_st', {}),
        ],
        universe='all', max_results=20,
    ),
    'shrink_pullback': StrategySpec(
        name='缩量回调',
        description='连续缩量回调，价格守住MA10，低吸买点',
        conditions=[
            StrategyCondition('ma_position', {'period': 10, 'above': True}),
            StrategyCondition('turnover', {'min': 0.5, 'max': 3.0}),
            StrategyCondition('change_pct', {'min': -5, 'max': 2}),
            StrategyCondition('exclude_st', {}),
        ],
        universe='all', max_results=20,
    ),
    'auction_surge': StrategySpec(
        name='竞价异动',
        description='高开3%以上，竞价量超过前日3倍，抢筹迹象明显',
        conditions=[
            StrategyCondition('change_pct', {'min': 3, 'max': 9}),
            StrategyCondition('volume_ratio', {'min': 3}),
            StrategyCondition('float_market_cap', {'min': 10, 'max': 200}),
            StrategyCondition('exclude_st', {}),
        ],
        universe='all', max_results=15,
    ),
    'sector_leader': StrategySpec(
        name='板块共振',
        description='所属板块涨停≥3家，个股放量领涨',
        conditions=[
            StrategyCondition('sector_limit_up', {'min_count': 3}),
            StrategyCondition('volume_ratio', {'min': 1.5}),
            StrategyCondition('change_pct', {'min': 2}),
            StrategyCondition('exclude_st', {}),
        ],
        universe='all', max_results=15,
    ),
    'inst_quality': StrategySpec(
        name='机构加持',
        description='机构持仓≥100家，流通市值适中，中线布局',
        conditions=[
            StrategyCondition('institution_count', {'min': 100}),
            StrategyCondition('market_cap', {'min': 100, 'max': 2000}),
            StrategyCondition('exclude_st', {}),
            StrategyCondition('exclude_bj', {}),
        ],
        universe='all', max_results=30,
    ),
}
```

- [ ] **Step 2: 验证模板**

```bash
cd D:/cursor/project && python -c "
from ashare_review.nl_strategy.templates import BUILTIN_TEMPLATES
for k, v in BUILTIN_TEMPLATES.items():
    print(f'{k}: {v.name} ({len(v.conditions)} conditions)')
"
# Expected: 5 templates listed
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/nl_strategy/ && git commit -m "feat: add NL strategy data models and built-in templates"
```

---

### Task 6: Flask SSE 基础设施

**Files:**
- Modify: `ashare_review/web/app.py` — 在文件末尾添加 SSE 辅助函数

- [ ] **Step 1: 在 app.py 添加 SSE 基础设施**

In `ashare_review/web/app.py`, after the existing imports, add:

```python
# ---- SSE Streaming Support (added for Vibe-Trading integration) ----
import queue, threading, uuid, time, json
from flask import Response, stream_with_context

# 简单的内存任务队列（生产环境可换 Redis）
_task_queues: dict[str, queue.Queue] = {}
_task_results: dict[str, dict] = {}

def _create_task() -> str:
    task_id = uuid.uuid4().hex[:12]
    _task_queues[task_id] = queue.Queue()
    _task_results[task_id] = {'status': 'pending', 'events': []}
    return task_id

def _emit_event(task_id: str, event_type: str, data: dict):
    msg = json.dumps({'type': event_type, 'data': data}, ensure_ascii=False)
    if task_id in _task_queues:
        _task_queues[task_id].put(msg)
    if task_id in _task_results:
        _task_results[task_id]['events'].append({'type': event_type, 'data': data})

def _complete_task(task_id: str, final_data: dict = None):
    if task_id in _task_results:
        _task_results[task_id]['status'] = 'done'
        if final_data:
            _task_results[task_id]['final'] = final_data
    _emit_event(task_id, 'done', final_data or {})

def _fail_task(task_id: str, error: str):
    if task_id in _task_results:
        _task_results[task_id]['status'] = 'error'
    _emit_event(task_id, 'error', {'message': error})


def _sse_stream(task_id: str):
    """SSE 生成器 — 从队列读取事件并推送到客户端"""
    def generate():
        q = _task_queues.get(task_id)
        if not q:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': 'Task not found'}})}\\\\n\\\\n"
            return
        timeout = 300  # 5分钟总超时
        start = time.time()
        while time.time() - start < timeout:
            try:
                msg = q.get(timeout=5)
                yield f"data: {msg}\\\\n\\\\n"
                if '"type": "done"' in msg or '"type": "error"' in msg:
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\\\\n\\\\n"
        # 清理
        _task_queues.pop(task_id, None)
    return Response(stream_with_context(generate()), mimetype='text/event-stream')
```

- [ ] **Step 2: 验证 SSE 端点**

```bash
cd D:/cursor/project && python -c "
from ashare_review.web.app import _create_task, _emit_event, _complete_task
tid = _create_task()
_emit_event(tid, 'test', {'msg': 'hello'})
_complete_task(tid, {'ok': True})
print(f'Task {tid}: {len(_task_results[tid][chr(101)+chr(118)+chr(101)+chr(110)+chr(116)+chr(115)])} events')
"
# Expected: Task ...: 2 events
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/web/app.py && git commit -m "feat: add Flask SSE streaming infrastructure for agent chat"
```
