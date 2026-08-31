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
    """返回可控行情：code -> {date_str: {'open':.., 'close':.., 'high'?:..}}，含 trade_date 列"""
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
            row = {'trade_date': datetime.strptime(ds, '%Y%m%d').date(),
                   'open': bar['open'], 'close': bar['close']}
            if 'high' in bar:
                row['high'] = bar['high']
            rows.append(row)
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


def test_record_day_market_open_row(tmp_path):
    """开盘涨跌家数记录为独立 market_open 类型，不再进 cycle 明细"""
    import json
    from ashare_review.prediction_ledger.service import record_day
    from ashare_review.prediction_ledger.store import LedgerStore
    db = str(tmp_path / 't.db')
    report = {
        'cycle': {'stage': '发酵期', 'next_bias': 'up', 'stage_desc': '赚钱效应增强',
                  'metrics': {'total_zt': 60}},
        'market_overview': {'open_up_count': 3200, 'open_down_count': 2100,
                            'open_flat_count': 300},
        'sentiment': {'picks': []},
        'auction_forecast': {},
    }
    assert record_day(report, '20260814', db) == 2   # cycle + market_open
    assert record_day(report, '20260814', db) == 0   # 幂等
    store = LedgerStore(db)
    rows = store.rows(365)
    cycle = [r for r in rows if r['pred_type'] == 'cycle']
    mo = [r for r in rows if r['pred_type'] == 'market_open']
    assert len(cycle) == 1 and len(mo) == 1
    # cycle 明细已不含开盘家数
    cd = json.loads(cycle[0]['detail'])
    assert 'open_up' not in cd
    assert cd['stage'] == '发酵期'
    # market_open 行带开盘家数（观测型，无 actual/hit）
    md = json.loads(mo[0]['detail'])
    assert md['open_up'] == 3200 and md['open_down'] == 2100
    assert md['open_flat'] == 300
    assert mo[0]['item_name'] == '开盘涨3200家/跌2100家'
    assert mo[0]['hit'] is None
    # 缺 market_overview → 不建 market_open 行
    report2 = dict(report); report2['market_overview'] = {}
    record_day(report2, '20260814', db)
    mo2 = [r for r in LedgerStore(db).rows(365) if r['pred_type'] == 'market_open']
    assert len(mo2) == 1   # 幂等不新增，也不因空 overview 追加


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


# ---------- Task 5: 历史追溯 ----------

def test_migrate_picks_history(tmp_path):
    import json
    from ashare_review.prediction_ledger.service import migrate_picks_history
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    hist = str(tmp_path / 'picks_history.json')
    json.dump({
        '20260813': [{'code': '600001', 'name': 'A', 'score': 61, 'reasons': []},
                     {'code': '600002', 'name': 'B', 'score': 50, 'reasons': []}],
        '20260810': [{'code': '600003', 'name': 'C', 'score': 55, 'reasons': []}],
    }, open(hist, 'w', encoding='utf-8'))
    cal = TradingCalendar()
    # 600001 次日涨停；600002 次日 -2%；600003 无 TDX 数据（跳过）
    tdx = FakeTdx({'600001': {'20260813': {'open': 10.0, 'close': 10.0},
                              '20260814': {'open': 11.0, 'close': 11.0}},
                   '600002': {'20260813': {'open': 10.0, 'close': 10.0},
                              '20260814': {'open': 9.8, 'close': 9.8}}})
    ak = FakeAk({})   # 无涨停池 → 降级按涨幅
    db = str(tmp_path / 't.db')
    inserted = migrate_picks_history(tdx, ak, calendar=cal, db_path=db, history_file=hist)
    assert inserted == 2
    # 幂等：再跑一遍不新增
    assert migrate_picks_history(tdx, ak, calendar=cal, db_path=db, history_file=hist) == 0
    store = LedgerStore(db)
    rows = {r['item_key']: r for r in store.rows(365)}
    assert rows['600001']['actual'] == 'zt' and rows['600001']['hit'] == 1
    assert rows['600002']['actual'] == 'flat' and rows['600002']['hit'] == 0


def test_migrate_missing_file(tmp_path):
    from ashare_review.prediction_ledger.service import migrate_picks_history
    assert migrate_picks_history(None, None, history_file=str(tmp_path / 'nope.json'),
                                 db_path=str(tmp_path / 't.db')) == 0


