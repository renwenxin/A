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


def test_get_regime_for_returns_tag(monkeypatch, tmp_path, state_df):
    fake_state = _FakeStateModule(state_df)
    fake_regime = _FakeRegimeModule()
    monkeypatch.setattr(lab_regime, 'market_state', fake_state)
    monkeypatch.setattr(lab_regime, 'regime_mod', fake_regime)
    # 隔离缓存文件到 tmp_path：若开发机已有 data/strategy_lab/market_state.csv
    # 会走真实快路径，覆盖路径不确定；指向空 tmp 文件保证走 mock 的 load_state。
    monkeypatch.setattr(lab_regime, '_STATE_CACHE', str(tmp_path / 'market_state.csv'))
    tag = lab_regime.get_regime_for('2026-09-08')
    assert tag == '强势趋势'


def test_get_regime_for_returns_empty_when_date_missing(monkeypatch, tmp_path, state_df):
    fake_state = _FakeStateModule(state_df)
    fake_regime = _FakeRegimeModule()
    monkeypatch.setattr(lab_regime, 'market_state', fake_state)
    monkeypatch.setattr(lab_regime, 'regime_mod', fake_regime)
    # 同上：隔离 _STATE_CACHE，避免真实缓存文件干扰 mock 路径
    monkeypatch.setattr(lab_regime, '_STATE_CACHE', str(tmp_path / 'market_state.csv'))
    assert lab_regime.get_regime_for('2020-01-01') == ''


class _LaggingStateModule:
    """模拟真实 load_state：缓存存在且非 rebuild 时返回旧 df；rebuild 才返回新 df。

    真实 market_state.load_state（market_state.py:23-38）在 cache_path 存在且
    rebuild=False 时直接读 CSV 返回、无视 start/end —— 本 fake 复刻该行为，
    避免旧实现"没传 rebuild 也能拿到新数据"的假阳性。
    """
    def __init__(self, old_df, new_df):
        self.old_df, self.new_df = old_df, new_df
        self.calls = []

    def load_state(self, start, end, cache_path=None, rebuild=False):
        self.calls.append({'start': start, 'end': end, 'rebuild': rebuild})
        return (self.new_df.copy() if rebuild else self.old_df.copy())


def test_rebuild_when_cache_lags(monkeypatch, tmp_path, state_df):
    """lab 缓存落后于 target（跨天记录第二天的典型场景）→ 必须显式 rebuild=True 全量重建。"""
    import csv as _csv
    import pandas as pd

    old = state_df.copy()                 # 旧缓存只到 2026-09-07
    old.loc[0, 'date'] = '2026-09-07'
    new = state_df.copy()                 # 新数据到 2026-09-08
    fake = _LaggingStateModule(old, new)
    monkeypatch.setattr(lab_regime, 'market_state', fake)
    monkeypatch.setattr(lab_regime, 'regime_mod', _FakeRegimeModule())
    monkeypatch.setattr(lab_regime, '_STATE_CACHE', str(tmp_path / 'market_state.csv'))
    # 预置"存在但落后"的缓存文件（模拟已写到 9-7，目标 9-8）
    with open(tmp_path / 'market_state.csv', 'w', encoding='utf-8', newline='') as f:
        w = _csv.writer(f)
        w.writerow(['date', 'sh_close'])
        w.writerow(['2026-09-07', 3900.0])
    tag = lab_regime.get_regime_for('2026-09-08')
    assert tag == '强势趋势'
    assert any(c['rebuild'] is True and str(c['end']) == '2026-09-08' for c in fake.calls)
