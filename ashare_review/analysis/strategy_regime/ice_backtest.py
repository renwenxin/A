"""冰点抄底战法回测 — 按 R 的战法 + 缠论设计

市场级别（冰点检测）:
  - 普通冰点: 上涨家数 ≤ 1200 或 涨停家数 ≤ 30
  - 极冰点:   上涨家数 ≤ 800 或 涨停家数 ≤ 20
  - 缠论增强: 冰点通常发生在 上证 下跌段 + MACD 背驰（下跌衰竭）

反转确认（冰点反转三条件，需在冰点后 5 个交易日内）:
  ① 大盘放量大阳: 上证涨幅 ≥ 1.5% 且 成交额 > 前 5 日均量
  ② 涨停家数明显增加: 涨停 ≥ 45（从冰点修复）
  ③ 上涨家数回升: 上涨家数 ≥ 2500（赚钱效应恢复）
  满足任一 → 冰点反转确认日

个股级别（超跌抄底标的）:
  候选: 沪深主板 + 非ST + 年涨停≥10（前期强势/妖股基因，limit_up_pool）
  超跌: 距 120 日高点回落 ≥ 30%
  底部特征: 站上 MA5（企稳） 或 近 5 日内有缠论底分型
  取超跌最深的 Top N

买入: 反转确认日次日开盘
卖出（缠论: 卖点永远在下跌中产生）:
  - 反弹到压力位（前 20 日高点）→ 止盈
  - 反弹 ≥ +8% → 止盈
  - 移动止盈: 最高收盘回落 > 5%
  - 止损: 跌破买入价 6%
  - 最长持有 10 天
"""
import os
import json
from datetime import date, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

import numpy as np
import pandas as pd

from ...data.tdx_reader import TdxReader
from ...utils.calendar import TradingCalendar
from . import chan

TOTAL_COST = 0.0035
LIMIT_UP_POOL_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'limit_up_pool.json')
NAME_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'stock_name_map.json')

# 冰点阈值（战法）
ICE_UP = 1200        # 普通冰点: 上涨家数 ≤ 1200
DEEP_ICE_UP = 800    # 极冰点
ICE_ZT = 30          # 普通冰点: 涨停 ≤ 30
DEEP_ICE_ZT = 20     # 极冰点
# 反转确认（冰点反转三条件，需在冰点后 5 个交易日内）:
#  ① 大盘放量大阳: 上证涨幅 ≥ 1.5%
#  ② 涨停家数明显增加: 涨停 ≥ 80 且 上涨家数 ≥ 3000（情绪明显修复）
#  满足① 或 ② → 冰点反转确认日。每段冰点只确认一次。
REV_CHG = 1.5        # 大盘涨幅 %
REV_ZT = 80          # 涨停回升阈值
REV_UP = 3000        # 上涨家数回升阈值
REV_WINDOW = 5       # 冰点后 5 日内确认
OVERSOLD_DROP = 0.30 # 超跌 30%
MAX_PRICE = 40       # 抄底股价上限（低价优先）
TOP_N = 5            # 每天抄底标的数
STOP = 0.06          # 止损 6%
TP = 0.08            # 止盈 8%
TRAIL = 0.05         # 移动止盈回落
MAX_HOLD = 10        # 最长持有天数


def find_reversal_days(state_df: pd.DataFrame) -> List[int]:
    """在 market_state 中找冰点反转确认日的索引。

    先把连续冰点日合并成段，每段冰点只在 REV_WINDOW 内确认一次反转，
    避免"冰点后 5 天每天都算反转"导致的过度交易。
    """
    ice = state_df['emotion'].isin(['冰点', '极冰点'])

    # 冰点段（连续冰点日合并）
    periods = []
    in_ice = False
    for i in range(len(state_df)):
        if ice.iloc[i]:
            if not in_ice:
                periods.append([i, i])
                in_ice = True
            else:
                periods[-1][1] = i
        else:
            in_ice = False

    revs = []
    for s, e in periods:
        for j in range(e + 1, min(e + 1 + REV_WINDOW, len(state_df))):
            r = state_df.iloc[j]
            big_up = r.get('sh_chg', 0) >= REV_CHG
            zt_recover = ((r.get('limit_up', 0) or 0) >= REV_ZT
                          and (r.get('up_count', 0) or 0) >= REV_UP)
            if not (big_up or zt_recover):
                continue
            # 二次确认: 反转大阳的次日不能大跌（过滤单日脉冲/诱多）
            if j + 1 < len(state_df):
                nxt = state_df.iloc[j + 1]
                if (nxt.get('sh_chg', 0) or 0) < -0.5:
                    continue
            revs.append(j)
            break  # 每段只确认一次
    return revs


