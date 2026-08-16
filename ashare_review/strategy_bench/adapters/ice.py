"""冰点抄底适配器（需要 market_state 构建 state_df）"""
from datetime import date, timedelta
from typing import Dict, List, Optional

from .base import StrategyAdapter, normalize_v3_style_trades


class IceAdapter(StrategyAdapter):
    strategy_id = 'ice'
    name = '冰点抄底'
    description = '冰点反转确认日收盘买入（缠论二买 + 超跌反弹）'
    param_schema = [
        {'name': 'lookback_days', 'label': '回看天数', 'type': 'int', 'default': 250, 'min': 60, 'max': 500,
         'help': '日历天数'},
    ]

    def normalize(self, raw_trades: List[Dict]) -> List[Dict]:
        return normalize_v3_style_trades(raw_trades)

    def run(self, params: Dict, tdx=None, ak=None) -> List[Dict]:
        from ...analysis.strategy_regime.ice_backtest import IceBottomBacktest
        from ...analysis.strategy_regime import market_state
        from ...data.tdx_reader import TdxReader
        tdx = tdx or TdxReader()
        lookback = int(params.get('lookback_days', 250))
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=lookback)
        state_df = market_state.load_state(start, end)   # load_state 内部会补 emotion/regime 列
        if state_df is None or state_df.empty:
            return []
        bt = IceBottomBacktest(tdx=tdx)
        raw = bt.run(state_df, start, end)
        return self.normalize(raw or [])
