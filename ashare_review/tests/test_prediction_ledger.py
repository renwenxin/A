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


# ---------- Task 3: SQLite 存储层 ----------

def _sample_rows():
    return [
        {'pred_date': '20260814', 'pred_type': 'picks', 'item_key': '600001',
         'item_name': '测试A', 'direction': None, 'score': 62,
         'detail': '{"reasons": []}'},
        {'pred_date': '20260814', 'pred_type': 'picks', 'item_key': '600002',
         'item_name': '测试B', 'direction': None, 'score': 45,
         'detail': '{"reasons": []}'},
        {'pred_date': '20260814', 'pred_type': 'cycle', 'item_key': 'daily',
         'item_name': '发酵期', 'direction': 'up', 'score': None,
         'detail': '{"total_zt": 60}'},
        {'pred_date': '20260814', 'pred_type': 'auction', 'item_key': 'daily',
         'item_name': '偏强', 'direction': 'high', 'score': None,
         'detail': '{"pool_codes": ["600001", "600002"]}'},
    ]


def test_store_upsert_idempotent(tmp_path):
    from ashare_review.prediction_ledger.store import LedgerStore
    store = LedgerStore(str(tmp_path / 't.db'))
    assert store.upsert_predictions(_sample_rows()) == 4
    assert store.upsert_predictions(_sample_rows()) == 0   # 重复写不产生新行
    assert len(store.rows(365)) == 4


def test_store_get_unverified_and_mark(tmp_path):
    from ashare_review.prediction_ledger.store import LedgerStore
    store = LedgerStore(str(tmp_path / 't.db'))
    store.upsert_predictions(_sample_rows())
    pending = store.get_unverified()
    assert len(pending) == 4
    first = pending[0]
    store.mark_verified(first['id'], 'zt', 1)
    assert len(store.get_unverified()) == 3


def test_store_summary_aggregation(tmp_path):
    from ashare_review.prediction_ledger.store import LedgerStore
    store = LedgerStore(str(tmp_path / 't.db'))
    store.upsert_predictions(_sample_rows())
    # 手工验证：600001 命中，600002 未中，cycle/auction 各命中
    rows = store.rows(365)
    for r in rows:
        if r['pred_type'] == 'picks':
            store.mark_verified(r['id'], 'zt' if r['item_key'] == '600001' else 'down',
                                1 if r['item_key'] == '600001' else 0)
        elif r['pred_type'] == 'cycle':
            store.mark_verified(r['id'], 'up', 1)
        else:
            store.mark_verified(r['id'], 'high', 1)
    s = store.summary(365)
    assert s['picks']['total'] == 2 and s['picks']['verified'] == 2 and s['picks']['hit'] == 1
    assert s['picks']['rate'] == 0.5
    assert s['cycle']['rate'] == 1.0
    assert s['auction']['rate'] == 1.0
    # 分数段：≥60 → 1/1；50-59 → 0；<50 → 0/1
    buckets = {b['label']: b for b in s['buckets']}
    assert buckets['≥60']['hit'] == 1 and buckets['≥60']['verified'] == 1
    assert buckets['50-59']['verified'] == 0
    assert buckets['<50']['hit'] == 0 and buckets['<50']['verified'] == 1
    # 覆盖统计
    assert s['coverage']['verified_days'] == 1
    assert s['coverage']['pending'] == 0


def test_store_summary_window_filter(tmp_path):
    from datetime import date
    from ashare_review.prediction_ledger.store import LedgerStore
    store = LedgerStore(str(tmp_path / 't.db'))
    today = date.today().strftime('%Y%m%d')
    store.upsert_predictions([{
        'pred_date': today, 'pred_type': 'picks', 'item_key': '600001',
        'item_name': '今天', 'direction': None, 'score': 60, 'detail': '{}'}])
    s = store.summary(1)     # 1 天窗口：包含今天
    assert s['picks']['total'] == 1
    s2 = store.summary(0)    # 0 天窗口：cutoff=今天，pred_date >= 今天 仍含今天（边界）
    assert s2['picks']['total'] == 1