class IceBottomBacktest:
    """冰点抄底战法回测器"""

    def __init__(self, tdx: TdxReader = None):
        self.tdx = tdx or TdxReader()
        self.cal = TradingCalendar()
        self._name_map = {}
        self._load_name_map()
        self._stock_cache: Dict[str, pd.DataFrame] = {}
        self._causal = None

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
        code = str(code).zfill(6)
        return 'sh' if code.startswith('6') else 'sz'

    def get_universe(self) -> List[str]:
        """候选池: 主板 + 非ST + 年涨停≥10（前期强势股，冰点抄底目标）"""
        if os.path.exists(LIMIT_UP_POOL_FILE):
            try:
                with open(LIMIT_UP_POOL_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                pool = data.get('pool', [])
                codes = [s['code'] for s in pool
                         if str(s['code']).startswith(('60', '00'))
                         and 'ST' not in self._get_name(s['code'])]
                return sorted(set(codes))
            except Exception:
                pass
        return []

    def _load_cache(self, codes: List[str]):
        """预读候选池数据（尾部 260 行）"""
        for code in codes:
            try:
                df = self.tdx.read_daily(code, self._market(code))
                if df is None or df.empty or len(df) < 150:
                    continue
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
                df = df.tail(260).reset_index(drop=True)
                df['ma5'] = df['close'].rolling(5).mean()
                df['high120'] = df['high'].rolling(120).max()
                # 缠论底分型（整段算一次）
                df['_frac'] = chan.detect_fractals(df)
                df = df.set_index('trade_date')
                self._stock_cache[code] = df
            except Exception:
                continue

    def _oversold_candidates(self, d: date) -> List[Dict]:
        """反转确认日 D 选出超跌标的（用 D 收盘数据）"""
        cands = []
        for code, df in self._stock_cache.items():
            if d not in df.index:
                continue
            # 因果"前期强势"过滤
            if self._causal is not None and not self._causal.eligible(code, d):
                continue
            pos = df.index.get_loc(d)
            if pos < 120:
                continue
            row = df.iloc[pos]
            close = float(row['close'])
            high120 = float(row['high120'])
            if high120 <= 0:
                continue
            drop = (high120 - close) / high120
            if drop < OVERSOLD_DROP:
                continue
            # 股价上限（抄底要低价，高价超跌股易继续阴跌）
            if close > MAX_PRICE:
                continue
            # 缩量企稳: 反转前一周均量 < 60日最大量 × 0.6（卖盘释放，筹码沉淀）
            vol_prev_week = float(df['volume'].iloc[max(0, pos - 7):pos].mean())
            vol_60 = float(df['volume'].iloc[max(0, pos - 60):pos].max())
            stable = vol_60 > 0 and vol_prev_week < vol_60 * 0.6
            if not stable:
                continue
            # 底部特征: 站上 MA5 或 近5日有底分型
            ma5 = float(row['ma5'])
            above_ma5 = (not pd.isna(ma5)) and close > ma5
            frac_near = bool(df['_frac'].iloc[max(0, pos - 5):pos + 1].eq(-1).any())
            if not (above_ma5 or frac_near):
                continue
            cands.append({
                'code': code, 'name': self._get_name(code),
                'close': close, 'drop': drop,
                'high120': high120,
                'above_ma5': above_ma5, 'frac': frac_near,
                'date': d,
            })
        cands.sort(key=lambda x: x['drop'], reverse=True)
        return cands[:TOP_N]

    def _simulate(self, c: Dict, buy_date: date) -> Optional[Dict]:
        code = c['code']
        df = self._stock_cache.get(code)
        if df is None or buy_date not in df.index:
            return None
        pos = df.index.get_loc(buy_date)
        entry = float(df['close'].iloc[pos])  # 反转确认日收盘价入场
        if entry <= 0:
            return None
        highest = entry
        for k in range(MAX_HOLD):
            j = pos + 1 + k  # 次日开始持有
            if j >= len(df):
                break
            bar = df.iloc[j]
            o = float(bar['open']); h = float(bar['high'])
            lo = float(bar['low']); c_ = float(bar['close'])
            d = df.index[j]
            # 止损
            if lo <= entry * (1 - STOP):
                exit_price = min(entry * (1 - STOP), o)
                return self._mk(c, buy_date, d, entry, exit_price, '止损-6%', k + 1)
            # 反弹到前20日高点 → 止盈
            high20 = float(df['high'].iloc[max(0, j - 20):j + 1].max())
            if high20 > entry * 1.02 and c_ >= high20 * 0.995:
                return self._mk(c, buy_date, d, entry, c_, '反弹到压力位', k + 1)
            # 反弹 ≥ +8%
            if c_ >= entry * (1 + TP):
                return self._mk(c, buy_date, d, entry, c_, '反弹+8%止盈', k + 1)
            # 移动止盈
            highest = max(highest, c_)
            if highest >= entry * 1.03 and c_ <= highest * (1 - TRAIL):
                return self._mk(c, buy_date, d, entry, c_, '移动止盈', k + 1)
        # 到期
        j = pos + 1 + MAX_HOLD
        if j < len(df):
            c_ = float(df['close'].iloc[j])
            d = df.index[j]
            return self._mk(c, buy_date, d, entry, c_, '10天到期', MAX_HOLD)
        return None

    @staticmethod
    def _mk(c, buy_date, sell_date, entry, exit_price, reason, days):
        gross = (exit_price - entry) / entry if entry > 0 else 0
        net = gross - TOTAL_COST
        return {
            'code': c['code'], 'name': c['name'],
            'signal_date': c['date'].strftime('%Y-%m-%d'),
            'buy_date': buy_date.strftime('%Y-%m-%d'),
            'sell_date': sell_date.strftime('%Y-%m-%d'),
            'buy_price': round(entry, 2),
            'sell_price': round(exit_price, 2),
            'gross_ret': round(gross * 100, 2),
            'net_ret': round(net * 100, 2),
            'is_win': net > 0,
            'exit_reason': reason,
            'days_held': days,
            'drop': round(c['drop'] * 100, 1),
        }

    def run(self, state_df: pd.DataFrame, start: date, end: date,
            causal_universe=None) -> List[Dict]:
        """回测冰点抄底。state_df 需含 emotion/limit_up/up_count/sh_chg。

        causal_universe: 因果候选池（CausalUniverse）。传入时用逐日因果判定
            "前期强势"(近250日涨停≥10)，修复静态池幸存者偏差；None 用静态池。
        """
        # 反转确认日
        rev_idx = find_reversal_days(state_df)
        print(f'冰点反转确认日: {len(rev_idx)} 个')
        if not rev_idx:
            return []

        self._causal = causal_universe
        # 候选池 + 预读
        if causal_universe is not None:
            universe = sorted(c for c in causal_universe.codes
                              if str(c).startswith(('60', '00')) and 'ST' not in self._get_name(c))
            print(f'冰点候选池(因果): {len(universe)} 只，预读数据...')
        else:
            universe = self.get_universe()
            print(f'冰点候选池: {len(universe)} 只，预读数据...')
        self._load_cache(universe)

        # 每个反转日买入（缠论二买：反转确认日收盘入场）
        trades = []
        for i in rev_idx:
            d = state_df.iloc[i]['date']
            if isinstance(d, str):
                d = date.fromisoformat(d)
            cands = self._oversold_candidates(d)
            if not cands:
                continue
            print(f'  {d} 反转日 → 抄底 {len(cands)} 只')
            for c in cands:
                tr = self._simulate(c, d)  # 买入日 = 反转确认日，收盘价入场
                if tr:
                    trades.append(tr)
        return trades
