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