def test_migrate_bad_json(tmp_path, caplog):
    """坏 JSON：返回 0 且记录告警（不伪装成'没有历史'）"""
    import logging
    from ashare_review.prediction_ledger.service import migrate_picks_history
    hist = tmp_path / 'picks_history.json'
    hist.write_text('{not valid json', encoding='utf-8')
    with caplog.at_level(logging.WARNING, logger='ashare_review.prediction_ledger.service'):
        n = migrate_picks_history(None, None, history_file=str(hist),
                                  db_path=str(tmp_path / 't.db'))
    assert n == 0
    assert any('跳过迁移' in r.message for r in caplog.records)


def test_migrate_network_down_fallback(tmp_path):
    """迁移时涨停池网络失败 → 按涨幅降级判定"""
    import json
    from ashare_review.prediction_ledger.service import migrate_picks_history
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.utils.calendar import TradingCalendar
    hist = str(tmp_path / 'picks_history.json')
    json.dump({'20260813': [{'code': '600001', 'name': 'A', 'score': 61, 'reasons': []}]},
              open(hist, 'w', encoding='utf-8'))
    cal = TradingCalendar()
    tdx = FakeTdx({'600001': {'20260813': {'open': 10.0, 'close': 10.0},
                              '20260814': {'open': 11.0, 'close': 11.0}}})
    ak = FakeAk({}, raise_on={'20260814'})   # 网络失败
    db = str(tmp_path / 't.db')
    n = migrate_picks_history(tdx, ak, calendar=cal, db_path=db, history_file=hist)
    assert n == 1
    store = LedgerStore(db)
    row = [r for r in store.rows(365) if r['item_key'] == '600001'][0]
    assert row['actual'] == 'zt' and row['hit'] == 1   # 涨幅 +10% ≥9.8% → zt


# ---------- Task 6: Web 接线 ----------

def test_review_route_records_ledger(tmp_path, monkeypatch):
    import unittest.mock as mock
    import pandas as pd
    from ashare_review.prediction_ledger import service as ledger_service
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.report.daily import DailyReport
    from ashare_review.web.app import app, tdx, ak_fetcher

    monkeypatch.setattr(ledger_service, 'DB_PATH', str(tmp_path / 'web.db'))
    monkeypatch.setattr(ledger_service, 'CACHE_PERSIST_DIR', str(tmp_path / 'cache'))
    monkeypatch.setattr(ak_fetcher, 'get_limit_up_pool', lambda d: [])   # 无网络
    monkeypatch.setattr(tdx, 'read_daily', lambda *a, **k: pd.DataFrame())  # 无 TDX 数据
    canned = {
        'date': '2026-08-14',
        'limit_up_codes': ['600001'],
        'sentiment': {'picks': [{'code': '600001', 'name': '测试A', 'score': 62, 'reasons': []}]},
        'cycle': {'stage': '发酵期', 'next_bias': 'up', 'stage_desc': 'x',
                  'metrics': {'total_zt': 60}},
        'auction_forecast': {'forecast': '偏强', 'direction': 'high', 'forecast_desc': 'y'},
    }
    app.config['TESTING'] = True
    with mock.patch.object(DailyReport, 'generate', return_value=canned):
        c = app.test_client()
        rv = c.get('/review?date=20260814&refresh=1')
        assert rv.status_code == 200
    store = LedgerStore(str(tmp_path / 'web.db'))
    rows = store.rows(365)
    assert len(rows) == 3
    assert {r['pred_type'] for r in rows} == {'picks', 'cycle', 'auction'}


def test_prediction_ledger_page(tmp_path, monkeypatch):
    from ashare_review.prediction_ledger import service as ledger_service
    from ashare_review.prediction_ledger.service import record_day
    from ashare_review.web.app import app

    monkeypatch.setattr(ledger_service, 'DB_PATH', str(tmp_path / 'page.db'))
    record_day({
        'limit_up_codes': ['600001'],
        'sentiment': {'picks': [{'code': '600001', 'name': '测试A', 'score': 62, 'reasons': []}]},
        'cycle': {'stage': '发酵期', 'next_bias': 'up', 'metrics': {'total_zt': 60}},
        'auction_forecast': {'forecast': '偏强', 'direction': 'high'},
        'market_overview': {'open_up_count': 3200, 'open_down_count': 2100},
    }, '20260814', str(tmp_path / 'page.db'))
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.get('/prediction_ledger')
    assert rv.status_code == 200
    body = rv.data.decode('utf-8')
    assert '测试A' in body
    assert '发酵期' in body
    assert '开盘涨3200家/跌2100家' in body
    assert '次日收盘' in body   # 胜负列头


