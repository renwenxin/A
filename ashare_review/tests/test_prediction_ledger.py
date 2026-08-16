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
    assert grade_pick(-3.0) == 'flat'                 # 正好 -3% → flat（-3%~0 区间含边界）
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


# ---------- Task 2: daily.py 结构化字段 ----------

def _lu(code='600001', name='测试', t='10:00', consecutive=1, is_first=True,
        is_seal=True, is_broken=False, seal_amount=1000.0, turnover=5000.0,
        cap=30.0, board_type='换手板', close=10.0):
    from ashare_review.data.models import LimitUpInfo
    return LimitUpInfo(code=code, name=name, limit_up_time=t, seal_amount=seal_amount,
                       turnover=turnover, float_market_cap=cap, consecutive=consecutive,
                       is_first=is_first, is_seal=is_seal, is_broken=is_broken,
                       board_type=board_type, close_price=close)


def _make_cycle_report():
    from ashare_review.report.daily import DailyReport
    return DailyReport(tdx=None, ak_fetcher=None)  # 这两个方法不触网


def test_cycle_next_bias_all_stages():
    rep = _make_cycle_report()
    cases = [
        # (limit_ups 构造参数, 期望 stage, 期望 next_bias)
        (100, 80, 6, 30, '高潮末期', 'down'),   # total≥100 封板率80% 高度6 一字30%
        (100, 80, 6, 20, '高潮期', 'flat'),     # 同上但一字<30%
        (60, 45, 5, 5, '发酵期', 'up'),         # ≥50 封板率75% 高度5
        (40, 26, 4, 3, '启动期', 'up'),         # ≥30 封板率65% 高度4
        (10, 8, 1, 0, '冰点期', 'flat'),        # total<15
        (40, 20, 2, 1, '退潮期', 'down'),       # 封板率50%<55
        (30, 21, 2, 2, '震荡期', 'flat'),       # 其他 (sealed=21: broken 30% 不>30% → else分支=震荡期)
    ]
    for total, sealed, max_cons, yizi, expect_stage, expect_bias in cases:
        limit_ups = []
        for i in range(total):
            is_yizi = i < yizi
            is_seal = i < sealed
            cons = max_cons if i == 0 else (1 if is_seal else 0)
            limit_ups.append(_lu(
                code=f'600{i:03d}', t='09:25' if is_yizi else '10:00',
                consecutive=cons, is_seal=is_seal,
                is_broken=(not is_seal) if not is_yizi else False,
                is_first=(cons == 1)))
        cycle = rep._detect_cycle_stage(limit_ups, {})
        assert cycle['stage'] == expect_stage, f"total={total} sealed={sealed}"
        assert cycle['next_bias'] == expect_bias, f"stage={cycle['stage']}"


def test_auction_direction_all_forecasts():
    rep = _make_cycle_report()
    cases = [
        # (总数, 一字数, 早盘数, 炸板数, 期望 forecast, 期望 direction)
        (30, 12, 16, 1, '火爆', 'high'),    # 一字≥10 且早盘占比≥50%
        (50, 3, 22, 2, '偏强', 'high'),     # 早盘占比≥40% 且总数≥50
        (30, 1, 8, 2, '中性', 'flat'),      # 早盘占比≥20%
        (30, 1, 2, 8, '偏弱', 'low'),       # 炸板>20%
        (20, 0, 2, 0, '观望', 'low'),       # 其余
    ]
    for total, yizi, early, broken, expect_fc, expect_dir in cases:
        limit_ups = []
        for i in range(total):
            if i < yizi:
                t = '09:25'
            elif i < yizi + early:
                t = '09:40'
            else:
                t = '14:00'
            limit_ups.append(_lu(code=f'600{i:03d}', t=t,
                                 is_seal=(i >= broken), is_broken=(i < broken),
                                 consecutive=(2 if i % 5 == 0 else 1)))
        fc = rep._forecast_next_auction(limit_ups, {})
        assert fc['forecast'] == expect_fc, f"total={total} yizi={yizi} early={early} broken={broken}"
        assert fc['direction'] == expect_dir, f"forecast={fc['forecast']}"
