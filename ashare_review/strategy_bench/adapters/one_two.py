"""1进2 接力适配器"""
from typing import Dict, List, Optional

from .base import StrategyAdapter, normalize_one_two_trades


class OneTwoAdapter(StrategyAdapter):
    strategy_id = 'one_two'
    name = '1进2接力'
    description = '首板次日接力（双数据源：akshare 优先 + TDX 回退）'
    param_schema = [
        {'name': 'lookback_days', 'label': '回看天数', 'type': 'int', 'default': 60, 'min': 10, 'max': 250},
        {'name': 'top_n', 'label': '每日入选前N', 'type': 'int', 'default': 5, 'min': 1, 'max': 20},
        {'name': 'min_score', 'label': '最低评分', 'type': 'int', 'default': 0, 'min': 0, 'max': 100,
         'help': '0 使用脚本内置默认(40)'},
    ]

    def normalize(self, result: dict) -> List[Dict]:
        return normalize_one_two_trades((result or {}).get('valid_trades', []))

    def run(self, params: Dict, tdx=None, ak=None) -> List[Dict]:
        from ...analysis.one_two_backtest import OneTwoBacktest
        bt = OneTwoBacktest()
        if tdx is not None:
            bt.tdx = tdx
        if ak is not None:
            bt.ak = ak
        result = bt.run(
            lookback_days=int(params.get('lookback_days', 60)),
            top_n=int(params.get('top_n', 5)),
            min_score=int(params.get('min_score', 0)),
        )
        return self.normalize(result)