# ---------- 次日收盘胜负（win） ----------

def test_pick_actual_win(tmp_path):
    from ashare_review.prediction_ledger.service import _pick_actual
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    cal = TradingCalendar()
    pd = datetime.strptime('20260814', '%Y%m%d').date()
    day1 = cal.next_trading_day(pd, offset=1).strftime('%Y%m%d')  # 第一天=验证日
    day2 = cal.next_trading_day(cal.next_trading_day(pd), offset=1).strftime('%Y%m%d')  # 第二天
    # 第一天(day1)开10.0收10.5；第二天(day2)收11.0 > 第一天开 → 胜
    tdx = FakeTdx({'600001': {'20260813': {'open': 9.5, 'close': 9.5},
                              '20260814': {'open': 10.0, 'close': 10.0},
                              day1: {'open': 10.0, 'close': 10.5},
                              day2: {'open': 11.0, 'close': 11.0}}})
    actual, hit, win, win_open, win_high = _pick_actual(tdx, '600001', set(), day1)
    assert win == 1 and actual == 'up3'
    # 第二天收9.0 < 第一天开10.0 → 负
    tdx2 = FakeTdx({'600001': {'20260813': {'open': 9.5, 'close': 9.5},
                               '20260814': {'open': 10.0, 'close': 10.0},
                               day1: {'open': 10.0, 'close': 10.5},
                               day2: {'open': 9.2, 'close': 9.0}}})
    actual2, hit2, win2, win_open2, win_high2 = _pick_actual(tdx2, '600001', set(), day1)
    assert win2 == 0
    # 第二天未到 → win None（不影响 actual）
    tdx3 = FakeTdx({'600001': {'20260813': {'open': 9.5, 'close': 9.5},
                               '20260814': {'open': 10.0, 'close': 10.0},
                               day1: {'open': 10.0, 'close': 10.5}}})
    actual3, hit3, win3, win_open3, win_high3 = _pick_actual(tdx3, '600001', set(), day1)
    assert win3 is None and actual3 == 'up3'


def test_pick_actual_win_open_high(tmp_path):
    """三口径胜负：次日收盘/开盘/高点 vs 首日开盘；缺 high 列 → win_high 为 None"""
    from ashare_review.prediction_ledger.service import _pick_actual
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    cal = TradingCalendar()
    pd = datetime.strptime('20260814', '%Y%m%d').date()
    day1 = cal.next_trading_day(pd, offset=1).strftime('%Y%m%d')
    day2 = cal.next_trading_day(cal.next_trading_day(pd), offset=1).strftime('%Y%m%d')
    # 首日开10.0；次日开10.5/高11.0/收10.2 → 三口径全胜
    tdx = FakeTdx({'600001': {'20260813': {'open': 9.5, 'close': 9.5},
                              '20260814': {'open': 10.0, 'close': 10.0},
                              day1: {'open': 10.0, 'close': 10.5},
                              day2: {'open': 10.5, 'close': 10.2, 'high': 11.0}}})
    actual, hit, win, win_open, win_high = _pick_actual(tdx, '600001', set(), day1)
    assert win == 1 and win_open == 1 and win_high == 1
    # 次日开9.8/高10.5/收10.2 → 开盘亏、高点赚、收盘赚
    tdx2 = FakeTdx({'600001': {'20260813': {'open': 9.5, 'close': 9.5},
                               '20260814': {'open': 10.0, 'close': 10.0},
                               day1: {'open': 10.0, 'close': 10.5},
                               day2: {'open': 9.8, 'close': 10.2, 'high': 10.5}}})
    actual2, hit2, win2, win_open2, win_high2 = _pick_actual(tdx2, '600001', set(), day1)
    assert win2 == 1 and win_open2 == 0 and win_high2 == 1
    # 次日开9.8/高9.9/收9.7 → 三口径全亏
    tdx3 = FakeTdx({'600001': {'20260813': {'open': 9.5, 'close': 9.5},
                               '20260814': {'open': 10.0, 'close': 10.0},
                               day1: {'open': 10.0, 'close': 10.5},
                               day2: {'open': 9.8, 'close': 9.7, 'high': 9.9}}})
    actual3, hit3, win3, win_open3, win_high3 = _pick_actual(tdx3, '600001', set(), day1)
    assert win3 == 0 and win_open3 == 0 and win_high3 == 0
    # 无 high 列 → win_high None（收盘/开盘照常判定）
    tdx4 = FakeTdx({'600001': {'20260813': {'open': 9.5, 'close': 9.5},
                               '20260814': {'open': 10.0, 'close': 10.0},
                               day1: {'open': 10.0, 'close': 10.5},
                               day2: {'open': 10.5, 'close': 10.2}}})
    actual4, hit4, win4, win_open4, win_high4 = _pick_actual(tdx4, '600001', set(), day1)
    assert win4 == 1 and win_open4 == 1 and win_high4 is None


