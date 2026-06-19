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
