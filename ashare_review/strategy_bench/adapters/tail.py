"""尾盘战法适配器（复刻 main() 全市场扫描流程）"""
from typing import Dict, List, Optional

from .base import StrategyAdapter, normalize_tail_signals


class TailAdapter(StrategyAdapter):
    strategy_id = 'tail'
    name = '尾盘战法'
    description = '尾盘选股（超跌反弹 + 平台突破），次日开盘卖出'
    param_schema = [
        {'name': 'days', 'label': '回测交易日数', 'type': 'int', 'default': 250, 'min': 30, 'max': 500},
        {'name': 'limit', 'label': '扫描股票数(0=全部)', 'type': 'int', 'default': 0, 'min': 0, 'max': 10000,
         'help': '调试用：限制扫描只数，0 表示全市场'},
    ]

    def run(self, params: Dict, tdx=None, ak=None) -> List[Dict]:
        import pandas as pd
        from ...analysis import tail_session_backtest as tail
        from ...data.tdx_reader import TdxReader
        from ...utils.calendar import TradingCalendar
        tdx = tdx or TdxReader()
        cal = TradingCalendar()
        days = int(params.get('days', 250))
        limit = int(params.get('limit', 0))
        sig_params = {
            'drawdown_window': 60, 'drawdown_min': 25.0, 'daily_gain_min': 4.0,
            'box_window': 60, 'vol_ratio_min': 1.5, 'platform_width_max': 18.0,
        }
        stocks = [(c, m) for c, m in tdx.list_stocks() if m != 'bj' and tail.is_a_stock(c)]
        if limit > 0:
            stocks = stocks[:limit]
        all_signals = []
        for code, market in stocks:
            try:
                df = tdx.read_daily(code, market)
            except Exception:
                continue
            if df.empty or len(df) < 300:
                continue
            df = df.tail(days + 300).reset_index(drop=True)
            sig = tail.compute_signals(df, code, sig_params)
            if sig is not None and not sig.empty:
                cutoff = df['trade_date'].iloc[-days]
                sig = sig[pd.to_datetime(sig['trade_date']) >= pd.to_datetime(cutoff)]
                if not sig.empty:
                    all_signals.append(sig)
        if not all_signals:
            return []
        S = pd.concat(all_signals, ignore_index=True)
        return normalize_tail_signals(S, 'open_ret', cal)