def test_validate_pending_stores_win_and_summary(tmp_path):
    from ashare_review.prediction_ledger.service import record_day, validate_pending
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    db = str(tmp_path / 't.db')
    record_day(_canned_report(), '20260814', db)
    cal = TradingCalendar()
    pd = datetime.strptime('20260814', '%Y%m%d').date()
    next_ymd = cal.next_trading_day(pd, offset=1).strftime('%Y%m%d')
    day2 = cal.next_trading_day(cal.next_trading_day(pd), offset=1).strftime('%Y%m%d')
    # 600001: 第一天开10.0 第二天收12.0 → 胜；600002: 第一天开10.0 第二天收9.0 → 负
    tdx = FakeTdx({'600001': {'20260814': {'open': 10.0, 'close': 10.0},
                              next_ymd: {'open': 10.0, 'close': 11.0},
                              day2: {'open': 12.0, 'close': 12.0}},
                   '600002': {'20260814': {'open': 10.0, 'close': 10.0},
                              next_ymd: {'open': 10.0, 'close': 9.5},
                              day2: {'open': 9.0, 'close': 9.0}}})
    ak = FakeAk({next_ymd: [_lu_info('600001', consecutive=2)]})
    validate_pending(tdx, ak, calendar=cal, db_path=db)
    store = LedgerStore(db)
    rows = {r['item_key']: r for r in store.rows(365) if r['pred_type'] == 'picks'}
    assert rows['600001']['win'] == 1
    assert rows['600002']['win'] == 0
    s = store.summary(365)
    assert s['win']['total'] == 2
    assert s['win']['wins'] == 1 and s['win']['losses'] == 1
    assert s['win']['rate'] == 0.5
    buckets = {b['label']: b for b in s['win_buckets']}
    assert buckets['≥60']['wins'] == 1 and buckets['≥60']['total'] == 1
    assert buckets['<50']['wins'] == 0 and buckets['<50']['total'] == 1


def test_refresh_pick_wins_fills_later(tmp_path):
    """胜负需第二天收盘，验证当天 win 为空，次日 refresh_pick_wins 补写"""
    from ashare_review.prediction_ledger.service import (record_day, validate_pending,
                                                         refresh_pick_wins)
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    db = str(tmp_path / 't.db')
    record_day(_canned_report(), '20260814', db)
    cal = TradingCalendar()
    pd = datetime.strptime('20260814', '%Y%m%d').date()
    next_ymd = cal.next_trading_day(pd, offset=1).strftime('%Y%m%d')
    day2 = cal.next_trading_day(cal.next_trading_day(pd), offset=1).strftime('%Y%m%d')
    # 验证当天：第二天数据还没出 → win 为空
    tdx_day1 = FakeTdx({'600001': {'20260814': {'open': 10.0, 'close': 10.0},
                                   next_ymd: {'open': 10.0, 'close': 11.0}},
                        '600002': {'20260814': {'open': 10.0, 'close': 10.0},
                                   next_ymd: {'open': 10.0, 'close': 9.5}}})
    ak = FakeAk({next_ymd: [_lu_info('600001', consecutive=2)]})
    validate_pending(tdx_day1, ak, calendar=cal, db_path=db)
    store = LedgerStore(db)
    rows = {r['item_key']: r for r in store.rows(365) if r['pred_type'] == 'picks'}
    assert rows['600001']['win'] is None
    assert rows['600001']['hit'] == 1   # 命中已判定
    # 次日：第二天数据出来了 → refresh 补写
    tdx_day2 = FakeTdx({'600001': {'20260814': {'open': 10.0, 'close': 10.0},
                                   next_ymd: {'open': 10.0, 'close': 11.0},
                                   day2: {'open': 12.0, 'close': 12.0}},
                        '600002': {'20260814': {'open': 10.0, 'close': 10.0},
                                   next_ymd: {'open': 10.0, 'close': 9.5},
                                   day2: {'open': 9.0, 'close': 9.0}}})
    n = refresh_pick_wins(tdx_day2, calendar=cal, db_path=db)
    assert n == 2
    rows2 = {r['item_key']: r for r in LedgerStore(db).rows(365) if r['pred_type'] == 'picks'}
    assert rows2['600001']['win'] == 1
    assert rows2['600002']['win'] == 0


