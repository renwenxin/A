# ashare_review/tests/test_strategy_lab_store.py
"""strategy_lab 存储层测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from ashare_review.strategy_lab.store import LabStore


@pytest.fixture()
def store(tmp_path):
    return LabStore(db_path=str(tmp_path / 'lab.db'))


def test_upsert_is_idempotent_by_date_strategy_code(store):
    row = {
        'trade_date': '2026-09-08', 'strategy': 'breakout_v3', 'rank': 1,
        'code': '600001', 'name': 'A', 'sector': '电子', 'regime': '强势趋势',
        'amount_yi': 12.3, 'vol_ratio': 1.5, 'seal_ratio': None,
        'gap_pct': None, 'open_px': 10.0, 'high_px': 10.5, 'low_px': 9.9,
        'close_px': 10.4, 'next_return': None, 'extra': '{}',
    }
    store.upsert_row(row)
    store.upsert_row(dict(row, close_px=99.0))   # 同日同策略同码 → 覆盖
    rows = store.rows('2026-09-08', strategy='breakout_v3')
    assert len(rows) == 1
    assert rows[0]['close_px'] == 99.0


def test_rows_filter_and_rank_order(store):
    store.upsert_row({'trade_date': '2026-09-08', 'strategy': 'one2', 'rank': 2,
                      'code': '600002', 'name': 'B', 'sector': '', 'regime': '强势趋势',
                      'amount_yi': 1, 'vol_ratio': 1, 'seal_ratio': None, 'gap_pct': None,
                      'open_px': 1, 'high_px': 1, 'low_px': 1, 'close_px': 1,
                      'next_return': None, 'extra': '{}'})
    store.upsert_row({'trade_date': '2026-09-08', 'strategy': 'one2', 'rank': 1,
                      'code': '600003', 'name': 'C', 'sector': '', 'regime': '强势趋势',
                      'amount_yi': 1, 'vol_ratio': 1, 'seal_ratio': None, 'gap_pct': None,
                      'open_px': 1, 'high_px': 1, 'low_px': 1, 'close_px': 1,
                      'next_return': None, 'extra': '{}'})
    rows = store.rows('2026-09-08', strategy='one2')
    assert [r['rank'] for r in rows] == [1, 2]
    assert store.rows('2026-09-08')  # 无 strategy 过滤 = 全部


def test_backfill_only_fills_empty(store):
    store.upsert_row({'trade_date': '2026-09-08', 'strategy': 'one2', 'rank': 1,
                      'code': '600003', 'name': 'C', 'sector': '', 'regime': '强势趋势',
                      'amount_yi': 1, 'vol_ratio': 1, 'seal_ratio': None,
                      'gap_pct': None, 'open_px': 1, 'high_px': 1, 'low_px': 1,
                      'close_px': 10.0, 'next_return': None, 'extra': '{}'})
    store.backfill('2026-09-08', code='600003', strategy='one2',
                   gap_pct=2.5, next_return=3.0)
    r = store.rows('2026-09-08')[0]
    assert r['gap_pct'] == 2.5 and r['next_return'] == 3.0
    # 再次回填不同值 → 不变（只填空）
    store.backfill('2026-09-08', code='600003', strategy='one2',
                   gap_pct=9.9, next_return=9.9)
    r = store.rows('2026-09-08')[0]
    assert r['gap_pct'] == 2.5 and r['next_return'] == 3.0


def test_dates_and_pending_counts(store):
    store.upsert_row({'trade_date': '2026-09-08', 'strategy': 'one2', 'rank': 1,
                      'code': '600003', 'name': 'C', 'sector': '', 'regime': '强势趋势',
                      'amount_yi': 1, 'vol_ratio': 1, 'seal_ratio': None,
                      'gap_pct': None, 'open_px': 1, 'high_px': 1, 'low_px': 1,
                      'close_px': 10.0, 'next_return': None, 'extra': '{}'})
    assert store.recorded_dates() == ['2026-09-08']
    s = store.pending_status()
    assert s['2026-09-08']['gap_pending'] == 1 and s['2026-09-08']['ret_pending'] == 1
