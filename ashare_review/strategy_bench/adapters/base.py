"""策略适配器基类 + 归一化辅助（不改动现有回测脚本）"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from ...utils.calendar import TradingCalendar


def normalize_v3_style_trades(raw_trades: List[Dict]) -> List[Dict]:
    """v3/zt_replica/ice 共用：buy_date/sell_date('%Y-%m-%d') + net_ret(%) → 统一 schema"""
    trades = []
    for t in raw_trades or []:
        entry = str(t.get('buy_date', '')).replace('-', '')
        exit_ = str(t.get('sell_date', '')).replace('-', '')
        if not entry or not exit_:
            continue
        trades.append({'entry_date': entry, 'exit_date': exit_,
                       'return_pct': float(t.get('net_ret', 0.0))})
    return trades


def normalize_one_two_trades(raw_trades: List[Dict]) -> List[Dict]:
    """one_two：entry_date/exit_date + return_pct"""
    trades = []
    for t in raw_trades or []:
        entry = str(t.get('entry_date', '')).replace('-', '')
        exit_ = str(t.get('exit_date', '')).replace('-', '')
        if not entry:
            continue
        trades.append({'entry_date': entry, 'exit_date': exit_ or entry,
                       'return_pct': float(t.get('return_pct', 0.0))})
    return trades


def normalize_tail_signals(sig_df, ret_col: str,
                           calendar: TradingCalendar) -> List[Dict]:
    """尾盘：信号行 → trade_date 入场，ret_col(%) 为收益，exit=下一交易日"""
    if sig_df is None or sig_df.empty:
        return []
    trades = []
    for _, row in sig_df.iterrows():
        td = str(row.get('trade_date', ''))[:10].replace('-', '')
        ret = row.get(ret_col)
        if not td or ret is None or str(ret) == 'nan':
            continue
        try:
            from datetime import datetime
            d = datetime.strptime(td, '%Y%m%d').date()
            nxt = calendar.next_trading_day(d, offset=1)
        except (ValueError, TypeError):
            continue
        if nxt is None:
            continue
        trades.append({'entry_date': td, 'exit_date': nxt.strftime('%Y%m%d'),
                       'return_pct': float(ret)})
    return trades


class StrategyAdapter(ABC):
    strategy_id: str = ''
    name: str = ''
    description: str = ''
    param_schema: List[Dict] = []   # [{name,label,type,default,min,max,help}]

    @abstractmethod
    def run(self, params: Dict, tdx=None, ak=None) -> List[Dict]:
        """返回归一化逐笔交易 [{entry_date, exit_date, return_pct}]"""
