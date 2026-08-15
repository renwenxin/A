"""尾盘选股筛选器 — 超跌选股法 + 平台突破选股法 (BV15xgn6GEnA)

核心纪律:
- 14:50–14:55 尾盘临收盘选股,今天尾盘买、明天冲高就卖
- 收盘必须站上当日分时均线(VWAP = amount/volume),差一分都不行
- 量能是核心确认,量不够坚决不做

战法一 · 超跌选股法:
  1. 区间跌幅要大(近 drawdown_window 日高点回撤 > drawdown_min)
  2. 日内涨幅 > 4%
  3. 收盘价 > 当日 VWAP
  加分项: 量能突破 / 主力资金 / 可叠加十字星战法(次日放量1.5倍)

战法二 · 平台突破选股法:
  1. 收盘站上箱体上沿(近 box_window 日最高价)
  2. 放量突破(volume > vol_ratio_min × 前5日均量)
  3. 前期是平台整理(振幅 < platform_width_max)
  排除: 上吊线(冲高回落长上影) / 位置过高只降级不排除
"""
from typing import List
import numpy as np
import pandas as pd

from .base import BaseScreener
from ..data.models import ScreeningResult


class TailSessionScreener(BaseScreener):
    """尾盘选股: 超跌低吸 + 平台突破,收盘价站在分时均线之上为铁律"""

    name = '尾盘选股'

    def __init__(self, tdx=None, ak_fetcher=None, **params):
        super().__init__(tdx, ak_fetcher)
        # ── 超跌选股参数 ──
        self.drawdown_window = int(params.get('drawdown_window', 60))
        self.drawdown_min = float(params.get('drawdown_min', 25.0))      # 区间回撤≥25%
        self.daily_gain_min = float(params.get('daily_gain_min', 4.0))   # 日内涨幅>4%
        # ── 平台突破参数 ──
        self.box_window = int(params.get('box_window', 60))
        self.vol_ratio_min = float(params.get('vol_ratio_min', 1.5))     # 放量≥1.5倍
        self.platform_width_max = float(params.get('platform_width_max', 18.0))  # 平台振幅≤18%
        # ── 通用过滤 ──
        self.exclude_shadow = params.get('exclude_shadow', True)         # 排除上吊线
        self.high_pos_mark = float(params.get('high_pos_mark', 80.0))    # 距250日低点涨幅>80%标记高位

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def screen(self, **kwargs) -> List[ScreeningResult]:
        max_stocks = int(kwargs.get('max_stocks', 0))   # 0=全市场,>0=快速自测
        stocks = [(c, m) for c, m in self.tdx.list_stocks()
                  if m != 'bj' and self._is_a_stock(c)]
        if max_stocks:
            stocks = stocks[:max_stocks]
        results = []
        for code, market in stocks:
            try:
                df = self.tdx.read_daily(code, market)
            except Exception:
                continue
            if len(df) < max(self.box_window, self.drawdown_window) + 20:
                continue
            name = self._get_name(code) or code
            if name.startswith(('ST', '*ST', 'SST', 'S*ST', 'NST')):
                continue

            score, reasons, detail = self._evaluate(df, code)
            if score > 0:
                results.append(ScreeningResult(
                    code=code, name=name, strategy=self.name,
                    score=min(round(score), 100), reasons=reasons,
                    detail=detail))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:100]

    @staticmethod
    def _is_a_stock(code: str) -> bool:
        """纯代码判断A股: 排除北交所(8/4开头)"""
        return code[0] in ('0', '3', '6') and code[0] != '4'

    # ------------------------------------------------------------------
    # 单股评估
    # ------------------------------------------------------------------
    def _evaluate(self, df: pd.DataFrame, code: str) -> tuple:
        if len(df) < 2:
            return 0, [], {}
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        close = float(latest['close'])
        prev_close = float(prev['close'])
        if prev_close <= 0 or close <= 0:
            return 0, [], {}

        detail = {'code': code, 'close': close}
        reasons = []
        score = 0.0

        # 当日 VWAP(分时均线) = 成交额/成交量
        vwap = self._vwap(latest)
        if vwap <= 0:
            return 0, [], {}
        detail['vwap'] = round(vwap, 3)
        above_vwap = close > vwap
        detail['above_vwap'] = above_vwap

        daily_gain = (close - prev_close) / prev_close * 100
        detail['daily_gain'] = round(daily_gain, 2)

        # ── 战法一: 超跌选股法 ──
        over = self._oversold_signal(df, latest, close, daily_gain, above_vwap)
        if over:
            dd = over['drawdown']
            score += over['score']
            reasons.append(f'超跌: 近{self.drawdown_window}日回撤{dd:.1f}%·今涨{daily_gain:.1f}%·站上分时均线')
            detail.update(over)
            detail['signal'] = 'oversold'

        # ── 战法二: 平台突破选股法 ──
        plat = self._platform_signal(df, latest, prev, close, above_vwap)
        if plat:
            score += plat['score']
            reasons.append(f'平台突破: 放量{plat["vol_ratio"]:.1f}倍·站上箱体·无上吊线')
            detail.update(plat)
            if 'signal' in detail:
                detail['signal'] = 'oversold+platform'
            else:
                detail['signal'] = 'platform'

        if score > 0:
            detail['sector'] = self._get_sector(code)
        return score, reasons, detail

    def _vwap(self, bar) -> float:
        amt = float(bar['amount'])
        vol = float(bar['volume'])
        if vol <= 0 or not np.isfinite(amt):
            return 0.0
        return amt / vol

    # ── 战法一: 超跌 ──
    def _oversold_signal(self, df, latest, close, daily_gain, above_vwap):
        window = self.drawdown_window
        window_high = float(df['high'].iloc[-window:].max())
        if window_high <= 0:
            return None
        drawdown = (close - window_high) / window_high * 100  # 负值=回撤
        if drawdown > -self.drawdown_min:
            return None
        if daily_gain < self.daily_gain_min:
            return None
        if not above_vwap:
            return None

        # 加分项: 放量(量能突破)
        vol_ratio = self._vol_ratio(df)
        score = 60.0
        if vol_ratio >= 2.0:
            score += 15
        elif vol_ratio >= 1.5:
            score += 8
        return {
            'score': score, 'drawdown': drawdown,
            'vol_ratio': round(vol_ratio, 2),
            'signal_oversold': True,
        }

    # ── 战法二: 平台突破 ──
    def _platform_signal(self, df, latest, prev, close, above_vwap):
        if len(df) < self.box_window + 1:
            return None

        # 箱体上沿 = 前 box_window 日最高收盘(不含当日)
        box_top = float(df['close'].iloc[-self.box_window-1:-1].max())
        if box_top <= 0:
            return None
        if close <= box_top:   # 收盘必须站上突破点
            return None

        # 平台整理: 前 box_window 日振幅 < 阈值(横盘而非单边涨)
        win = df.iloc[-self.box_window-1:-1]
        platform_low = float(win['low'].min())
        platform_high = float(win['high'].max())
        if platform_low <= 0:
            return None
        width = (platform_high - platform_low) / platform_low * 100
        if width > self.platform_width_max:
            return None

        # 放量
        vol_ratio = self._vol_ratio(df)
        if vol_ratio < self.vol_ratio_min:
            return None

        # 排除上吊线(冲高回落长上影: 上影≥2.5倍实体 且 上影幅度≥3%)
        if self.exclude_shadow and self._is_shooting_star(latest, prev):
            return None

        score = 65.0
        # 位置过滤: 距250日低点涨幅过大 → 高位,只轻仓(降级不加分)
        high_pos = self._position_score(df, close)
        if high_pos > self.high_pos_mark:
            score += 10   # 符合但高位,给较低分(轻仓档)
        else:
            score += 20
        return {
            'score': score, 'box_top': round(box_top, 2),
            'platform_width': round(width, 2),
            'vol_ratio': round(vol_ratio, 2),
            'rise_from_low': round(high_pos, 1),
            'signal_platform': True,
        }

    def _vol_ratio(self, df) -> float:
        """当日量 / 前5日均量"""
        vol_ma5 = float(df['volume'].iloc[-6:-1].mean())
        cur_vol = float(df['volume'].iloc[-1])
        if vol_ma5 <= 0:
            return 0.0
        return cur_vol / vol_ma5

    @staticmethod
    def _is_shooting_star(latest, prev) -> bool:
        """上吊线/射击之星: 上影线≥2.5倍实体 且 上影幅度(相对收盘)≥3%"""
        open_p = float(latest['open'])
        high = float(latest['high'])
        close = float(latest['close'])
        body = abs(close - open_p)
        upper_shadow = high - max(open_p, close)
        if body <= 0 or close <= 0:
            return False
        return (upper_shadow >= 2.5 * body) and (upper_shadow / close * 100 >= 3.0)

    @staticmethod
    def _position_score(df, close) -> float:
        """距250日低点涨幅(%)"""
        if len(df) < 60:
            return 0.0
        low_250 = float(df['low'].iloc[-250:].min())
        if low_250 <= 0:
            return 0.0
        return (close - low_250) / low_250 * 100
