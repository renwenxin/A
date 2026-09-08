# ashare_review/tests/test_strategy_lab_store.py
"""strategy_lab 存储层测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from ashare_review.strategy_lab.store import LabStore


def _row(**over):
    base = {
        'trade_date': '2026-09-08', 'strategy': 'one2', 'rank': 1,
        'code': '600001', 'name': 'A', 'sector': '电子', 'regime': '强势趋势',
        'amount_yi': 1.0, 'vol_ratio': 1.0, 'seal_ratio': None,
        'gap_pct': None, 'open_px': 1, 'high_px': 1, 'low_px': 1,
        'close_px': 10.0, 'next_return': None, 'extra': '{}',
    }
    base.update(over)
    return base


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


def test_data_dir_points_to_ashare_review_data():
    """默认 DB 目录必须是 ashare_review/data（两层 dirname），不能漂到项目根 data/。

    源文件位于 ashare_review/strategy_lab/store.py：
      1 层 dirname = ashare_review/strategy_lab
      2 层 dirname = ashare_review            ← 正确（再拼 data = ashare_review/data）
      3 层 dirname = 项目根                   ← 回归 bug（data/strategy_lab.db 落在根目录）
    """
    import ashare_review.strategy_lab.store as m
    expect = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(m.__file__))), 'data')
    assert os.path.abspath(m._DATA_DIR) == os.path.abspath(expect)


def test_default_db_path_joins_data_dir(monkeypatch, tmp_path):
    """不传 db_path 时，用模块 _DATA_DIR 拼 strategy_lab.db（monkeypatch 避免建真实库）。"""
    import ashare_review.strategy_lab.store as m
    monkeypatch.setattr(m, '_DATA_DIR', str(tmp_path))
    s = m.LabStore()
    assert s.db_path == str(tmp_path / 'strategy_lab.db')


def test_empty_store_returns_empty_state(store):
    """空表: pending_status 返回 {}、recorded_dates 返回 []"""
    assert store.pending_status() == {}
    assert store.recorded_dates() == []


def test_backfill_missing_row_is_silent(store):
    """backfill 对不存在行静默返回（不抛异常，不建行）"""
    store.backfill('2026-09-08', code='600001', strategy='one2',
                   gap_pct=2.5, next_return=3.0)
    assert store.rows('2026-09-08') == []
    assert store.pending_status() == {}


def test_upsert_does_not_overwrite_backfilled_gap_or_return(store):
    """upsert 不覆盖已回填的 gap_pct/next_return（回填专用字段保护）。

    回归点（minor-1）：record 重跑时 collectors 默认 gap_pct=None，
    upsert 若把 gap_pct 也纳入 DO UPDATE SET，会把已回填的竞价涨幅抹成 None。
    """
    store.upsert_row(_row())
    store.backfill('2026-09-08', code='600001', strategy='one2',
                   gap_pct=2.5, next_return=3.0)
    # 重跑 record：默认行 gap_pct=None / next_return=None，快照字段有变化
    store.upsert_row(_row(close_px=10.6, rank=2))
    r = store.rows('2026-09-08')[0]
    assert r['gap_pct'] == 2.5          # 不被 None 抹掉
    assert r['next_return'] == 3.0      # 不被 None 抹掉
    assert r['close_px'] == 10.6        # 普通快照列仍被覆盖
    assert r['rank'] == 2

