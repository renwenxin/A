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
