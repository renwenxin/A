"""涨停复制适配器"""
from datetime import date, timedelta
from typing import Dict, List, Optional

from .base import StrategyAdapter, normalize_v3_style_trades


class ZTReplicaAdapter(StrategyAdapter):
    strategy_id = 'zt_replica'
    name = '涨停复制'
    description = '近期涨停回调企稳后的二次启动（含双响炮模式）'
    param_schema = [
        {'name': 'lookback_days', 'label': '回看天数', 'type': 'int', 'default': 365, 'min': 60, 'max': 500,
         'help': '日历天数（脚本默认 365）'},
        {'name': 'only_double_cannon', 'label': '仅双响炮', 'type': 'bool', 'default': False},
    ]

    def normalize(self, result: dict) -> List[Dict]:
        return normalize_v3_style_trades((result or {}).get('trades', []))

    def run(self, params: Dict, tdx=None, ak=None) -> List[Dict]:
        from ...analysis.zt_replica_backtest import ZTReplicaBacktest
        lookback = int(params.get('lookback_days', 365))
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=lookback)
        bt = ZTReplicaBacktest(only_double_cannon=bool(params.get('only_double_cannon', False)))
        if tdx is not None:
            bt.tdx = tdx
        result = bt.run(start_date=start, end_date=end)
        return self.normalize(result)
