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
        breadth = _tdx.get_market_breadth()
        return json.dumps({'up': breadth['up_count'], 'down': breadth['down_count'],
                           'ratio': f"{breadth['up_count']}:{breadth['down_count']}"}, ensure_ascii=False)

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