def test_ledger_detail_shows_auction_verdict_on_picks_row(tmp_path, monkeypatch):
    """明细里每个标的（picks 行）末尾展示竞价判定（关联 pick_auction 行）"""
    from ashare_review.prediction_ledger import service as ledger_service
    from ashare_review.prediction_ledger.service import record_day, record_pick_auctions
    from ashare_review.web.app import app

    monkeypatch.setattr(ledger_service, 'DB_PATH', str(tmp_path / 'page2.db'))
    db = str(tmp_path / 'page2.db')
    # 真实流程：08-13 选出标的（record_day 记 pred_date=08-13）；
    # 08-14 复盘时验证昨日（record_pick_auctions 也记 pred_date=08-13），两者关联上
    record_day({
        'limit_up_codes': ['600001'],
        'sentiment': {'picks': [{'code': '600001', 'name': '测试A', 'score': 62,
                                 'reasons': ['首板']}]},
        'cycle': {'stage': '发酵期', 'next_bias': 'up', 'metrics': {'total_zt': 60}},
        'auction_forecast': {'forecast': '偏强', 'direction': 'high'},
    }, '20260813', db)
    record_pick_auctions({
        'yesterday_picks': [
            {'code': '600001', 'name': '测试A', 'today_chg': 5.0,
             'is_zt_today': False,
             'auction': {'verdict': '抢筹', 'type': '高开高走', 'open_pct': 2.5}},
        ],
    }, '20260814', db)
    app.config['TESTING'] = True
    c = app.test_client()
    body = c.get('/prediction_ledger').data.decode('utf-8')
    # picks 行末尾出现竞价判定（抢筹），非标的行（周期）为 —
    assert '🔥 抢筹' in body
    # 该行同时带标的与判定，确认是同一条标的
    assert '测试A' in body


def test_ledger_validate_api(tmp_path, monkeypatch):
    from ashare_review.prediction_ledger import service as ledger_service
    from ashare_review.web.app import app

    monkeypatch.setattr(ledger_service, 'DB_PATH', str(tmp_path / 'api.db'))
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.post('/api/ledger/validate')
    assert rv.status_code == 200
    assert rv.get_json()['ok'] is True


# ---------- Task 7: 竞价判断(pick_auction)入台账 ----------

def test_hit_for_auction_verdict():
    from ashare_review.prediction_ledger.validate import hit_for_auction_verdict
    # 抢筹：强信号 → 需涨停/涨≥3%
    assert hit_for_auction_verdict('抢筹', 'zt') == 1
    assert hit_for_auction_verdict('抢筹', 'up3') == 1
    assert hit_for_auction_verdict('抢筹', 'up') == 0
    assert hit_for_auction_verdict('抢筹', 'down') == 0
    # 达标：可参与 → 收涨即可
    assert hit_for_auction_verdict('达标', 'up') == 1
    assert hit_for_auction_verdict('达标', 'up3') == 1
    assert hit_for_auction_verdict('达标', 'flat') == 0
    # 观望：规避 → 不涨(震荡/大跌)才算规避正确
    assert hit_for_auction_verdict('观望', 'flat') == 1
    assert hit_for_auction_verdict('观望', 'down') == 1
    assert hit_for_auction_verdict('观望', 'up') == 0
    # 无法判定 / 未知判定
    assert hit_for_auction_verdict('抢筹', None) is None
    assert hit_for_auction_verdict('未知', 'up') is None


