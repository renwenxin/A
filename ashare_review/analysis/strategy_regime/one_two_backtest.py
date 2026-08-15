"""1进2 接力战法回测 — 复盘分析《今日一进二精选》的 TDX 本地近似

选股：复刻 report/daily.py `_select_top_picks` 的可计算硬性条件
  - 仅首板（consecutive==1：今日涨停且昨日未涨停）
  - 排除一字板（开盘=最高=最低=收盘）
  - 沪深主板（60/00/001/002），非 ST
  - 股价 3-15 元（低价=群众基础广）
  - 股性活跃（近 250 交易日涨停次数，作评分加分）
  - 封板质量（上影线小≈封成比） + 早盘封板（高开幅度近似涨停时间）
  评分 ≥ 8 → Top 8（与复盘页一致）

买入：T+1 开盘价（gap>7% 视为买不到跳过）
卖出：连板跟踪 → 断板卖出 · 首日未晋级收盘卖出 · -5% 止损 · 最多 3 天

数据源：TDX 本地日线（akshare 封单额/涨停时间/流通市值无历史，用 TDX 可算量近似）。
"""
import os
import json
from datetime import date, timedelta
from typing import Dict, List, Optional, Set
from collections import defaultdict

import numpy as np
import pandas as pd

from ...data.tdx_reader import TdxReader
from ...utils.calendar import TradingCalendar
from ..one_two_backtest import TdxLimitUpIndex, _board_limit_threshold, _is_a_stock

TOTAL_COST = 0.0035          # 万3佣金×2 + 印花税0.05% ≈ 0.35%
NAME_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'stock_name_map.json')


