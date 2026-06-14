import pandas as pd
import numpy as np
from ashare_review.analysis.pattern import detect_box_breakout, detect_w_bottom, detect_n_pattern


def make_ohlc(closes, vols=None):
    if vols is None:
        vols = [100000] * len(closes)
    return pd.DataFrame({
        'close': closes, 'open': closes, 'high': closes, 'low': closes,
        'volume': vols, 'trade_date': pd.date_range('2026-01-01', periods=len(closes), freq='B')
    })


def test_detect_box_breakout():
    # 40天横盘在9~11之间(振幅>15%) + 最后3天放量突破上沿
    # 箱体区域: 40根K线在8.8~10.8之间震荡(振幅>15%)
    np.random.seed(42)
    base = [9.8, 10.5, 10.2, 8.8, 10.8, 9.0, 10.3, 9.2, 10.6, 9.4] * 4  # 40根
    breakout = [11.5, 12.0, 12.8]  # 突破箱体上沿 10.8 * 1.03 = 11.124
    closes = base + breakout
    vols = [100000] * 40 + [500000, 800000, 1200000]
    df = make_ohlc(closes, vols)
    df['ma60'] = df['close'].rolling(60, min_periods=1).mean()
    df['ma89'] = df['close'].rolling(89, min_periods=1).mean()
    result = detect_box_breakout(df)
    assert result is not None
    assert result['pattern'] == '箱体突破'


def test_detect_n_pattern():
    # 涨一波→缩量回调→重新放量拉升突破前高
    # 44根K线: 前24根温和上涨(高量), 第25根见顶15.0,
    # 然后缩量回调到12.6, 之后回升突破15.0到15.8
    closes = [
        10.0, 10.3, 10.6, 10.9, 11.2, 11.5, 11.8, 12.1,
        12.3, 12.5, 12.7, 12.9, 13.1, 13.3, 13.5, 13.7,
        13.9, 14.0, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7,
        14.9, 15.0, 14.8, 14.3, 13.8, 13.2, 12.8, 12.6,
        12.8, 13.1, 13.4, 13.7, 14.0, 14.3, 14.6, 14.9,
        15.2, 15.4, 15.6, 15.8,
    ]
    # 前25根量高, 后19根量缩 (< 70%)
    vols = [1000000] * 25 + [300000] * 19
    df = make_ohlc(closes, vols)
    result = detect_n_pattern(df)
    assert result is not None
    assert result['pattern'] == 'N字结构'


def test_detect_w_bottom():
    # W底: 下跌→反弹→再跌(两低接近)→突破颈线
    # 构造65根K线, iloc[-60:]内形成W形态:
    #   左底~8.0 → 颈线~10.0 → 右底~7.92 → 突破颈线到11.0
    # 先构造后60根的W形态
    n = 60
    # 使用分段构造确保形状清晰
    closes = np.zeros(n)
    # 0-14: 高位下跌到左底8.0
    closes[0:15] = np.linspace(12, 8.0, 15)
    # 15-29: 反弹到颈线10.0
    closes[15:30] = np.linspace(8.0, 10.0, 15)
    # 30-44: 回落到右底7.92 (略低于左底, 成为全局最低, 同时< 3%公差)
    closes[30:45] = np.linspace(10.0, 7.92, 15)
    # 45-59: 突破颈线上涨到11.0
    closes[45:60] = np.linspace(7.92, 11.0, 15)
    # 前补齐5根高位(会被iloc[-60:]排除)
    closes = np.concatenate([[12.0] * 5, closes])
    df = make_ohlc(closes.tolist())
    result = detect_w_bottom(df)
    assert result is not None
    assert result['pattern'] == 'W底'
