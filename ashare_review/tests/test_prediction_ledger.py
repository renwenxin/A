"""预测台账单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- Task 1: 判定引擎 ----------

def test_grade_pick_zt_priority():
    from ashare_review.prediction_ledger.validate import grade_pick
    assert grade_pick(20.0, is_zt=True) == 'zt'      # 涨停优先
    assert grade_pick(1.5, is_zt=True) == 'zt'       # 即使涨幅小也判涨停


def test_grade_pick_boundaries():
    from ashare_review.prediction_ledger.validate import grade_pick
    assert grade_pick(3.0) == 'up3'                   # 正好 3% → up3
    assert grade_pick(2.99) == 'up'                   # <3% → up
    assert grade_pick(0.0) == 'up'                    # 平盘 → up
    assert grade_pick(-2.99) == 'flat'                # -3% 以内 → flat
    assert grade_pick(-3.0) == 'down'                 # 正好 -3% → down
    assert grade_pick(-5.0) == 'down'


def test_grade_cycle_boundaries():
    from ashare_review.prediction_ledger.validate import grade_cycle
    assert grade_cycle(100, 110) == 'up'              # r=1.1 → up
    assert grade_cycle(100, 109) == 'flat'            # r=1.09 → flat
    assert grade_cycle(100, 90) == 'down'             # r=0.9 → down
    assert grade_cycle(100, 91) == 'flat'             # r=0.91 → flat
    assert grade_cycle(100, 100) == 'flat'
    assert grade_cycle(0, 10) is None                 # 当日涨停数为 0 无法判定


def test_grade_auction_boundaries():
    from ashare_review.prediction_ledger.validate import grade_auction
    assert grade_auction(1.5) == 'high'               # ≥1.5% → high
    assert grade_auction(1.49) == 'flat'
    assert grade_auction(-0.5) == 'low'               # ≤-0.5% → low
    assert grade_auction(-0.49) == 'flat'
    assert grade_auction(0.0) == 'flat'


def test_hit_for_all_types():
    from ashare_review.prediction_ledger.validate import hit_for
    # picks：zt/up3 命中
    assert hit_for('picks', None, 'zt') == 1
    assert hit_for('picks', None, 'up3') == 1
    assert hit_for('picks', None, 'up') == 0
    assert hit_for('picks', None, 'down') == 0
    # cycle：方向一致命中
    assert hit_for('cycle', 'up', 'up') == 1
    assert hit_for('cycle', 'up', 'down') == 0
    assert hit_for('cycle', 'flat', 'flat') == 1
    # auction：方向一致命中
    assert hit_for('auction', 'high', 'high') == 1
    assert hit_for('auction', 'high', 'low') == 0
    # 无法判定
    assert hit_for('picks', None, None) is None
    assert hit_for('cycle', None, 'up') is None
