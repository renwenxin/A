"""Swarm 调度器 — 并行执行→辩论→综合"""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from .base import AgentOpinion, TradingPlan
from .analysts import (
    create_agent,
    TechnicalAnalyst,
    FundamentalAnalyst,
    SentimentAnalyst,
    SectorAnalyst,
    FlowAnalyst,
    RiskManager,
    LeadAnalyst,
)
from .providers import create_provider
from ..config import get_config
from ..utils.log import get_logger

logger = get_logger(__name__)

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

    def _make_provider(self):
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
        analysis_context = f"分析标的: {name}({symbol})\n"
        if context:
            analysis_context += f"已知信息: {context}\n"

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

        prompt = f"请对以下标的进行分析:\n\n{context}"
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
        opinions_text = '\n'.join([
            f"- {o.agent}: {o.direction}(信心{o.confidence:.0%}), "
            f"要点: {'; '.join(o.key_points[:3])}, 风险: {'; '.join(o.risks[:2])}"
            for o in opinions if o
        ])
        prompt = f"{context}\n\n各分析师意见:\n{opinions_text}\n\n请做风控评估。"
        try:
            result = await agent.analyze(prompt, output_schema=_OPINION_SCHEMA['schema'])
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
        opinions_text = '\n'.join([
            f"### {o.agent}\n- 方向: {o.direction}\n"
            f"- 信心: {o.confidence:.0%}\n"
            f"- 要点: {'; '.join(o.key_points)}\n"
            f"- 风险: {'; '.join(o.risks)}\n- 评分: {o.score}"
            for o in all_opinions if o
        ])
        prompt = f"标的: {name}({symbol})\n{context}\n\n各分析师意见:\n{opinions_text}\n\n请综合给出交易计划。"
        try:
            result = await agent.analyze(prompt, output_schema=_TRADING_PLAN_SCHEMA['schema'])
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