def _report_with_yesterday_picks():
    return {
        'date': '2026-08-14',
        'yesterday_picks': [
            {'code': '600001', 'name': 'A', 'today_chg': 5.0, 'is_zt_today': False,
             'auction': {'verdict': '抢筹', 'type': '高开高走', 'open_pct': 2.5,
                         'vol_rule': {'desc': 'x', 'vol_0924': 100000,
                                      'vol_0925': 120000,
                                      'prev_max_minute_vol': 200000}}},
            {'code': '600002', 'name': 'B', 'today_chg': -4.0, 'is_zt_today': False,
             'auction': {'verdict': '观望', 'type': '低开走弱', 'open_pct': -2.0}},
            {'code': '600003', 'name': 'C', 'today_chg': 1.0, 'is_zt_today': False,
             'auction': None},   # 无竞价判定 → 跳过
            {'code': '600004', 'name': 'D', 'today_chg': -2.0, 'is_zt_today': False,
             'auction': {'verdict': '抢筹', 'type': '高开砸盘', 'open_pct': 3.0}},
        ],
    }


def test_record_pick_auctions_writes_with_actual(tmp_path):
    from ashare_review.prediction_ledger.service import record_pick_auctions
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    db = str(tmp_path / 't.db')
    assert record_pick_auctions(_report_with_yesterday_picks(), '20260814', db) == 3
    assert record_pick_auctions(_report_with_yesterday_picks(), '20260814', db) == 0  # 幂等
    store = LedgerStore(db)
    rows = {r['item_key']: r for r in store.rows(365)
            if r['pred_type'] == 'pick_auction'}
    assert set(rows) == {'600001', '600002', '600004'}
    cal = TradingCalendar()
    prev_ymd = cal.prev_trading_day(datetime.strptime('20260814', '%Y%m%d').date(),
                                    offset=1).strftime('%Y%m%d')
    assert rows['600001']['pred_date'] == prev_ymd
    assert rows['600001']['direction'] == '抢筹'
    assert rows['600001']['actual'] == 'up3' and rows['600001']['hit'] == 1
    assert rows['600002']['direction'] == '观望'
    assert rows['600002']['actual'] == 'down' and rows['600002']['hit'] == 1
    assert rows['600004']['actual'] == 'flat' and rows['600004']['hit'] == 0  # 抢筹却收跌 → 未中
    import json
    d = json.loads(rows['600001']['detail'])
    assert d['auction_type'] == '高开高走'
    assert d['vol_0925'] == 120000


def test_record_pick_auctions_skips_error(tmp_path):
    from ashare_review.prediction_ledger.service import record_pick_auctions
    assert record_pick_auctions({'error': 'boom'}, '20260814',
                                str(tmp_path / 't.db')) == 0
    assert record_pick_auctions(None, '20260814', str(tmp_path / 't.db')) == 0


def test_store_summary_pick_auction_buckets(tmp_path):
    from ashare_review.prediction_ledger.service import record_pick_auctions
    from ashare_review.prediction_ledger.store import LedgerStore
    db = str(tmp_path / 't.db')
    record_pick_auctions(_report_with_yesterday_picks(), '20260814', db)
    s = LedgerStore(db).summary(365)
    assert s['pick_auction']['total'] == 3
    assert s['pick_auction']['verified'] == 3
    assert s['pick_auction']['hit'] == 2
    assert s['pick_auction']['rate'] == round(2 / 3, 4)
    buckets = {b['label']: b for b in s['pick_auction_buckets']}
    assert buckets['抢筹']['hit'] == 1 and buckets['抢筹']['verified'] == 2
    assert buckets['观望']['hit'] == 1 and buckets['观望']['verified'] == 1
    assert buckets['达标']['verified'] == 0
    # 无对应精选行（无 win 数据）→ 胜率为空，命中率不受影响
    assert buckets['抢筹']['win_rate'] is None
    assert buckets['抢筹']['wins'] == 0


