"""量价分析 + 成交量复合炮识别"""
import pandas as pd
import numpy as np
from typing import List, Dict


def classify_volume_price(close_up: bool, volume_up: bool) -> str:
    """量价关系四分类"""
    if close_up and volume_up:
        return '放量上涨'
    elif close_up and not volume_up:
        return '量价背离'
    elif not close_up and volume_up:
        return '恐慌抛售'
    elif not close_up and not volume_up:
        return '无量阴跌'


def detect_shrink_consolidation(df: pd.DataFrame, window: int = 10, shrink_ratio: float = 1/3) -> bool:
    """检测缩量横盘：当前成交量缩到前期高量的1/3以下"""
    if len(df) < window * 2:
        return False
    recent_vol = df['volume'].iloc[-window:].mean()
    prior_vol_max = df['volume'].iloc[-window*2:-window].max()
    return prior_vol_max > 0 and recent_vol / prior_vol_max < shrink_ratio


def detect_volume_cannon(df: pd.DataFrame, vol_ma_period: int = 20, burst_multiplier: float = 1.5) -> List[Dict]:
    """识别成交量复合炮: 连续3根及以上放量柱(>1.5倍均量)"""
    if len(df) < vol_ma_period + 5:
        return []
    df = df.copy()
    df['vol_ma'] = df['volume'].rolling(window=vol_ma_period).mean()
    df['is_burst'] = df['volume'] > df['vol_ma'] * burst_multiplier
    results = []
    i = len(df) - 1
    while i >= 0:
        if df['is_burst'].iloc[i]:
            start = i
            while start > 0 and df['is_burst'].iloc[start-1]:
                start -= 1
            count = i - start + 1
            if count >= 3:
                cannon_type = '复合炮' if count >= 4 else '炮'
                results.append({
                    'start_idx': start, 'end_idx': i,
                    'count': count, 'cannon_type': cannon_type,
                    'start_date': str(df.index[start]),
                    'end_date': str(df.index[i]),
                    'max_volume': int(df['volume'].iloc[start:i+1].max()),
                })
            i = start - 1
        else:
            i -= 1
    return results


def detect_volume_breakout(df: pd.DataFrame, lookback: int = 60) -> bool:
    """当日是否为近期最大量(倍量突破)"""
    if len(df) < lookback:
        return False
    today_vol = df['volume'].iloc[-1]
    prior_max = df['volume'].iloc[-lookback:-1].max()
    return today_vol > prior_max


def volume_price_label(df: pd.DataFrame) -> pd.DataFrame:
    """给每行赋予量价标签"""
    df = df.copy()
    df['close_up'] = df['close'].diff() > 0
    df['volume_up'] = df['volume'].diff() > 0
    df['vp_label'] = df.apply(lambda r: classify_volume_price(r['close_up'], r['volume_up']), axis=1)
    return df