def test_store_set_actual(tmp_path):
    from ashare_review.prediction_ledger.store import LedgerStore
    store = LedgerStore(str(tmp_path / 't.db'))
    store.upsert_predictions(_sample_rows())
    store.set_actual('20260814', 'picks', '600001', 'up3', 1)
    rows = store.rows(365)
    row = [r for r in rows if r['item_key'] == '600001'][0]
    assert row['actual'] == 'up3' and row['hit'] == 1

def test_store_summary_unverified_excluded(tmp_path):
    """未验证记录不计入 rate 分母（设计文档 §8）"""
    from ashare_review.prediction_ledger.store import LedgerStore
    store = LedgerStore(str(tmp_path / 't.db'))
    store.upsert_predictions([
        {'pred_date': '20260814', 'pred_type': 'picks', 'item_key': '600001',
         'item_name': 'A', 'direction': None, 'score': 61, 'detail': '{}'},
        {'pred_date': '20260814', 'pred_type': 'picks', 'item_key': '600002',
         'item_name': 'B', 'direction': None, 'score': 55, 'detail': '{}'},
    ])
    # 只验证 600001（命中），600002 保持未验证
    rows = store.rows(365)
    target = [r for r in rows if r['item_key'] == '600001'][0]
    store.mark_verified(target['id'], 'zt', 1)
    s = store.summary(365)
    assert s['picks']['total'] == 2          # total 含未验证
    assert s['picks']['verified'] == 1       # 分母只算已验证
    assert s['picks']['rate'] == 1.0
    assert s['coverage']['pending'] == 1

# ---------- Task 4: 编排层 record_day / validate_pending ----------

class FakeTdx:
    """返回可控行情：code -> {date_str: {'open':.., 'close':..}}，含 trade_date 列"""
    def __init__(self, data):
        self.data = data

    def read_daily(self, code, market):
        import pandas as pd
        from datetime import datetime
        d = self.data.get(str(code))
        if not d:
            return pd.DataFrame()
        rows = []
        for ds, bar in d.items():
            rows.append({'trade_date': datetime.strptime(ds, '%Y%m%d').date(),
                         'open': bar['open'], 'close': bar['close']})
        df = pd.DataFrame(rows)
        return df.sort_values('trade_date').reset_index(drop=True)


class FakeAk:
    """涨停池可控：date -> LimitUpInfo 列表"""
    def __init__(self, pools=None, raise_on=None):
        self.pools = pools or {}        # {'20260814': [LimitUpInfo, ...]}
        self.raise_on = raise_on or set()

    def get_limit_up_pool(self, trade_date):
        if trade_date in self.raise_on:
            raise RuntimeError('network down')
        return self.pools.get(trade_date, [])


def _lu_info(code, consecutive=1):
    from ashare_review.data.models import LimitUpInfo
    return LimitUpInfo(code=code, name='测试', limit_up_time='10:00', seal_amount=1000,
                       turnover=5000, float_market_cap=30, consecutive=consecutive,
                       is_first=consecutive == 1, is_seal=True, is_broken=False,
                       board_type='换手板', close_price=10.0)


def _canned_report():
    return {
        'date': '2026-08-14',
        'limit_up_codes': ['600001', '600002'],
        'sentiment': {'picks': [
            {'code': '600001', 'name': '测试A', 'score': 62, 'reasons': ['首板']},
            {'code': '600002', 'name': '测试B', 'score': 45, 'reasons': []},
        ]},
        'cycle': {'stage': '发酵期', 'next_bias': 'up', 'stage_desc': '赚钱效应增强',
                  'metrics': {'total_zt': 60}},
        'auction_forecast': {'forecast': '偏强', 'direction': 'high',
                             'forecast_desc': '多数涨停股预期高开'},
    }


