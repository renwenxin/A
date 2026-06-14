# tests/test_volume.py
import pandas as pd
import numpy as np
from ashare_review.analysis.volume import detect_volume_cannon, classify_volume_price


def test_detect_volume_cannon():
    dates = pd.date_range('2026-01-01', periods=30, freq='B')
    # 前20根普通量(建立均线基准), 后接4根连续放量(>1.5倍均量), 再6根普通量
    vol = [100]*20 + [350, 400, 380, 350] + [100]*6
    close = [10.0]*20 + [10.5, 11.0, 11.5, 12.0] + [12.0]*6
    df = pd.DataFrame({
        'close': close, 'volume': [v*10000 for v in vol],
        'open': close, 'high': close, 'low': close
    }, index=dates)
    result = detect_volume_cannon(df)
    assert len(result) > 0
    assert result[0]['cannon_type'] == '复合炮'


def test_volume_price_classification():
    assert classify_volume_price(close_up=True, volume_up=True) == '放量上涨'
    assert classify_volume_price(close_up=True, volume_up=False) == '量价背离'
    assert classify_volume_price(close_up=False, volume_up=True) == '恐慌抛售'
    assert classify_volume_price(close_up=False, volume_up=False) == '无量阴跌'
