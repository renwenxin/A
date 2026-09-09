# ashare_review/tests/test_strategy_lab_collectors.py
"""三个 collector 契约测试（mock 依赖，不碰真实网络/TDX 扫描）"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from ashare_review.strategy_lab.collectors import (
    collect_breakout_v3, collect_one2, collect_boll_v9,
    COLLECTOR_IDS,
)


class _FakeTdx:
    def __init__(self, snap=None):
        self.snap = snap or {'amount_yi': 3.0, 'vol_ratio': 1.2, 'seal_ratio': None,
                             'open_px': 10, 'high_px': 10.5, 'low_px': 9.8,
                             'close_px': 10.3, 'sector': '半导体'}

    def read_daily(self, code, market):
        raise FileNotFoundError(code)   # snapshot 层应吞掉并继续


class _FakeAk:
    pass


class _FakePredictor:
    def verify_pending(self): return 0

    def predict(self, trade_date=None, top_n=10, persist=True):
        return [{'code': '600001', 'name': 'A', 'score': 80.0,
                 'close': 10.3, 'pressure': 10.6, 'dist_pct': 2.5,
                 'features': {'near': True}, 'reasons': ['贴线蓄势']}] * top_n


def _patch(monkeypatch, kind):
    if kind == 'breakout':
        # collector 用函数内延迟 import → patch 真实定义位置
        monkeypatch.setattr('ashare_review.tools.breakout_predict.BreakoutPredictor',
                            lambda *a, **k: _FakePredictor())
        import ashare_review.strategy_lab.collectors.breakout_v3 as m
        monkeypatch.setattr(m, 'enrich_stock',
                            lambda code, tdx, seal_map=None: {'amount_yi': 3.0,
                                                              'vol_ratio': 1.2, 'seal_ratio': None,
                                                              'open_px': 10, 'high_px': 10.5,
                                                              'low_px': 9.8, 'close_px': 10.3,
                                                              'sector': '半导体'})
    elif kind == 'one2':
        import ashare_review.strategy_lab.collectors.one2 as m
        monkeypatch.setattr(m, 'enrich_stock', lambda code, tdx, seal_map=None: {
            'amount_yi': 3.0, 'vol_ratio': 1.2, 'seal_ratio': 1.2,
            'open_px': 10, 'high_px': 10.5, 'low_px': 9.8, 'close_px': 10.3,
            'sector': '半导体'})
    elif kind == 'boll':
        monkeypatch.setattr('ashare_review.analysis.boll_v9.scan_universe',
                            lambda trade_date=None, tdx=None, top_n=None, industry_map=None: [
                                {'code': '600002', 'name': 'B', 'sector': '半导体',
                                 'score': 20.0, 'close': 9.5, 'dist_to_lb': -2.0,
                                 'rsi': 30.0, 'mfi': 25.0}])


def test_collector_ids():
    assert 'breakout_v3' in COLLECTOR_IDS and 'one2' in COLLECTOR_IDS and 'boll_v9' in COLLECTOR_IDS


def test_breakout_collect_top10_rank(monkeypatch):
    _patch(monkeypatch, 'breakout')
    items = collect_breakout_v3('2026-09-08', _FakeTdx(), _FakeAk())
    assert len(items) == 10
    assert [i['rank'] for i in items] == list(range(1, 11))
    extra = json.loads(items[0]['extra'])
    assert extra['pressure'] == 10.6 and extra['score'] == 80.0


def test_one2_collect_reads_report_picks(monkeypatch):
    _patch(monkeypatch, 'one2')
    report = {'sentiment': {'picks': [
        {'code': f'6000{i:02d}', 'name': f'N{i}', 'score': 9, 'reasons': ['r'],
         'seal_ratio': 1.2, 'sector_name': '半导体'}
        for i in range(1, 11)]}}
    items = collect_one2('2026-09-08', _FakeTdx(), _FakeAk(), report_payload=report)
    assert len(items) == 10
    assert items[0]['rank'] == 1


def test_one2_collect_missing_report_raises(monkeypatch):
    _patch(monkeypatch, 'one2')
    with pytest.raises(RuntimeError):
        collect_one2('2026-09-08', _FakeTdx(), _FakeAk(), report_payload=None)


def test_boll_collect_returns_items(monkeypatch):
    _patch(monkeypatch, 'boll')
    items = collect_boll_v9('2026-09-08', _FakeTdx(), _FakeAk())
    assert len(items) == 1
    extra = json.loads(items[0]['extra'])
    assert extra['score'] == 20.0
