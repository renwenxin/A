"""冰点抄底 — 集中持仓 + 每日轮换（资金模型版）

原 ice_backtest 是"每个反转日买入 TopN 超跌股、各自独立持有"。
这里加组合层：N 仓 + 每日轮换——满仓后若新反转日出现更强的超跌候选
（超跌更深的强势股），轮出最弱持仓、换入新标的。

买卖规则沿用 ice_backtest：
  - 市场: 冰点 + 缠论反转确认 + 二次确认（find_reversal_days）
  - 个股: 前期强势(年涨停≥10)超跌≥30% + 缩量企稳 + 站上MA5/底分型，股价≤40元
  - 买入: 反转确认日收盘价（缠论二买）
  - 卖出: 反弹+8%/前20日高点止盈 · 移动止盈(最高回落5%) · 止损-6% · 最长10天
"""
import os
import json
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd

from ...data.tdx_reader import TdxReader
from ...utils.calendar import TradingCalendar
from .ice_backtest import IceBottomBacktest, find_reversal_days, TOTAL_COST, MAX_HOLD, STOP, TP, TRAIL

NAME_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'stock_name_map.json')


class IcePortfolio:
    """冰点抄底 集中持仓 + 每日轮换"""

    def __init__(self, tdx: TdxReader = None, max_positions: int = 3,
                 position_pct: float = None, rotation: bool = True):
        self.tdx = tdx or TdxReader()
        self.cal = TradingCalendar()
        self.max_positions = max_positions
        self.position_pct = position_pct or (1.0 / max_positions)
        self.rotation = rotation
        self._base = IceBottomBacktest(tdx)
        self._stock_cache: Dict[str, pd.DataFrame] = {}

    def _read(self, code):
        if code not in self._stock_cache:
            try:
                df = self.tdx.read_daily(code, self._base._market(code))
                if df is not None and not df.empty:
                    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
                    df = df.set_index('trade_date')
                    df['ma5'] = df['close'].rolling(5).mean()
                    df['high120'] = df['high'].rolling(120).max()
                    from . import chan
                    df['_frac'] = chan.detect_fractals(df.reset_index())[:].values
                    self._stock_cache[code] = df
            except Exception:
                self._stock_cache[code] = None
        return self._stock_cache.get(code)

    def _oversold(self, d: date, top_n: int) -> List[Dict]:
        """反转确认日 D 的超跌候选（复用 ice_backtest 逻辑，返回前 top_n）"""
        # 简版: 直接用 base 的候选，但 base 返回 top5；这里按超跌排序截断
        cands = self._base._oversold_candidates(d)
        return cands[:top_n]

    # ── 持仓日度卖出检查（沿用冰点规则；当日按对应价格卖出） ──
    def _check_exit(self, code, h, td):
        df = self._read(code)
        if df is None or td not in df.index:
            return None
        pos = df.index.get_loc(td)
        if pos < 1:
            return None
        bar = df.iloc[pos]
        o, hi, lo, c = float(bar['open']), float(bar['high']), float(bar['low']), float(bar['close'])
        entry = h['buy_price']
        days = h.get('days', 1)
        # 止损 -6%
        if lo <= entry * (1 - STOP):
            return {'reason': '止损-6%', 'days': days, 'sell_price': min(entry * (1 - STOP), o)}
        # 反弹到前20日高点
        high20 = float(df['high'].iloc[max(0, pos - 20):pos + 1].max())
        if high20 > entry * 1.02 and c >= high20 * 0.995:
            return {'reason': '反弹到压力位', 'days': days, 'sell_price': c}
        # 反弹 ≥ +8%
        if c >= entry * (1 + TP):
            return {'reason': '反弹+8%止盈', 'days': days, 'sell_price': c}
        # 移动止盈
        highest = max(h.get('highest', entry), c)
        h['highest'] = highest
        if highest >= entry * 1.03 and c <= highest * (1 - TRAIL):
            return {'reason': '移动止盈', 'days': days, 'sell_price': c}
        # 最长 10 天
        if days >= MAX_HOLD:
            return {'reason': '10天到期', 'days': days, 'sell_price': c}
        return None

    def run(self, state_df, start: date, end: date) -> Dict:
        rev_idx = find_reversal_days(state_df)
        if not rev_idx:
            return {'trades': [], 'cumulative_return': 0, 'max_drawdown': 0, 'portfolio_values': []}
        # 候选池预读
        universe = sorted(c for c in self._base.get_universe()
                          if str(c).startswith(('60', '00')) and 'ST' not in self._base._get_name(c))
        self._base._load_cache(universe)
        self._stock_cache = self._base._stock_cache

        trading_days = [r['date'] for _, r in state_df.iterrows()]
        if isinstance(trading_days[0], str):
            trading_days = [date.fromisoformat(d) for d in trading_days]
        td_set = set(trading_days)

        INITIAL = 1_000_000.0
        cash = INITIAL
        holdings: Dict[str, dict] = {}
        trades = []
        portfolio_values = []
        peak = INITIAL

        for td in trading_days:
            if td < start or td > end:
                continue
            # 1) 持仓卖出检查（当日执行）
            for code in list(holdings.keys()):
                h = holdings[code]
                h['days'] = h.get('days', 0) + 1
                ex = self._check_exit(code, h, td)
                if ex:
                    sell_price = ex['sell_price']
                    cash += h['shares'] * sell_price * (1 - 0.0008)
                    gross = (sell_price - h['buy_price']) / h['buy_price']
                    trades.append({
                        'signal_date': h['signal_date'].strftime('%Y-%m-%d'),
                        'buy_date': h['buy_date'].strftime('%Y-%m-%d'),
                        'sell_date': td.strftime('%Y-%m-%d'),
                        'code': code, 'name': h.get('name', code),
                        'buy_price': round(h['buy_price'], 2), 'sell_price': round(sell_price, 2),
                        'net_ret': round((gross - TOTAL_COST) * 100, 2),
                        'is_win': (gross - TOTAL_COST) > 0,
                        'exit_reason': ex['reason'], 'days_held': ex['days'],
                        'drop': h.get('drop', 0),
                    })
                    del holdings[code]

            # 2) 若是反转确认日 → 买超跌候选（填仓 + 轮换）
            if td in td_set and (td.strftime('%Y-%m-%d') in {str(r['date']) for _, r in state_df.iterrows()}):
                d = td
                # 判断是否反转日: 用 signal 近似（该日是否在 rev_idx 里）
                is_rev = False
                for i in rev_idx:
                    dd = state_df.iloc[i]['date']
                    if isinstance(dd, str):
                        dd = date.fromisoformat(dd)
                    if dd == d:
                        is_rev = True
                        break
                if is_rev:
                    cands = self._oversold(d, top_n=self.max_positions)
                    cands.sort(key=lambda x: -x['drop'])
                    available_slots = max(0, self.max_positions - len(holdings))
                    if available_slots == 0 and self.rotation and cands:
                        # 轮换: 新反转日的更强超跌候选 > 最弱持仓
                        weakest, weakest_strength = None, 1e18
                        for _code, _h in holdings.items():
                            df = self._read(_code)
                            cur = float(df.loc[td, 'close']) if (df is not None and td in df.index) else _h['buy_price']
                            ret = cur / _h['buy_price'] - 1 if _h['buy_price'] > 0 else 0
                            s = _h.get('drop', 0) * 10 + ret   # 强度 = 超跌 + 当前收益
                            if s < weakest_strength:
                                weakest, weakest_strength = _code, s
                        top = cands[0]
                        if weakest and top['drop'] > _h.get('drop', 0):
                            hw = holdings.pop(weakest)
                            _df = self._read(weakest)
                            wpx = float(_df.loc[td, 'close']) if (_df is not None and td in _df.index) else hw['buy_price']
                            cash += hw['shares'] * wpx * (1 - 0.0008)
                            gross = (wpx - hw['buy_price']) / hw['buy_price']
                            trades.append({
                                'signal_date': hw['signal_date'].strftime('%Y-%m-%d'),
                                'buy_date': hw['buy_date'].strftime('%Y-%m-%d'),
                                'sell_date': td.strftime('%Y-%m-%d'),
                                'code': weakest, 'name': hw.get('name', weakest),
                                'buy_price': round(hw['buy_price'], 2), 'sell_price': round(wpx, 2),
                                'net_ret': round((gross - TOTAL_COST) * 100, 2),
                                'is_win': (gross - TOTAL_COST) > 0,
                                'exit_reason': '轮换换仓(更强超跌)', 'days_held': hw.get('days', 1),
                                'drop': hw.get('drop', 0),
                            })
                            available_slots = 1
                    for c in cands[:max(0, available_slots)]:
                        code = c['code']
                        if code in holdings:
                            continue
                        entry = c['close']
                        position_capital = INITIAL * self.position_pct
                        shares = int(position_capital / entry / 100) * 100
                        if shares < 100 or shares * entry > cash:
                            continue
                        cash -= shares * entry * (1 + 0.0003)
                        holdings[code] = {
                            'code': code, 'name': c['name'], 'drop': c['drop'],
                            'buy_price': entry, 'shares': shares,
                            'buy_date': td, 'signal_date': td, 'highest': entry,
                        }

            # 3) 市值
            pos_val = 0.0
            for _code, _h in holdings.items():
                df = self._read(_code)
                if df is not None and td in df.index:
                    pos_val += _h['shares'] * float(df.loc[td, 'close'])
            total = cash + pos_val
            portfolio_values.append((td, total, cash, pos_val))
            if total > peak:
                peak = total

        cum = (cash / INITIAL - 1) * 100
        peak2, maxdd = INITIAL, 0.0
        for _, tv, _, _ in portfolio_values:
            if tv > peak2:
                peak2 = tv
            dd = (peak2 - tv) / peak2 * 100
            maxdd = max(maxdd, dd)
        return {'trades': trades, 'cumulative_return': round(cum, 2),
                'max_drawdown': round(maxdd, 2), 'portfolio_values': portfolio_values}
