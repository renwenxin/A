"""1进2 接力 — 集中持仓 + 每日轮换（资金模型版）

原 one_two_backtest 是"逐笔独立模拟"（每笔全仓），没有组合/资金层。
这里加组合层：N 仓 × (100/N)% + 每日轮换——
  满仓后若新首板信号评分 > 最弱持仓强度(=信号分×(1+当前收益))，
  次日开盘卖出最弱、换入新标的（只做最强首板）。

买卖规则沿用 one_two_backtest：
  - 选股: 首板+非一字+主板+3-15元+股性+封板质量（_select_picks）
  - 买入: T+1 开盘，竞价确认高开 3%~7%
  - 卖出: 连板跟踪(涨停持有/断板卖) + 未晋级收盘卖 + 冲高+7%止盈 + -5%止损 + 最多3天
"""
import os
import json
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd

from ...data.tdx_reader import TdxReader
from ...utils.calendar import TradingCalendar
from ..one_two_backtest import TdxLimitUpIndex, _board_limit_threshold, _is_a_stock

TOTAL_COST = 0.0035
NAME_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'stock_name_map.json')


class OneTwoPortfolio:
    """1进2 集中持仓 + 每日轮换"""

    def __init__(self, tdx: TdxReader = None, max_positions: int = 3,
                 position_pct: float = None, rotation: bool = True):
        self.tdx = tdx or TdxReader()
        self.cal = TradingCalendar()
        self.max_positions = max_positions
        self.position_pct = position_pct or (1.0 / max_positions)
        self.rotation = rotation
        self._name_map = {}
        self._load_name_map()
        self._lup_days = {}
        self._stock_cache: Dict[str, pd.DataFrame] = {}

    def _load_name_map(self):
        if os.path.exists(NAME_CACHE_FILE):
            try:
                with open(NAME_CACHE_FILE, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self._name_map = d
            except Exception:
                pass

    def _get_name(self, code):
        return self._name_map.get(str(code).zfill(6), str(code))

    def _market(self, code):
        return 'sh' if str(code).startswith('6') else 'sz'

    def _build_lup_days(self, lu_idx):
        from collections import defaultdict
        self._lup_days = defaultdict(set)
        for d, lus in lu_idx._index.items():
            for lu in lus:
                self._lup_days[lu['code']].add(d)

    def _was_lup_yesterday(self, code, td):
        prev = self.cal.prev_trading_day(td, offset=1)
        return prev in self._lup_days.get(code, set())

    def _limit_up_count(self, code, td, window_td=250):
        days = self._lup_days.get(code)
        if not days:
            return 0
        start = td
        for _ in range(window_td + 1):
            start = self.cal.prev_trading_day(start, offset=1)
        return sum(1 for d in days if start < d <= td)

    # ── 选股（与 one_two_backtest._select_picks 一致） ──
    def _select_picks(self, lus, td):
        picks = []
        for lu in lus:
            code = lu['code']
            if not _is_a_stock(code):
                continue
            if self._was_lup_yesterday(code, td):
                continue
            if not str(code).startswith(('60', '00', '001', '002')):
                continue
            name = self._get_name(code)
            if 'ST' in name:
                continue
            if lu['is_yizi']:
                continue
            close = float(lu['close'])
            if not (3 <= close <= 15):
                continue
            score = 21  # 主板+3 + 低价+10 + 首板+8
            lc = self._limit_up_count(code, td)
            if lc >= 15:
                score += 8
            elif lc >= 10:
                score += 5
            elif lc >= 5:
                score += 2
            hi, lo = float(lu['high']), float(lu['low'])
            us = (hi - close) / (hi - lo + 0.01)
            if us < 0.2:
                score += 10
            elif us < 0.5:
                score += 5
            prev_close = float(lu['prev_close'])
            gap = (float(lu['open']) - prev_close) / prev_close if prev_close > 0 else 0
            if 0.01 <= gap <= 0.05:
                score += 8
            elif gap < 0.01:
                score += 4
            amount_yi = float(lu['amount']) / 1e8
            if amount_yi > 5:
                score += 5
            elif amount_yi > 2:
                score += 3
            if score < 8:
                continue
            picks.append({'code': code, 'name': name, 'score': min(score, 100),
                          'close': close, 'signal_date': td, 'limit_count': lc})
        picks.sort(key=lambda x: x['score'], reverse=True)
        return picks

    def _read(self, code):
        if code not in self._stock_cache:
            try:
                df = self.tdx.read_daily(code, self._market(code))
                if df is not None and not df.empty:
                    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
                    df = df.set_index('trade_date')
                    self._stock_cache[code] = df
            except Exception:
                self._stock_cache[code] = None
        return self._stock_cache.get(code)

    # ── 持仓日度卖出检查（沿用逐笔规则；命中当日按对应价格卖出） ──
    def _check_exit(self, code, h, td):
        df = self._read(code)
        if df is None or td not in df.index:
            return None
        pos = df.index.get_loc(td)
        if pos < 1:
            return None
        bar = df.iloc[pos]
        prev_close = float(df['close'].iloc[pos - 1])
        o, hi, lo, c = float(bar['open']), float(bar['high']), float(bar['low']), float(bar['close'])
        entry = h['buy_price']
        threshold = _board_limit_threshold(code) / 100.0
        chg = (c - prev_close) / prev_close if prev_close > 0 else 0
        days = h.get('days', 1)
        # -5% 止损（当日以 min(止损, 开盘) 成交）
        if lo <= entry * 0.95:
            return {'reason': '止损-5%', 'days': days, 'sell_price': min(entry * 0.95, o)}
        # 涨停 → 晋级持有
        if chg >= threshold:
            h['had_zt'] = True
            return None
        # 冲高 +7% 止盈（不封板，当日以 +7% 成交）
        if hi >= entry * 1.07:
            return {'reason': '冲高+7%止盈', 'days': days, 'sell_price': entry * 1.07}
        # 首日未晋级 → 收盘卖
        if days == 1 and not h.get('had_zt'):
            return {'reason': '未晋级', 'days': days, 'sell_price': c}
        # 晋级后断板 → 收盘卖
        if h.get('had_zt'):
            return {'reason': '断板卖出', 'days': days, 'sell_price': c}
        # 最多 3 天 → 收盘卖
        if days >= 3:
            return {'reason': '3天到期', 'days': days, 'sell_price': c}
        return None

    # ── 主回测（资金模型） ──
    def run(self, start: date, end: date) -> Dict:
        lu_idx = TdxLimitUpIndex(self.tdx, lookback_calendar_days=420)
        lu_idx.build(start - timedelta(days=400), end)
        self._build_lup_days(lu_idx)

        trading_days = []
        d = start
        while d <= end:
            if self.cal.is_trading_day(d):
                trading_days.append(d)
            d += timedelta(days=1)
        td_set = set(trading_days)

        INITIAL = 1_000_000.0
        cash = INITIAL
        holdings: Dict[str, dict] = {}
        trades = []
        portfolio_values = []
        peak = INITIAL

        for i, td in enumerate(trading_days):
            # 1) 持仓卖出检查（当日按对应价格卖出）
            for code in list(holdings.keys()):
                h = holdings[code]
                h['days'] = h.get('days', 0) + 1
                ex = self._check_exit(code, h, td)
                if ex:
                    sell_price = ex['sell_price']
                    proceeds = h['shares'] * sell_price * (1 - 0.0008)
                    cash += proceeds
                    gross = (sell_price - h['buy_price']) / h['buy_price']
                    trades.append({
                        'signal_date': h['signal_date'].strftime('%Y-%m-%d'),
                        'buy_date': h['buy_date'].strftime('%Y-%m-%d'),
                        'sell_date': td.strftime('%Y-%m-%d'),
                        'code': code, 'name': h.get('name', code),
                        'buy_price': round(h['buy_price'], 2),
                        'sell_price': round(sell_price, 2),
                        'net_ret': round((gross - TOTAL_COST) * 100, 2),
                        'is_win': (gross - TOTAL_COST) > 0,
                        'exit_reason': ex['reason'],
                        'days_held': ex['days'],
                        'score': h.get('score', 0),
                    })
                    del holdings[code]

            # 2) 检测新信号
            candidates = self._select_picks(lu_idx.get_first_boards(td), td)
            candidates.sort(key=lambda x: -x['score'])
            # 竞价确认 + 找 T+1
            buys = []
            for c in candidates:
                ntd = self.cal.next_trading_day(td)
                if ntd not in td_set:
                    continue
                df = self._read(c['code'])
                if df is None or ntd not in df.index:
                    continue
                buy_open = float(df.loc[ntd, 'open'])
                gap = (buy_open - c['close']) / c['close']
                if not (0.03 <= gap <= 0.07):   # 竞价确认 3%~7%
                    continue
                buys.append({**c, 'buy_open': buy_open, 'next_td': ntd})

            # 4) 买入：填仓 + 轮换
            available_slots = max(0, self.max_positions - len(holdings))
            if available_slots == 0 and self.rotation and buys:
                weakest, weakest_strength = None, 1e18
                for _code, _h in holdings.items():
                    df = self._read(_code)
                    cur = float(df.loc[td, 'close']) if (df is not None and td in df.index) else _h['buy_price']
                    ret = cur / _h['buy_price'] - 1 if _h['buy_price'] > 0 else 0
                    s = _h.get('score', 0) * (1 + ret)
                    if s < weakest_strength:
                        weakest, weakest_strength = _code, s
                top = buys[0]
                if weakest and top['score'] > weakest_strength:
                    # 当日收盘卖出最弱持仓，腾出资金换入更强首板
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
                        'exit_reason': '轮换换仓(更强首板)', 'days_held': hw.get('days', 1),
                        'score': hw.get('score', 0),
                    })
                    available_slots = 1

            for c in buys[:max(0, available_slots)]:
                code = c['code']
                if code in holdings:
                    continue
                ntd = c['next_td']
                position_capital = INITIAL * self.position_pct
                shares = int(position_capital / c['buy_open'] / 100) * 100
                if shares < 100:
                    continue
                buy_cost = shares * c['buy_open'] * (1 + 0.0003)
                if buy_cost > cash:
                    continue
                cash -= buy_cost
                holdings[code] = {
                    'code': code, 'name': c['name'], 'score': c['score'],
                    'buy_price': c['buy_open'], 'shares': shares,
                    'buy_date': ntd, 'signal_date': td, 'had_zt': False,
                }

            # 5) 市值 + 记录
            pos_val = 0.0
            for _code, _h in holdings.items():
                df = self._read(_code)
                if df is not None and td in df.index:
                    pos_val += _h['shares'] * float(df.loc[td, 'close'])
            total = cash + pos_val
            portfolio_values.append((td, total, cash, pos_val))
            if total > peak:
                peak = total
            last_total = total

        # 收尾：强制平仓
        last_td = trading_days[-1]
        for code in list(holdings.keys()):
            h = holdings.pop(code)
            df = self._read(code)
            if df is not None and last_td in df.index:
                sell_price = float(df.loc[last_td, 'close'])
            else:
                sell_price = h['buy_price']
            cash += h['shares'] * sell_price * (1 - 0.0008)
            gross = (sell_price - h['buy_price']) / h['buy_price']
            trades.append({
                'signal_date': h['signal_date'].strftime('%Y-%m-%d'),
                'buy_date': h['buy_date'].strftime('%Y-%m-%d'),
                'sell_date': last_td.strftime('%Y-%m-%d'),
                'code': code, 'name': h.get('name', code),
                'buy_price': round(h['buy_price'], 2), 'sell_price': round(sell_price, 2),
                'net_ret': round((gross - TOTAL_COST) * 100, 2),
                'is_win': (gross - TOTAL_COST) > 0, 'exit_reason': '回测到期强平',
                'days_held': 1, 'score': h.get('score', 0),
            })

        cum = (cash / INITIAL - 1) * 100
        peak2, maxdd = INITIAL, 0.0
        for _, tv, _, _ in portfolio_values:
            if tv > peak2:
                peak2 = tv
            dd = (peak2 - tv) / peak2 * 100
            maxdd = max(maxdd, dd)

        return {'trades': trades, 'cumulative_return': round(cum, 2),
                'max_drawdown': round(maxdd, 2), 'portfolio_values': portfolio_values}