def test_record_day_writes_three_types(tmp_path):
    from ashare_review.prediction_ledger.service import record_day
    from ashare_review.prediction_ledger.store import LedgerStore
    db = str(tmp_path / 't.db')
    assert record_day(_canned_report(), '20260814', db) == 4
    assert record_day(_canned_report(), '20260814', db) == 0   # 幂等
    store = LedgerStore(db)
    rows = store.rows(365)
    assert len(rows) == 4
    types = {r['pred_type'] for r in rows}
    assert types == {'picks', 'cycle', 'auction'}


def test_record_day_skips_error_report(tmp_path):
    from ashare_review.prediction_ledger.service import record_day
    assert record_day({'error': 'boom'}, '20260814', str(tmp_path / 't.db')) == 0
    assert record_day(None, '20260814', str(tmp_path / 't.db')) == 0


def test_validate_pending_picks(tmp_path):
    from ashare_review.prediction_ledger.service import record_day, validate_pending
    from ashare_review.prediction_ledger.store import LedgerStore
    db = str(tmp_path / 't.db')
    record_day(_canned_report(), '20260814', db)
    from ashare_review.utils.calendar import TradingCalendar
    cal = TradingCalendar()
    from datetime import datetime, timedelta
    d = datetime.strptime('20260814', '%Y%m%d').date()
    n = cal.next_trading_day(d, offset=1)
    next_ymd = n.strftime('%Y%m%d')
    tdx = FakeTdx({'600001': {'20260814': {'open': 10.0, 'close': 10.0},
                              next_ymd: {'open': 11.0, 'close': 11.0}},
                   '600002': {'20260814': {'open': 10.0, 'close': 10.0},
                              next_ymd: {'open': 9.5, 'close': 9.5}}})
    ak = FakeAk({next_ymd: [_lu_info('600001', consecutive=2)]})
    n_validated = validate_pending(tdx, ak, calendar=cal, db_path=db)
    assert n_validated == 4
    store = LedgerStore(db)
    rows = {r['item_key']: r for r in store.rows(365) if r['pred_type'] == 'picks'}
    assert rows['600001']['actual'] == 'zt' and rows['600001']['hit'] == 1
    assert rows['600002']['actual'] == 'down' and rows['600002']['hit'] == 0


def test_validate_pending_cycle_and_auction(tmp_path):
    from ashare_review.prediction_ledger.service import record_day, validate_pending
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    db = str(tmp_path / 't.db')
    record_day(_canned_report(), '20260814', db)
    cal = TradingCalendar()
    next_ymd = cal.next_trading_day(datetime.strptime('20260814', '%Y%m%d').date(),
                                    offset=1).strftime('%Y%m%d')
    tdx = FakeTdx({'600001': {'20260814': {'open': 10.0, 'close': 10.0},
                              next_ymd: {'open': 10.2, 'close': 10.5}},
                   '600002': {'20260814': {'open': 10.0, 'close': 10.0},
                              next_ymd: {'open': 10.1, 'close': 10.0}}})
    pool = [_lu_info(f'600{i:03d}') for i in range(67)]  # 覆盖 index0/1 后恰余 66 只不同股票：r=66/60=1.1 → up
    pool[0], pool[1] = _lu_info('600001'), _lu_info('600002')
    ak = FakeAk({next_ymd: pool})
    n = validate_pending(tdx, ak, calendar=cal, db_path=db)
    assert n == 4
    store = LedgerStore(db)
    rows = {r['pred_type']: r for r in store.rows(365)}
    assert rows['cycle']['actual'] == 'up' and rows['cycle']['hit'] == 1
    assert rows['auction']['actual'] == 'high' and rows['auction']['hit'] == 1


