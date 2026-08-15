"""2进3 接力战法回测 — 与 1进2 同规则，仅把标的从"首板"换成"二板"

背景：用户想验证「一进二战法的买卖规则不变，仅把选股从首板改成二板，胜率是否更高」。

选股（唯一改动，其余与 strategy_regime/one_two_backtest.py 完全一致）：
  1进2: 今日涨停 且 昨日未涨停                → 首板
  2进3: 今日涨停 且 昨日涨停 且 前日未涨停     → 恰好二板（不包含三板+）

买入：T+1 开盘价（gap>7% 买不到 / gap<3% 竞价未确认，均跳过）
卖出：连板跟踪 → 断板卖出 · 首日未晋级收盘卖出 · -5% 止损 · 最多 3 天
成本：万3佣金×2 + 印花税0.05% ≈ 0.35%

数据源：TDX 本地日线（与 1进2 一致）。
用法：
    python -m ashare_review.analysis.strategy_regime.two_three_backtest
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

TOTAL_COST = 0.0035          # 万3佣金×2 + 印花税0.05% ≈ 0.35%（与1进2相同）
NAME_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'stock_name_map.json')

# 与 strategy_regime/run.py 相同的回测区间（2025-08 ~ 2026-08，一整年）
START = date(2025, 8, 8)
END = date(2026, 8, 7)


class TwoThreeBacktest:
    """2进3 战法历史回测器（TDX 数据源）"""

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

    # ── 涨停日索引 ──
    def _build_lup_days(self, lu_idx: TdxLimitUpIndex):
        """从涨停索引构建 code -> 涨停日期集合（用于连板判断 + 年涨停次数）"""
        self._lup_days = defaultdict(set)
        for d, lus in lu_idx._index.items():
            for lu in lus:
                self._lup_days[lu['code']].add(d)

    def _limit_up_count(self, code: str, td: date, window_td: int = 250) -> int:
        """近 window_td 个交易日内的涨停次数（股性）"""
        days = self._lup_days.get(code)
        if not days:
            return 0
        start = td
        for _ in range(window_td + 1):
            start = self.cal.prev_trading_day(start, offset=1)
        return sum(1 for d in days if start < d <= td)

    def _was_lup_n_ago(self, code: str, td: date, n: int) -> bool:
        """td 往前第 n 个交易日是否为涨停日"""
        d = td
        for _ in range(n):
            d = self.cal.prev_trading_day(d, offset=1)
        return d in self._lup_days.get(code, set())

    # ── 选股（恰好二板） ──
    def _select_picks(self, lus: List[Dict], td: date) -> List[Dict]:
        picks = []
        for lu in lus:
            code = lu['code']
            if not _is_a_stock(code):
                continue
            # 恰好二板：昨日涨停 且 前日未涨停（今日必涨停，因为在 lus 里）
            if not self._was_lup_n_ago(code, td, 1):
                continue          # 昨日未涨停 → 首板，不是二板
            if self._was_lup_n_ago(code, td, 2):
                continue          # 前日也涨停 → 三板及以上，不是二板
            # 主板
            if not (str(code).startswith(('60', '00', '001', '002'))):
                continue
            # 非 ST
            name = self._get_name(code)
            if 'ST' in name:
                continue
            # 一字板排除（二板一字买不到，与1进2排除首板一字一致）
            if lu['is_yizi']:
                continue
            close = float(lu['close'])
            # 股价 3-15
            if not (3 <= close <= 15):
                continue

            # ── 评分（TDX 近似，与 1进2 完全相同，仅把"首板"字样换成"二板"） ──
            score = 0
            reasons = []
            score += 3
            score += 10
            reasons.append(f'低价{close:.1f}元')
            score += 8
            reasons.append('二板')
            lc = self._limit_up_count(code, td)
            if lc >= 15:
                score += 8
            elif lc >= 10:
                score += 5
            elif lc >= 5:
                score += 2
            elif lc <= 1:
                score -= 2
            hi, lo = float(lu['high']), float(lu['low'])
            us = (hi - close) / (hi - lo + 0.01)
            if us < 0.2:
                score += 10
                reasons.append('封死无上影')
            elif us < 0.5:
                score += 5
            prev_close = float(lu['prev_close'])
            gap = (float(lu['open']) - prev_close) / prev_close if prev_close > 0 else 0
            if 0.01 <= gap <= 0.05:
                score += 8
                reasons.append('早盘板')
            elif gap < 0.01:
                score += 4
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

    # ── 交易模拟（与 1进2 逐行一致） ──
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

        if t1 not in df.index:
            return None
        entry_open = float(df.loc[t1, 'open'])
        if entry_open <= 0:
            return None
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
            pos = df.index.get_loc(d)
            if pos == 0:
                break
            prev_close = float(df['close'].iloc[pos - 1])
            chg = (c - prev_close) / prev_close if prev_close > 0 else 0

            if lo <= entry * 0.95:
                exit_price = min(entry * 0.95, o)
                return self._mk_trade(p, t1, d, entry, exit_price,
                                      '止损-5%', k + 1, had_zt)
            if chg >= threshold:
                had_zt = True
                continue
            tp_price = entry * 1.07
            if h >= tp_price:
                return self._mk_trade(p, t1, d, entry, tp_price,
                                      '冲高止盈', k + 1, had_zt)
            if k == 0:
                return self._mk_trade(p, t1, d, entry, c, '未晋级收盘卖', 1, had_zt)
            return self._mk_trade(p, t1, d, entry, c, '断板卖出', k + 1, had_zt)

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
        scan_start = start - timedelta(days=400)
        lu_idx = TdxLimitUpIndex(self.tdx, lookback_calendar_days=420)
        print('扫描涨停索引...')
        lu_idx.build(scan_start, end)
        self._build_lup_days(lu_idx)

        trading_days = []
        d = start
        while d <= end:
            if self.cal.is_trading_day(d):
                trading_days.append(d)
            d += timedelta(days=1)

        trades = []
        for i, td in enumerate(trading_days):
            if (i + 1) % 30 == 0:
                print(f'  2进3 {i+1}/{len(trading_days)} 天, 交易 {len(trades)} 笔')
            lus = lu_idx.get_first_boards(td)
            if not lus:
                continue
            picks = self._select_picks(lus, td)
            for p in picks:
                tr = self._simulate(p)
                if tr:
                    trades.append(tr)
        return trades


# ======================================================================
# 对比统计（1进2 vs 2进3，同一区间同一口径）
# ======================================================================
def strat_stats(trades: List[Dict]) -> Dict:
    """单策略统计（逐笔等权，与 strategy_regime/run.py 口径一致）"""
    valid = [t for t in trades if not t.get('skipped_gap')]
    wins = [t for t in valid if t.get('is_win')]
    losses = [t for t in valid if not t.get('is_win')]
    rets = [t.get('net_ret', 0) for t in valid]
    gross = [t.get('gross_ret', 0) for t in valid]
    return {
        'n': len(valid), 'wins': len(wins), 'losses': len(losses),
        'skipped': len(trades) - len(valid),
        'win_rate': round(len(wins) / max(len(valid), 1) * 100, 1),
        'avg_ret': round(float(np.mean(rets)) if rets else 0, 2),
        'avg_win': round(float(np.mean([t['net_ret'] for t in wins])) if wins else 0, 2),
        'avg_loss': round(float(np.mean([t['net_ret'] for t in losses])) if losses else 0, 2),
        'pf': round(sum(t['net_ret'] for t in wins) / max(abs(sum(t['net_ret'] for t in losses)), 0.01), 2),
        'cum_ret': round(sum(rets), 2),  # 等权累计（非复利）
        'total_gross': round(sum(gross), 2),
    }


def print_comparison(two_three_trades: List[Dict],
                     one_two_trades: List[Dict] = None):
    rows = [('2进3', two_three_trades)]
    if one_two_trades is not None:
        rows.append(('1进2', one_two_trades))

    print(f'\n{"="*78}')
    print(f'  战法对比回测（{START} ~ {END}，已扣成本0.35%）')
    print(f'{"="*78}')
    print(f'  {"战法":<6} {"候选":>6} {"有效":>6} {"高开跳过":>8} '
          f'{"胜率%":>8} {"笔均%":>8} {"均盈%":>8} {"均亏%":>8} '
          f'{"盈亏比":>6} {"累计%":>8}')
    print('  ' + '-' * 76)
    for label, trades in rows:
        st = strat_stats(trades)
        print(f'  {label:<6} {len(trades):>6} {st["n"]:>6} {st["skipped"]:>8} '
              f'{st["win_rate"]:>8.1f} {st["avg_ret"]:>+8.2f} '
              f'{st["avg_win"]:>+8.2f} {st["avg_loss"]:>+8.2f} '
              f'{st["pf"]:>6.2f} {st["cum_ret"]:>+8.1f}')

    # 出场原因分布
    print(f'\n  ==== 出场原因分布 ====')
    from collections import Counter
    for label, trades in rows:
        valid = [t for t in trades if not t.get('skipped_gap')]
        cnt = Counter(t['exit_reason'] for t in valid)
        print(f'  {label:<6} ' + ' | '.join(f'{k}×{v}' for k, v in cnt.most_common()))

    # 按持仓天数
    print(f'\n  ==== 按持仓天数 ====')
    for label, trades in rows:
        valid = [t for t in trades if not t.get('skipped_gap')]
        by_days = defaultdict(list)
        for t in valid:
            by_days[t['days_held']].append(t)
        parts = []
        for d in sorted(by_days):
            w = len([t for t in by_days[d] if t['is_win']])
            parts.append(f'{d}天: {len(by_days[d])}笔 胜{w} 率{w/max(len(by_days[d]),1)*100:.0f}%')
        print(f'  {label:<6} ' + ' | '.join(parts))


def main():
    import argparse
    ap = argparse.ArgumentParser(description='2进3 战法回测（对比 1进2）')
    ap.add_argument('--start', default=str(START), help='开始日期 YYYY-MM-DD')
    ap.add_argument('--end', default=str(END), help='结束日期 YYYY-MM-DD')
    ap.add_argument('--save', default=None, help='保存2进3交易到json')
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    print('=' * 60)
    print('  2进3 战法回测（买卖规则与 1进2 完全一致，仅标的换成二板）')
    print(f'  区间: {start} ~ {end}')
    print('=' * 60)

    bt = TwoThreeBacktest()
    trades = bt.run(start, end)
    if args.save:
        os.makedirs(os.path.dirname(os.path.abspath(args.save)), exist_ok=True)
        with open(args.save, 'w', encoding='utf-8') as f:
            json.dump(trades, f, ensure_ascii=False)
        print(f'[保存] 2进3 交易 → {args.save}')

    # 加载 1进2 基线（同一区间缓存）
    one_two_trades = None
    cached = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                          'data', 'strategy_regime', 'one_two_trades.json')
    if os.path.exists(cached):
        try:
            with open(cached, 'r', encoding='utf-8') as f:
                one_two_trades = json.load(f)
            one_two_trades = [t for t in one_two_trades
                              if args.start <= t['signal_date'] <= args.end]
            print(f'[基线] 加载 1进2 缓存（{len(one_two_trades)} 笔，区间 {args.start}~{args.end}）')
        except Exception as e:
            print(f'[基线] 加载失败: {e}')
            one_two_trades = None

    print_comparison(trades, one_two_trades)


if __name__ == '__main__':
    main()
