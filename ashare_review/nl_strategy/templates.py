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