def test_validate_pending_network_down_skips(tmp_path):
    from ashare_review.prediction_ledger.service import record_day, validate_pending
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    db = str(tmp_path / 't.db')
    record_day(_canned_report(), '20260814', db)
    cal = TradingCalendar()
    next_ymd = cal.next_trading_day(datetime.strptime('20260814', '%Y%m%d').date(),
                                    offset=1).strftime('%Y%m%d')
    tdx = FakeTdx({'600001': {'20260814': {'open': 10.0, 'close': 10.0},
                              next_ymd: {'open': 11.0, 'close': 11.0}},
                   '600002': {'20260814': {'open': 10.0, 'close': 10.0},
                              next_ymd: {'open': 9.5, 'close': 9.5}}})
    ak = FakeAk({}, raise_on={next_ymd})
    n = validate_pending(tdx, ak, calendar=cal, db_path=db)
    assert n == 3
    store = LedgerStore(db)
    rows = {r['pred_type']: r for r in store.rows(365)}
    assert rows['cycle']['hit'] is None
    assert rows['auction']['hit'] == 1


def test_validate_pending_target_bar_in_middle(tmp_path):
    """延迟/历史验证：目标 K 线在历史中间（非最后一行）也能正确定级"""
    from ashare_review.prediction_ledger.service import record_day, validate_pending
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    db = str(tmp_path / 't.db')
    record_day(_canned_report(), '20260814', db)
    cal = TradingCalendar()
    next_ymd = cal.next_trading_day(datetime.strptime('20260814', '%Y%m%d').date(),
                                    offset=1).strftime('%Y%m%d')
    # 3 根 K 线：目标 next_ymd 在中间（后面还有更新的 20260818）
    tdx = FakeTdx({'600001': {'20260814': {'open': 10.0, 'close': 10.0},
                              next_ymd: {'open': 11.0, 'close': 11.0},
                              '20260818': {'open': 9.0, 'close': 9.0}},
                   '600002': {'20260814': {'open': 10.0, 'close': 10.0},
                              next_ymd: {'open': 9.5, 'close': 9.5},
                              '20260818': {'open': 12.0, 'close': 12.0}}})
    ak = FakeAk({next_ymd: [_lu_info('600001', consecutive=2)]})
    n = validate_pending(tdx, ak, calendar=cal, db_path=db)
    store = LedgerStore(db)
    rows = {r['item_key']: r for r in store.rows(365) if r['pred_type'] == 'picks'}
    # 600001 用 next_ymd 定级：+10% → zt（不是用 20260818 的 9.0 判 -10%）
    assert rows['600001']['actual'] == 'zt' and rows['600001']['hit'] == 1
    assert rows['600002']['actual'] == 'down' and rows['600002']['hit'] == 0


def test_validate_pending_empty_pool_skips_cycle(tmp_path):
    """涨停池拉取成功但为空 → cycle 跳过（不误判 down），picks 走涨幅降级"""
    from ashare_review.prediction_ledger.service import record_day, validate_pending
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    db = str(tmp_path / 't.db')
    record_day(_canned_report(), '20260814', db)
    cal = TradingCalendar()
    next_ymd = cal.next_trading_day(datetime.strptime('20260814', '%Y%m%d').date(),
                                    offset=1).strftime('%Y%m%d')
    tdx = FakeTdx({'600001': {'20260814': {'open': 10.0, 'close': 10.0},
                              next_ymd: {'open': 11.0, 'close': 11.0}},
                   '600002': {'20260814': {'open': 10.0, 'close': 10.0},
                              next_ymd: {'open': 9.5, 'close': 9.5}}})
    ak = FakeAk({})      # 成功返回但空池
    n = validate_pending(tdx, ak, calendar=cal, db_path=db)
    store = LedgerStore(db)
    rows = {r['pred_type']: r for r in store.rows(365)}
    assert rows['cycle']['hit'] is None      # 空池 → cycle 跳过
    assert rows['auction']['hit'] == 1       # auction 走 TDX 不受影响
    picks = {r['item_key']: r for r in store.rows(365) if r['pred_type'] == 'picks'}
    assert picks['600001']['actual'] == 'zt' and picks['600001']['hit'] == 1
    assert picks['600002']['actual'] == 'down' and picks['600002']['hit'] == 0
