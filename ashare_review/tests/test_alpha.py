"""Alpha 因子模块单元测试"""
import pytest
import pandas as pd
import numpy as np
from ashare_review.alpha.base import AlphaFactor, FactorReport
from ashare_review.alpha.registry import FactorRegistry, get_registry
from ashare_review.alpha.factors.gtja191.momentum import (
    GTJA_Momentum_5D, GTJA_MA_Deviation, GTJA_RSI, register_gtja_momentum,
)
from ashare_review.alpha.factors.custom.limit_up import LimitUpGene
from ashare_review.alpha.factors.custom.ma_system import MABullAlignment


def make_test_df(n=200):
    """生成合成日线数据"""
    np.random.seed(42)
    dates = pd.date_range('2025-01-01', periods=n, freq='B')
    close = 10 + np.cumsum(np.random.randn(n) * 0.2) + 5
    df = pd.DataFrame({
        'open': close - np.random.rand(n) * 0.5,
        'high': close + np.random.rand(n) * 0.8,
        'low': close - np.random.rand(n) * 0.8,
        'close': close,
        'volume': np.random.randint(10000, 100000, n),
    }, index=dates)
    return df


class TestFactorRegistry:
    def test_register_and_retrieve(self):
        r = FactorRegistry()
        f = GTJA_Momentum_5D()
        r.register(f)
        assert r.count == 1
        assert r.get('GTJA_001') is f

    def test_list_by_zoo(self):
        r = FactorRegistry()
        register_gtja_momentum(r)
        gtja = r.list_by_zoo('gtja191')
        assert len(gtja) >= 6
        for f in gtja:
            assert f.zoo == 'gtja191'

    def test_global_registry_auto_loads(self):
        r = get_registry()
        assert r.count >= 9  # Default registration on first call


class TestFactors:
    def test_momentum_5d(self):
        df = make_test_df()
        f = GTJA_Momentum_5D()
        series = f.calculate(df)
        assert len(series) == len(df)
        assert series.iloc[-1] is not None

    def test_ma_deviation(self):
        df = make_test_df()
        f = GTJA_MA_Deviation()
        series = f.calculate(df)
        assert len(series) == len(df)
        # 前19个应该是NaN
        assert pd.isna(series.iloc[0])

    def test_rsi_range(self):
        df = make_test_df()
        f = GTJA_RSI()
        series = f.calculate(df)
        valid = series.dropna()
        if len(valid) > 0:
            assert valid.min() >= 0
            assert valid.max() <= 100

    def test_limit_up_gene(self):
        df = make_test_df(n=400)  # 需要超过250日窗口
        f = LimitUpGene()
        series = f.calculate(df)
        valid = series.dropna()
        assert len(valid) > 0
        assert valid.min() >= 0
        assert valid.max() <= 1

    def test_ma_bull_alignment(self):
        df = make_test_df()
        # 需要提前计算均线
        for p in [5, 10, 20, 60]:
            df[f'ma{p}'] = df['close'].rolling(p).mean()
        f = MABullAlignment()
        series = f.calculate(df)
        valid = series.dropna()
        assert valid.max() <= 1.0
