"""形态识别: 箱体突破 / W底 / N字回调"""
import pandas as pd
import numpy as np
from typing import Optional, Dict


def detect_box_breakout(df: pd.DataFrame, box_period: int = 40, break_pct: float = 0.03) -> Optional[Dict]:
    """识别底部箱体突破: 横盘震荡后放量突破箱体上沿"""
    if len(df) < box_period + 3:
        return None
    box_slice = df.iloc[-box_period-3:-3]
    recent = df.iloc[-3:]
    box_high = box_slice['high'].max()
    box_low = box_slice['low'].min()
    box_range = box_high - box_low
    if box_range / box_low < 0.15:  # 箱体振幅 < 15%
        return None
    breakout_close = recent['close'].iloc[-1]
    if breakout_close > box_high * (1 + break_pct):
        return {
            'pattern': '箱体突破',
            'box_high': box_high, 'box_low': box_low,
            'box_period': box_period,
            'breakout_price': breakout_close,
            'breakout_pct': (breakout_close - box_high) / box_high * 100
        }
    return None


def detect_w_bottom(df: pd.DataFrame, lookback: int = 60, tolerance: float = 0.03) -> Optional[Dict]:
    """识别W底: 两个低点接近，放量突破颈线"""
    if len(df) < lookback:
        return None
    seg = df.iloc[-lookback:]
    lows = seg['close'].rolling(20).min().dropna()
    if len(lows) < 40:
        return None
    # Use positional index to avoid DatetimeIndex dependency
    min_pos = int(lows.values.argmin())  # position within lows/since first valid
    min_val = lows.iloc[min_pos]
    # left lows must be at least 10 positions before the right low
    if min_pos < 10:
        return None
    left_lows = lows.iloc[:min_pos - 10]
    if left_lows.empty:
        return None
    left_min_val = left_lows.min()
    left_min_pos = int(left_lows.values.argmin())
    if abs(left_min_val - min_val) / min_val < tolerance:
        # neck = max close between left low and right low in seg
        # lows has rolling(20) offset: lows.iloc[i] maps to seg.iloc[i + 19]
        roll_offset = 19
        seg_left_pos = left_min_pos + roll_offset
        seg_right_pos = min_pos + roll_offset
        neck = seg['close'].iloc[seg_left_pos:seg_right_pos + 1].max()
        if seg['close'].iloc[-1] > neck:
            return {'pattern': 'W底', 'left_low': float(left_min_val), 'right_low': float(min_val), 'neck': float(neck)}
    return None


def detect_n_pattern(df: pd.DataFrame, lookback: int = 40) -> Optional[Dict]:
    """识别N字结构: 涨→缩量回调→重新放量拉升"""
    if len(df) < lookback:
        return None
    seg = df.iloc[-lookback:]
    close = seg['close'].values
    vol = seg['volume'].values
    # 找近20日(排除最后5根)高点后回调>3%，然后突破该高点
    # 排除最后5根是因为它们可能是突破K线本身
    search_start = max(0, len(close) - 20)
    search_end = len(close) - 5
    if search_start >= search_end:
        return None
    recent_high_idx = search_start + close[search_start:search_end].argmax()
    if recent_high_idx >= len(close) - 5:
        return None
    pullback_low = close[recent_high_idx:].min()
    pullback_pct = (close[recent_high_idx] - pullback_low) / close[recent_high_idx]
    if pullback_pct < 0.03:
        return None
    if close[-1] > close[recent_high_idx]:
        vol_before = vol[:recent_high_idx].mean()
        vol_after = vol[recent_high_idx:].mean()
        if vol_after < vol_before * 0.7:  # 缩量回调确认
            return {
                'pattern': 'N字结构',
                'high': float(close[recent_high_idx]),
                'pullback_low': float(pullback_low),
                'breakout_price': float(close[-1])
            }
    return None
