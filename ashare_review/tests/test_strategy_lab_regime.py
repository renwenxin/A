# ashare_review/tests/test_strategy_lab_regime.py
"""regime 当日化测试（mock market_state，不依赖真实 TDX 全市场扫描）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from ashare_review.strategy_lab import regime as lab_regime


class _FakeStateModule:
    """伪装 strategy_regime.market_state：load_state 返回构造的 state_df"""
    def __init__(self, state_df):
        self._df = state_df

    def load_state(self, start, end, cache_path=None, rebuild=False):
        return self._df.copy()


class _FakeRegimeModule:
    def __init__(self):
        self.calls = []

    def compute_regime(self, df):
        self.calls.append(len(df))
        df = df.copy()
        df['regime'] = '强势趋势'
        return df


@pytest.fixture()
def state_df():
    import pandas as pd
    return pd.DataFrame([{
        'date': '2026-09-08', 'sh_close': 3940.55, 'gz_close': 9800.0,
        'up_count': 3413, 'down_count': 2030, 'limit_up': 73, 'limit_down': 6,
        'sh_ma60': 3900.0, 'sh_trend_now': '上涨',
    }])


def test_get_regime_for_returns_tag(monkeypatch, state_df):
    fake_state = _FakeStateModule(state_df)
    fake_regime = _FakeRegimeModule()
    monkeypatch.setattr(lab_regime, 'market_state', fake_state)
    monkeypatch.setattr(lab_regime, 'regime_mod', fake_regime)
    tag = lab_regime.get_regime_for('2026-09-08')
    assert tag == '强势趋势'


def test_get_regime_for_returns_empty_when_date_missing(monkeypatch, state_df):
    fake_state = _FakeStateModule(state_df)
    fake_regime = _FakeRegimeModule()
    monkeypatch.setattr(lab_regime, 'market_state', fake_state)
    monkeypatch.setattr(lab_regime, 'regime_mod', fake_regime)
    assert lab_regime.get_regime_for('2020-01-01') == ''