def test_store_summary_pick_auction_win_rate(tmp_path):
    """竞价判断板块胜率三口径：次日收盘/开盘/高点 vs 首日开盘（关联同标的精选）"""
    from ashare_review.prediction_ledger.store import LedgerStore
    db = str(tmp_path / 't.db')
    store = LedgerStore(db)
    store.upsert_predictions([
        # 08-13 选出四只精选（600004 只有收盘口径，模拟升级前的旧数据）
        {'pred_date': '20260813', 'pred_type': 'picks', 'item_key': '600001',
         'item_name': 'A', 'direction': None, 'score': 60, 'detail': '{}'},
        {'pred_date': '20260813', 'pred_type': 'picks', 'item_key': '600002',
         'item_name': 'B', 'direction': None, 'score': 55, 'detail': '{}'},
        {'pred_date': '20260813', 'pred_type': 'picks', 'item_key': '600004',
         'item_name': 'D', 'direction': None, 'score': 50, 'detail': '{}'},
        {'pred_date': '20260813', 'pred_type': 'picks', 'item_key': '600003',
         'item_name': 'C', 'direction': None, 'score': 50, 'detail': '{}'},
        # 08-14 竞价判定（pred_date 与精选一致才能关联上）
        {'pred_date': '20260813', 'pred_type': 'pick_auction', 'item_key': '600001',
         'item_name': 'A', 'direction': '抢筹', 'score': None, 'detail': '{}'},
        {'pred_date': '20260813', 'pred_type': 'pick_auction', 'item_key': '600002',
         'item_name': 'B', 'direction': '抢筹', 'score': None, 'detail': '{}'},
        {'pred_date': '20260813', 'pred_type': 'pick_auction', 'item_key': '600004',
         'item_name': 'D', 'direction': '抢筹', 'score': None, 'detail': '{}'},
        {'pred_date': '20260813', 'pred_type': 'pick_auction', 'item_key': '600003',
         'item_name': 'C', 'direction': '观望', 'score': None, 'detail': '{}'},
    ])
    # 竞价判定当日结果
    for k, actual, hit in [('600001', 'up3', 1), ('600002', 'down', 0),
                           ('600004', 'up3', 1), ('600003', 'flat', 1)]:
        store.set_actual('20260813', 'pick_auction', k, actual, hit)
    # 精选次日胜负：收盘口径三只都有；开盘/高点口径 600004 缺（旧数据）
    store.set_actual('20260813', 'picks', '600001', 'up3', 1, win=1, win_open=1, win_high=1)
    store.set_actual('20260813', 'picks', '600002', 'down', 0, win=0, win_open=0, win_high=1)
    store.set_actual('20260813', 'picks', '600004', 'up3', 1, win=1)          # win_open/win_high 缺
    store.set_actual('20260813', 'picks', '600003', 'flat', 0, win=1, win_open=0, win_high=1)
    s = LedgerStore(db).summary(365)
    buckets = {b['label']: b for b in s['pick_auction_buckets']}
    # 抢筹：3 只；收盘 2 胜/3 → 66.7%，开盘 1 胜/2 → 50%，高点 2 胜/2 → 100%
    b = buckets['抢筹']
    assert b['wins'] == 2 and b['win_rate'] == round(2 / 3, 4)
    assert b['wins_open'] == 1 and b['win_open_rate'] == 0.5
    assert b['wins_high'] == 2 and b['win_high_rate'] == 1.0
    # 观望：1 只；收盘/高点 100%，开盘 0%
    assert buckets['观望']['win_rate'] == 1.0
    assert buckets['观望']['win_open_rate'] == 0.0
    assert buckets['观望']['win_high_rate'] == 1.0
    # 达标：无记录 → 三口径胜率均空
    assert buckets['达标']['win_rate'] is None
    assert buckets['达标']['win_open_rate'] is None
    assert buckets['达标']['win_high_rate'] is None