class OneTwoBacktest:
    """1进2 战法历史回测器（TDX 数据源）"""

    def __init__(self, tdx: TdxReader = None):
        self.tdx = tdx or TdxReader()
        self.cal = TradingCalendar()
        self._name_map: Dict[str, str] = {}
        self._load_name_map()
        self._lup_days: Dict[str, Set[date]] = {}  # code -> set(limit-up dates)

    def _load_name_map(self):
        if os.path.exists(NAME_CACHE_FILE):
            try:
                with open(NAME_CACHE_FILE, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self._name_map = d
            except Exception:
                pass

    def _get_name(self, code: str) -> str:
        return self._name_map.get(str(code).zfill(6), str(code))

    def _market(self, code: str) -> str:
        code = str(code).zfill(6)
        if code.startswith('6'):
            return 'sh'
        return 'sz'

    # ── 首板检测 ──
    def _build_lup_days(self, lu_idx: TdxLimitUpIndex):
        """从涨停索引构建 code -> 涨停日期集合（用于首板判断 + 年涨停次数）"""
        self._lup_days = defaultdict(set)
        for d, lus in lu_idx._index.items():
            for lu in lus:
                self._lup_days[lu['code']].add(d)

    def _limit_up_count(self, code: str, td: date, window_td: int = 250) -> int:
        """近 window_td 个交易日内的涨停次数（股性）"""
        days = self._lup_days.get(code)
        if not days:
            return 0
        # 找 td 之前 window_td 个交易日
        start = td
        for _ in range(window_td + 1):
            start = self.cal.prev_trading_day(start, offset=1)
        return sum(1 for d in days if start < d <= td)

    def _was_lup_yesterday(self, code: str, td: date) -> bool:
        prev = self.cal.prev_trading_day(td, offset=1)
        return prev in self._lup_days.get(code, set())

    # ── 选股（复刻 _select_top_picks） ──
    def _select_picks(self, lus: List[Dict], td: date) -> List[Dict]:
        picks = []
        for lu in lus:
            code = lu['code']
            if not _is_a_stock(code):
                continue
            # 仅首板（昨日未涨停）
            if self._was_lup_yesterday(code, td):
                continue
            # 主板
            if not (str(code).startswith(('60', '00', '001', '002'))):
                continue
            # 非 ST
            name = self._get_name(code)
            if 'ST' in name:
                continue
            # 一字板排除
            if lu['is_yizi']:
                continue
            close = float(lu['close'])
            # 股价 3-15
            if not (3 <= close <= 15):
                continue

            # ── 评分（TDX 近似 _select_top_picks） ──
            score = 0
            reasons = []
            # 主板 +3
            score += 3
            # 股价 3-15 +10
            score += 10
            reasons.append(f'低价{close:.1f}元')
            # 首板 +8
            score += 8
            # 股性（年涨停次数）
            lc = self._limit_up_count(code, td)
            if lc >= 15:
                score += 8
            elif lc >= 10:
                score += 5
            elif lc >= 5:
                score += 2
            elif lc <= 1:
                score -= 2
            # 封板质量：上影线小≈封死（近似封成比）
            hi, lo = float(lu['high']), float(lu['low'])
            us = (hi - close) / (hi - lo + 0.01)
            if us < 0.2:
                score += 10
                reasons.append('封死无上影')
            elif us < 0.5:
                score += 5
            # 早盘封板：小高开拉板≈早盘（近似涨停时间）
            prev_close = float(lu['prev_close'])
            gap = (float(lu['open']) - prev_close) / prev_close if prev_close > 0 else 0
            if 0.01 <= gap <= 0.05:
                score += 8
                reasons.append('早盘板')
            elif gap < 0.01:
                score += 4
            # 成交额市场合力
            amount_yi = float(lu['amount']) / 1e8
            if amount_yi > 5:
                score += 5
            elif amount_yi > 2:
                score += 3

            if score < 8:
                continue
            picks.append({
                'code': code, 'name': name,
                'score': score, 'close': close,
                'change_pct': lu['change_pct'],
                'limit_count': lc,
                'signal_date': td,
                'gap': round(gap, 3),
            })

        picks.sort(key=lambda x: x['score'], reverse=True)
        return picks[:8]

    # ── 交易模拟（连板跟踪 + 断板卖） ──
    def _simulate(self, p: Dict) -> Optional[Dict]:
        code = p['code']
        signal_date = p['signal_date']
        t1 = self.cal.next_trading_day(signal_date)
        if t1 is None:
            return None
        try:
            df = self.tdx.read_daily(code, self._market(code))
        except Exception:
            return None
        if df is None or df.empty:
            return None
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        df = df.set_index('trade_date')

        # T+1 开盘
        if t1 not in df.index:
            return None
        entry_open = float(df.loc[t1, 'open'])
        if entry_open <= 0:
            return None
        # gap 过滤：>7% 买不到；<3% 竞价未确认（战法: 首板次日需高开3%+才参与）
        gap = (entry_open - p['close']) / p['close']
        if gap > 0.07 or gap < 0.03:
            return {
                'code': code, 'name': p['name'], 'score': p['score'],
                'signal_date': signal_date.strftime('%Y-%m-%d'),
                'buy_date': t1.strftime('%Y-%m-%d'),
                'buy_price': 0, 'sell_price': 0, 'net_ret': 0,
                'is_win': False, 'exit_reason': '竞价未确认' if gap < 0.03 else 'gap>7%买不到',
                'days_held': 0, 'skipped_gap': True,
            }

        threshold = _board_limit_threshold(code) / 100.0
        entry = entry_open
        had_zt = False
        hold_dates = [t1, self.cal.next_trading_day(t1),
                      self.cal.next_trading_day(self.cal.next_trading_day(t1))]

        for k, d in enumerate(hold_dates):
            if d not in df.index:
                break
            bar = df.loc[d]
            o = float(bar['open']); h = float(bar['high'])
            lo = float(bar['low']); c = float(bar['close'])
            # 涨停判断需要前收
            pos = df.index.get_loc(d)
            if pos == 0:
                break
            prev_close = float(df['close'].iloc[pos - 1])
            chg = (c - prev_close) / prev_close if prev_close > 0 else 0

            # -5% 止损
            if lo <= entry * 0.95:
                exit_price = min(entry * 0.95, o)
                return self._mk_trade(p, t1, d, entry, exit_price,
                                      '止损-5%', k + 1, had_zt)
            # 涨停 → 晋级，继续持有
            if chg >= threshold:
                had_zt = True
                continue
            # 未涨停
            tp_price = entry * 1.07
            if h >= tp_price:
                # 战法: 拉到7-8点不封 → 冲高止盈
                return self._mk_trade(p, t1, d, entry, tp_price,
                                      '冲高止盈', k + 1, had_zt)
            if k == 0:
                return self._mk_trade(p, t1, d, entry, c, '未晋级收盘卖', 1, had_zt)
            return self._mk_trade(p, t1, d, entry, c, '断板卖出', k + 1, had_zt)

        # 3 天到期
        last = hold_dates[-1]
        if last in df.index:
            c = float(df.loc[last, 'close'])
            return self._mk_trade(p, t1, last, entry, c, '3天到期', 3, had_zt)
        return None

    @staticmethod
    def _mk_trade(p, buy_d, sell_d, entry, exit_price, reason, days, had_zt):
        gross = (exit_price - entry) / entry if entry > 0 else 0
        net = gross - TOTAL_COST
        return {
            'code': p['code'], 'name': p['name'], 'score': p['score'],
            'signal_date': p['signal_date'].strftime('%Y-%m-%d'),
            'buy_date': buy_d.strftime('%Y-%m-%d'),
            'sell_date': sell_d.strftime('%Y-%m-%d'),
            'buy_price': round(entry, 2),
            'sell_price': round(exit_price, 2),
            'gross_ret': round(gross * 100, 2),
            'net_ret': round(net * 100, 2),
            'is_win': net > 0,
            'exit_reason': reason,
            'days_held': days,
            'had_zt': had_zt,
            'skipped_gap': False,
        }

    # ── 主入口 ──
    def run(self, start: date, end: date) -> List[Dict]:
        """回测 [start, end]，返回逐笔交易列表。"""
        # 涨停索引需要覆盖 start 之前的窗口以判断首板 + 年涨停
        scan_start = start - timedelta(days=400)
        lu_idx = TdxLimitUpIndex(self.tdx, lookback_calendar_days=420)
        print('扫描涨停索引...')
        lu_idx.build(scan_start, end)
        self._build_lup_days(lu_idx)

        # 交易日
        trading_days = []
        d = start
        while d <= end:
            if self.cal.is_trading_day(d):
                trading_days.append(d)
            d += timedelta(days=1)

        trades = []
        for i, td in enumerate(trading_days):
            if (i + 1) % 30 == 0:
                print(f'  1进2 {i+1}/{len(trading_days)} 天, 交易 {len(trades)} 笔')
            lus = lu_idx.get_first_boards(td)
            if not lus:
                continue
            picks = self._select_picks(lus, td)
            for p in picks:
                tr = self._simulate(p)
                if tr:
                    trades.append(tr)
        return trades
