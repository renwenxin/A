"""明日突破预测 — 单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd


def _df(closes, volumes=None, highs=None, n_extra=0):
    n = len(closes)
    vols = volumes or [1000] * n
    hs = highs or [c * 1.02 for c in closes]
    return pd.DataFrame({
        'trade_date': pd.date_range('2026-01-01', periods=n + n_extra),
        'open': closes, 'high': hs, 'low': [c * 0.98 for c in closes],
        'close': closes, 'volume': vols,
    })


def test_compute_features_near():
    """贴压力位≤3% → near=True"""
    from ashare_review.tools.breakout_predict import compute_features
    closes = [10.0] * 30
    df = _df(closes)
    f = compute_features(df, pressure=10.2, limit_count=12)   # 距压力位 ~2%
    assert f['near'] is True
    assert f['dist_pct'] == 2.0
    f2 = compute_features(df, pressure=11.0, limit_count=12)  # 距压力位 ~9%
    assert f2['near'] is False


def test_score_weights():
    """near 权重最高；全特征组合分最高"""
    from ashare_review.tools.breakout_predict import (score_features,
                                                      SCORE_BASE, W_NEAR)
    base = score_features({'limit_count': 10, 'near': False, 'probe': False,
                           'ma_bull': False, 'vol_shrink': False, 'vol_up': False})
    assert base == SCORE_BASE
    near = score_features({'limit_count': 10, 'near': True, 'probe': False,
                           'ma_bull': False, 'vol_shrink': False, 'vol_up': False})
    assert near == SCORE_BASE + W_NEAR
    full = score_features({'limit_count': 20, 'near': True, 'probe': True,
                           'ma_bull': True, 'vol_shrink': True, 'vol_up': True})
    assert full >= near


def test_vol_shrink_only_no_bonus():
    """地量不 near 时不加分（校准: 单独为负）"""
    from ashare_review.tools.breakout_predict import score_features, SCORE_BASE
    s = score_features({'limit_count': 10, 'near': False, 'probe': False,
                        'ma_bull': False, 'vol_shrink': True, 'vol_up': False})
    assert s == SCORE_BASE


def test_predictor_predict_and_verify(tmp_path):
    """预测落台账 + 次日验证闭环"""
    import json
    from ashare_review.tools.breakout_predict import BreakoutPredictor, _DB_FILE
    import ashare_review.tools.breakout_predict as bp

    class FakeSP:
        class FakeState(dict):
            def __init__(self):
                super().__init__()
                self['watch'] = {
                    '600001': {'name': 'A', 'top_line': 10.2, 'limit_count': 18, 'close': 10.0},
                }
        def __init__(self):
            self._state = self.FakeState()
            self.tdx = None
    class FakeTdx:
        def read_daily(self, code, market):
            import pandas as pd
            closes = [10.0] * 30
            return pd.DataFrame({
                'trade_date': pd.date_range('2026-05-01', periods=30),
                'open': closes, 'high': [c * 1.02 for c in closes],
                'low': [c * 0.98 for c in closes], 'close': closes,
                'volume': [1000] * 30,
            })
    # monkeypatch 台账路径到 tmp
    db_tmp = str(tmp_path / 'bp_test.db')
    bp._DB_FILE = db_tmp
    pred = BreakoutPredictor(FakeSP())
    pred.tdx = FakeTdx()
    # 预测
    top = pred.predict(trade_date='2026-08-31')
    assert len(top) == 1 and top[0]['code'] == '600001'
    assert top[0]['score'] >= 55   # near+limit+probe
    import sqlite3
    conn = sqlite3.connect(db_tmp)
    rows = conn.execute('SELECT date, code, next_date, hit FROM predictions').fetchall()
    conn.close()
    assert rows and rows[0][0] == '2026-08-31' and rows[0][3] is None
    # 验证: 次日收盘 10.5 > pressure 10.2 → hit
    class FakeTdx2(FakeTdx):
        def read_daily(self, code, market):
            closes = [10.0] * 30 + [10.5]
            return pd.DataFrame({
                # 8-02 起 31 根 → 最后一根 = 2026-09-01（next_date）
                'trade_date': pd.date_range('2026-08-02', periods=31),
                'open': closes, 'high': [c * 1.02 for c in closes],
                'low': [c * 0.98 for c in closes], 'close': closes,
                'volume': [1000] * 31,
            })
    pred.tdx = FakeTdx2()
    # 台账 next_date = 下一交易日(2026-09-01); verify 用 _today_str 实际今天(2026-09-02)
    # 手动把 next_date 改成 <= 今天再 verify
    conn = sqlite3.connect(db_tmp)
    conn.execute("UPDATE predictions SET next_date='2026-09-01'")
    conn.commit(); conn.close()
    n = pred.verify_pending()
    assert n == 1
    conn = sqlite3.connect(db_tmp)
    hit = conn.execute('SELECT hit FROM predictions WHERE code=?', ('600001',)).fetchone()[0]
    conn.close()
    assert hit == 1