def test_backfill_pick_auctions_from_cache(tmp_path):
    """从持久复盘报告缓存回填 pick_auction（幂等、跳过无判定/坏文件）"""
    import json
    from ashare_review.prediction_ledger.service import backfill_pick_auctions_from_cache
    from ashare_review.prediction_ledger.store import LedgerStore
    cache = tmp_path / 'cache'
    cache.mkdir()
    # 一个有判定、一个无 yesterday_picks、一个坏 JSON
    (cache / 'review_report_20260814.json').write_text(json.dumps({
        '_payload': {
            'date': '2026-08-14',
            'yesterday_picks': [
                {'code': '600001', 'name': 'A', 'today_chg': 5.0,
                 'is_zt_today': False,
                 'auction': {'verdict': '抢筹', 'type': '高开高走'}},
            ],
        },
    }, ensure_ascii=False), encoding='utf-8')
    (cache / 'review_report_20260813.json').write_text(json.dumps({
        '_payload': {'date': '2026-08-13', 'yesterday_picks': [
            {'code': '600002', 'name': 'B', 'today_chg': 1.0, 'is_zt_today': False,
             'auction': None}]},
    }, ensure_ascii=False), encoding='utf-8')
    (cache / 'review_report_20260812.json').write_text('{bad json', encoding='utf-8')
    db = str(tmp_path / 't.db')
    n = backfill_pick_auctions_from_cache(db_path=db, cache_dir=str(cache))
    assert n == 1
    # 幂等
    assert backfill_pick_auctions_from_cache(db_path=db, cache_dir=str(cache)) == 0
    store = LedgerStore(db)
    pa = [r for r in store.rows(365) if r['pred_type'] == 'pick_auction']
    assert len(pa) == 1 and pa[0]['item_key'] == '600001'
    assert pa[0]['pred_date'] == '20260813'   # 08-14 验证的是 08-13 选出的标的
    assert pa[0]['direction'] == '抢筹'
    # 目录不存在 → 0
    assert backfill_pick_auctions_from_cache(db_path=db,
                                             cache_dir=str(tmp_path / 'nope')) == 0


def test_review_route_records_pick_auctions(tmp_path, monkeypatch):
    """/review?refresh=1 新生成路径把昨日标的竞价判断写入台账"""
    import unittest.mock as mock
    import pandas as pd
    from ashare_review.prediction_ledger import service as ledger_service
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.report.daily import DailyReport
    from ashare_review.web.app import app, tdx, ak_fetcher

    monkeypatch.setattr(ledger_service, 'DB_PATH', str(tmp_path / 'web2.db'))
    monkeypatch.setattr(ledger_service, 'CACHE_PERSIST_DIR', str(tmp_path / 'cache2'))
    monkeypatch.setattr(ak_fetcher, 'get_limit_up_pool', lambda d: [])
    monkeypatch.setattr(tdx, 'read_daily', lambda *a, **k: pd.DataFrame())
    canned = {
        'date': '2026-08-14',
        'limit_up_codes': ['600001'],
        'sentiment': {'picks': [{'code': '600001', 'name': '测试A', 'score': 62,
                                 'reasons': []}]},
        'cycle': {'stage': '发酵期', 'next_bias': 'up', 'stage_desc': 'x',
                  'metrics': {'total_zt': 60}},
        'auction_forecast': {'forecast': '偏强', 'direction': 'high',
                             'forecast_desc': 'y'},
        'pick_stats': {'total_picks': 0, 'avg_score': 0, 'total_days': 0},
        'yesterday_picks': [
            {'code': '600001', 'name': 'A', 'today_chg': 5.0,
             'is_zt_today': False, 'is_2board': False,
             'yesterday_score': 62, 'yesterday_price': 10.0,
             'result': '📈 收涨', 'result_class': 'neutral',
             'auction': {'verdict': '抢筹', 'type': '高开高走', 'open_pct': 2.5}},
        ],
    }
    app.config['TESTING'] = True
    with mock.patch.object(DailyReport, 'generate', return_value=canned):
        c = app.test_client()
        rv = c.get('/review?date=20260814&refresh=1')
        assert rv.status_code == 200
    store = LedgerStore(str(tmp_path / 'web2.db'))
    rows = store.rows(365)
    types = {r['pred_type'] for r in rows}
    assert types == {'picks', 'cycle', 'auction', 'pick_auction'}
    pa = [r for r in rows if r['pred_type'] == 'pick_auction']
    assert len(pa) == 1 and pa[0]['item_key'] == '600001'
    assert pa[0]['direction'] == '抢筹' and pa[0]['hit'] == 1


