"""启动突破 V3 适配器"""
from datetime import date, timedelta
from typing import Dict, List, Optional

from .base import StrategyAdapter, normalize_v3_style_trades


class V3Adapter(StrategyAdapter):
    strategy_id = 'v3'
    name = '启动突破V3'
    description = 'MAVOL180 放量突破 + 压力位突破（含资金/仓位模型）'
    param_schema = [
        {'name': 'lookback_days', 'label': '回看天数', 'type': 'int', 'default': 365, 'min': 20, 'max': 500,
         'help': '日历天数（脚本默认 365）'},
        {'name': 'max_positions', 'label': '最大持仓数', 'type': 'int', 'default': 10, 'min': 1, 'max': 20},
    ]

    def normalize(self, result: dict) -> List[Dict]:
        return normalize_v3_style_trades((result or {}).get('trades', []))

    def run(self, params: Dict, tdx=None, ak=None) -> List[Dict]:
        from ...analysis.v3_backtest import V3Backtest
        lookback = int(params.get('lookback_days', 250))
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=lookback)
        bt = V3Backtest()
        if tdx is not None:
            bt.tdx = tdx
        result = bt.run(start_date=start, end_date=end,
                        max_positions=int(params.get('max_positions', 10)))
        return self.normalize(result)
