"""今日1进2（视频方法论版）单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- Task 1: 权重 ----------

def test_default_weights_complete():
    from ashare_review.one_two_v2.weights import DEFAULT_WEIGHTS, DIMENSIONS
    assert set(DEFAULT_WEIGHTS['dimensions'].keys()) == set(DIMENSIONS)
    assert len(DIMENSIONS) == 8
    for k in ('auction_ratio_high', 'auction_ratio_mid', 'auction_ratio_low',
              'volume_health_pct', 'cap_max', 'price_max'):
        assert k in DEFAULT_WEIGHTS['thresholds']
    assert DEFAULT_WEIGHTS['thresholds']['auction_ratio_high'] == 10.0
    assert DEFAULT_WEIGHTS['thresholds']['volume_health_pct'] == 80.0


def test_validate_weights():
    from ashare_review.one_two_v2.weights import validate_weights, DEFAULT_WEIGHTS
    assert validate_weights(DEFAULT_WEIGHTS) == []
    bad = dict(DEFAULT_WEIGHTS)
    bad['dimensions'] = dict(bad['dimensions'], quality=60)
    assert any('quality' in e for e in validate_weights(bad))
    bad2 = dict(DEFAULT_WEIGHTS)
    bad2['thresholds'] = dict(bad2['thresholds'], auction_ratio_high=-1)
    assert any('auction_ratio_high' in e for e in validate_weights(bad2))
    bad3 = {'dimensions': {}, 'thresholds': {}}
    assert validate_weights(bad3) != []


def test_weight_store(tmp_path):
    from ashare_review.one_two_v2.weights import WeightStore
    path = str(tmp_path / 'w.json')
    s = WeightStore(path)
    assert s.get()['dimensions']['quality'] == 30
    s.set({'dimensions': {'quality': 40}, 'thresholds': {'auction_ratio_low': 2.0}})
    s2 = WeightStore(path)
    w = s2.get()
    assert w['dimensions']['quality'] == 40
    assert w['thresholds']['auction_ratio_low'] == 2.0
    bad = tmp_path / 'bad.json'
    bad.write_text('{broken', encoding='utf-8')
    assert WeightStore(str(bad)).get()['dimensions']['quality'] == 30

# ---------- Task 2: 8 维打分 ----------

def _lu(code='600001', name='测试', t='10:00', cons=1, seal=True, broken=False,
        seal_amt=5000.0, turnover=8000.0, cap=40.0, price=8.0, board='换手板'):
    from ashare_review.data.models import LimitUpInfo
    return LimitUpInfo(code=code, name=name, limit_up_time=t, seal_amount=seal_amt,
                       turnover=turnover, float_market_cap=cap, consecutive=cons,
                       is_first=cons == 1, is_seal=seal, is_broken=broken,
                       board_type=board, close_price=price)


def _weights():
    from ashare_review.one_two_v2.weights import DEFAULT_WEIGHTS
    return DEFAULT_WEIGHTS


def test_candidate_filter():
    from ashare_review.one_two_v2.picks import filter_candidates
    pool = [
        _lu(code='600001', t='09:35', seal_amt=5000, turnover=8000),
        _lu(code='600002', t='09:25', seal_amt=5000, turnover=8000),
        _lu(code='600003', cons=3),
        _lu(code='000001', t='10:00'),
        _lu(code='300001', t='10:00'),
        _lu(code='830001', t='10:00'),
    ]
    cands = filter_candidates(pool)
    codes = sorted(c['code'] for c in cands)
    assert codes == ['000001', '600001']


def test_score_quality():
    from ashare_review.one_two_v2.picks import score_dimension
    w = _weights()
    lu = _lu(t='09:35', seal_amt=6000, turnover=8000, cap=40.0)
    r = score_dimension('quality', lu, {}, w)
    assert r['score'] > 20
    lu2 = _lu(t='14:30', seal_amt=2000, turnover=8000)
    r2 = score_dimension('quality', lu2, {}, w)
    assert r2['score'] < r['score']


def test_score_theme_stage():
    from ashare_review.one_two_v2.picks import score_dimension
    w = _weights()
    ctx = {'sector': {'zt_count': 6, 'max_consecutive': 2, 'is_new_theme': True}}
    r = score_dimension('theme_stage', _lu(), ctx, w)
    assert r['score'] > 0 and '试水' in r['reason']
    ctx2 = {'sector': {'zt_count': 9, 'max_consecutive': 5}}
    r2 = score_dimension('theme_stage', _lu(), ctx2, w)
    assert r2['score'] < 0 and '兑现' in r2['reason']


def test_score_volume_health():
    from ashare_review.one_two_v2.picks import score_dimension
    w = _weights()
    r = score_dimension('volume_health', _lu(), {'today_vol': 800, 'prev_high_vol': 1000}, w)
    assert r['score'] > 0
    r2 = score_dimension('volume_health', _lu(), {'today_vol': 200, 'prev_high_vol': 1000}, w)
    assert r2['score'] < 0
    r3 = score_dimension('volume_health', _lu(), {'today_vol': 2000, 'prev_high_vol': 1000}, w)
    assert r3['score'] < 0


def test_score_emotion_energy_status():
    from ashare_review.one_two_v2.picks import score_dimension
    w = _weights()
    r = score_dimension('emotion', _lu(), {'zt_trend': 'double_ice'}, w)
    assert r['score'] > 0
    r2 = score_dimension('emotion', _lu(), {'zt_trend': 'double_climax'}, w)
    assert r2['score'] < 0
    r3 = score_dimension('energy_ladder', _lu(), {'ladder_at_2': True}, w)
    assert r3['score'] > 0
    r4 = score_dimension('energy_ladder', _lu(), {'ladder_at_2': False, 'ladder_at_3': True}, w)
    assert r4['score'] < 0
    r5 = score_dimension('status', _lu(), {'upper_same_theme': False}, w)
    assert r5['score'] > 0
    r6 = score_dimension('status', _lu(), {'upper_same_theme': True}, w)
    assert r6['score'] < 0


def test_tactic_classify():
    from ashare_review.one_two_v2.picks import classify_tactic
    assert classify_tactic(_lu(t='14:30')) == 'weak_strong'
    assert classify_tactic(_lu(t='10:00', seal=True, broken=True)) == 'weak_strong'
    t = classify_tactic(_lu(t='09:40'))
    assert t in ('graph', 'auction')

# ---------- Task 3: 竞价确认 ----------

def test_auction_ratio_tiers():
    from ashare_review.one_two_v2.auction import grade_auction_ratio
    from ashare_review.one_two_v2.weights import DEFAULT_WEIGHTS
    w = DEFAULT_WEIGHTS['thresholds']
    # 竞价额(万) / 市值(亿×1e4 万)：40亿市值 → 4亿竞价=10%、2亿=5%、1.2亿=3%
    assert grade_auction_ratio(40000.0, 40.0, w)['level'] == 'extreme'
    assert grade_auction_ratio(20000.0, 40.0, w)['level'] == 'high'
    assert grade_auction_ratio(12000.0, 40.0, w)['level'] == 'mid'
    assert grade_auction_ratio(1000.0, 40.0, w)['level'] == 'low'


def test_auction_tactic_trigger():
    from ashare_review.one_two_v2.auction import check_trigger
    r = check_trigger('weak_strong', open_change_pct=4.0,
                      auction_volume=600, preclose_volume=1000, prev_high=800.0, gap_price=None)
    assert r['triggered'] is True and '弱转强' in r['note']
    r2 = check_trigger('weak_strong', open_change_pct=1.0,
                       auction_volume=600, preclose_volume=1000, prev_high=800.0, gap_price=None)
    assert r2['triggered'] is False
    r3 = check_trigger('graph', open_change_pct=2.0, auction_volume=0,
                       preclose_volume=0, prev_high=9.5, gap_price=10.2)
    assert r3['triggered'] is True and '图形' in r3['note']
    r4 = check_trigger('graph', open_change_pct=2.0, auction_volume=0,
                       preclose_volume=0, prev_high=10.5, gap_price=10.2)
    assert r4['triggered'] is False
    r5 = check_trigger('auction', open_change_pct=2.0, auction_volume=0,
                       preclose_volume=0, prev_high=0.0, gap_price=None,
                       ratio=6.0, thresholds={'auction_ratio_low': 3.0})
    assert r5['triggered'] is True

# ---------- Task 4: 台账 ----------

def test_ledger_record_and_verify(tmp_path):
    from ashare_review.one_two_v2.ledger import Ledger
    l = Ledger(str(tmp_path / 't.db'))
    pid = l.record_pick('20260831', '600001', '测试', 55.0,
                        {'quality': 20}, 'auction', mcap=40.0)
    assert pid > 0
    assert l.record_pick('20260831', '600001', '测试', 55.0, {}, 'auction') == 0
    n = l.verify_pick('20260831', '600001', 'zt', 1, 6.5)
    assert n == 1
    row = l.get_pick('20260831', '600001')
    assert row['next_result'] == 'zt' and row['hit'] == 1
    assert row['auction_ratio'] == 6.5
    assert row['mcap'] == 40.0


def test_ledger_dimension_stats(tmp_path):
    from ashare_review.one_two_v2.ledger import Ledger
    l = Ledger(str(tmp_path / 't.db'))
    for i, (score, hit) in enumerate([(30, 1), (25, 1), (20, 0), (-5, 0), (0, 0)]):
        l.record_pick('20260831', f'6000{i:02d}', 'X', float(score),
                      {'quality': {'score': score}}, 'auction')
        l.verify_pick('20260831', f'6000{i:02d}', 'zt' if hit else 'down', hit, 0)
    stats = l.dimension_stats()
    q = stats['dimensions']['quality']
    assert q['pos_total'] == 3 and q['pos_hit'] == 2
    assert q['neg_total'] == 2 and q['neg_hit'] == 0
    t = stats['by_tactic']
    assert t['auction']['total'] == 5


def test_ledger_pending_verification(tmp_path):
    from ashare_review.one_two_v2.ledger import Ledger
    l = Ledger(str(tmp_path / 't.db'))
    l.record_pick('20260831', '600001', 'A', 50.0, {}, 'auction')
    l.record_pick('20260830', '600002', 'B', 45.0, {}, 'graph')
    pending = l.get_pending()
    assert len(pending) == 2
    assert pending[0]['pick_date'] == '20260830'

# ---------- Task 5: 编排 ----------

def _ws():
    from ashare_review.one_two_v2.weights import DEFAULT_WEIGHTS
    return DEFAULT_WEIGHTS


def test_run_picks(tmp_path, monkeypatch):
    from ashare_review.one_two_v2 import service as svc
    from ashare_review.one_two_v2.ledger import Ledger
    monkeypatch.setattr(svc, 'LEDGER_DB', str(tmp_path / 't.db'))
    result = svc.run_picks(
        pool=[_lu(code='600001', t='09:40', seal_amt=6000, turnover=8000, cap=40.0)],
        weights=_ws(), ctx={'scored': {'600001': {'zt_trend': 'double_ice'}}},
        trade_date='20260831')
    assert result['total'] == 1
    assert result['picks'][0]['code'] == '600001'
    l = Ledger(str(tmp_path / 't.db'))
    assert len(l.list_picks('20260831')) == 1


def test_run_picks_empty_pool(tmp_path, monkeypatch):
    from ashare_review.one_two_v2 import service as svc
    monkeypatch.setattr(svc, 'LEDGER_DB', str(tmp_path / 't.db'))
    r = svc.run_picks(pool=[], weights=_ws())
    assert r['total'] == 0 and r['picks'] == []


def test_verify_pending_fake(tmp_path, monkeypatch):
    from ashare_review.one_two_v2 import service as svc
    from ashare_review.one_two_v2.ledger import Ledger
    monkeypatch.setattr(svc, 'LEDGER_DB', str(tmp_path / 't.db'))
    l = Ledger(str(tmp_path / 't.db'))
    l.record_pick('20260828', '600001', 'A', 50.0, {'quality': {'score': 10}}, 'auction')
    n = svc.verify_pending(ledger=l, verify_fake=lambda row: ('zt', 1, None))
    assert n == 1
    assert l.get_pick('20260828', '600001')['hit'] == 1


def test_grade_next_day():
    from ashare_review.one_two_v2.service import _grade_next_day
    assert _grade_next_day(10.0, 11.0, '600001') == ('zt', 1)
    assert _grade_next_day(10.0, 10.3, '600001') == ('up3', 1)
    assert _grade_next_day(10.0, 9.7, '600001') == ('flat', 0)
    assert _grade_next_day(10.0, 9.0, '600001') == ('down', 0)
    assert _grade_next_day(10.0, 11.8, '300001') == ('up3', 1)
    assert _grade_next_day(10.0, 12.2, '300001') == ('zt', 1)


def test_build_pick_context():
    from ashare_review.one_two_v2.service import build_pick_context
    pool = [
        _lu(code='600001', t='09:40', board='半导体'),
        _lu(code='600002', cons=2, t='09:25', board='半导体'),
        _lu(code='600003', cons=3, t='09:25', board='其他'),
    ]
    import pandas as pd
    state_df = pd.DataFrame({'limit_up': [80, 60, 40]})
    concept_map = {'半导体': {'members': {'600001': 1, '600002': 1}}}
    ctx = build_pick_context(pool, concept_map=concept_map, state_df=state_df)
    s1 = ctx['scored']['600001']
    assert s1['zt_trend'] == 'double_ice'
    assert s1['concept_count'] == 1
    assert s1['upper_same_theme'] is True
    assert s1['ladder_at_2'] is True or s1['ladder_at_3'] is True

# ---------- Task 6: Web API ----------

def test_one_two_page_and_api(tmp_path, monkeypatch):
    from ashare_review.one_two_v2 import service as svc
    from ashare_review.one_two_v2 import weights as wmod
    from ashare_review.web.app import app
    monkeypatch.setattr(svc, 'LEDGER_DB', str(tmp_path / 't.db'))
    monkeypatch.setattr(wmod, '_WEIGHTS_PATH', str(tmp_path / 'w.json'))
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.get('/one_two_picks')
    assert rv.status_code == 200
    body = rv.data.decode('utf-8')
    assert '今日1进2' in body
    rv2 = c.get('/api/one_two/weights')
    assert rv2.status_code == 200
    assert rv2.get_json()['dimensions']['quality'] == 30
    rv3 = c.post('/api/one_two/weights', json={'dimensions': {'quality': 40}})
    assert rv3.status_code == 200
    rv4 = c.post('/api/one_two/weights', json={'dimensions': {'quality': 99}})
    assert rv4.status_code == 400
    rv5 = c.get('/api/one_two/ledger/stats')
    assert rv5.status_code == 200
    rv6 = c.get('/api/one_two/picks')
    assert rv6.status_code == 200

# ---------- Task 8b: 量能前高量真实计算 ----------

class FakeTdxVol:
    """可控日线（含 volume）：code -> [(YYYYMMDD, volume), ...]"""
    def __init__(self, data):
        self.data = data

    def read_daily(self, code, market):
        import pandas as pd
        from datetime import datetime
        bars = self.data.get(str(code))
        if not bars:
            return pd.DataFrame()
        rows = [{'trade_date': datetime.strptime(d, '%Y%m%d').date(),
                 'open': 10.0, 'close': 10.0, 'volume': v} for d, v in bars]
        return pd.DataFrame(rows).sort_values('trade_date').reset_index(drop=True)


def test_volume_health_data():
    from ashare_review.one_two_v2.service import _volume_health_data
    # 60 根：前 59 根最高 1000，第 60 根(选股日) 800
    bars = [(f'2026060{i}', 100 + i * 5) for i in range(1, 10)] +            [(f'202606{i:02d}', 200) for i in range(10, 20)] +            [(f'202606{i:02d}', 1000) for i in range(20, 30)] +            [(f'202607{i:02d}', 300) for i in range(1, 10)] +            [(f'202607{i:02d}', 250) for i in range(10, 20)] +            [(f'202607{i:02d}', 900) for i in range(20, 30)] +            [(f'202608{i:02d}', 400) for i in range(1, 10)] +            [('20260831', 800)]
    tdx = FakeTdxVol({'600001': bars})
    today_vol, prev_high = _volume_health_data(tdx, '600001', '20260831')
    assert today_vol == 800
    assert prev_high == 1000    # 前 60 日最高


def test_volume_health_no_tdx_data():
    from ashare_review.one_two_v2.service import _volume_health_data
    tdx = FakeTdxVol({})
    assert _volume_health_data(tdx, '600001', '20260831') == (0, 0)


def test_build_pick_context_fills_volume():
    from ashare_review.one_two_v2.service import build_pick_context
    bars = [(f'202606{i:02d}', 100 + i) for i in range(1, 30)] +            [(f'202607{i:02d}', 200 + i) for i in range(1, 30)] +            [('20260831', 800)]
    tdx = FakeTdxVol({'600001': bars, '600002': bars})
    pool = [
        _lu(code='600001', t='09:40', board='半导体'),
        _lu(code='600002', cons=2, t='09:25', board='半导体'),
    ]
    ctx = build_pick_context(pool, tdx=tdx, trade_date='20260831')
    assert ctx['scored']['600001']['today_vol'] == 800
    assert ctx['scored']['600001']['prev_high_vol'] > 0

# ---------- 修复：dimensions 解析 + 当日覆盖 ----------

def test_ledger_list_parses_dimensions(tmp_path):
    from ashare_review.one_two_v2.ledger import Ledger
    l = Ledger(str(tmp_path / 't.db'))
    l.record_pick('20260831', '600001', 'A', 55.0,
                  {'quality': {'score': 20, 'reason': 'x'}}, 'auction')
    rows = l.list_picks('20260831')
    assert isinstance(rows[0]['dimensions'], dict)
    assert rows[0]['dimensions']['quality']['score'] == 20


def test_ledger_clear_day(tmp_path):
    from ashare_review.one_two_v2.ledger import Ledger
    l = Ledger(str(tmp_path / 't.db'))
    l.record_pick('20260831', '600001', 'A', 50.0, {}, 'auction')
    l.record_pick('20260831', '600002', 'B', 45.0, {}, 'graph')
    l.record_pick('20260830', '600003', 'C', 40.0, {}, 'auction')
    assert l.clear_day('20260831') == 2
    assert len(l.list_picks('20260831')) == 0
    assert len(l.list_picks('20260830')) == 1   # 其他日期不受影响


def test_run_picks_overwrites_day(tmp_path, monkeypatch):
    """同日重跑盘后精选 → 当日记录被覆盖为最新一批（≤top_n）"""
    from ashare_review.one_two_v2 import service as svc
    from ashare_review.one_two_v2.ledger import Ledger
    monkeypatch.setattr(svc, 'LEDGER_DB', str(tmp_path / 't.db'))
    pool1 = [_lu(code='600001', t='09:40', seal_amt=6000, turnover=8000, cap=40.0)]
    pool2 = [_lu(code='600002', t='10:00', seal_amt=3000, turnover=8000, cap=60.0)]
    svc.run_picks(pool1, trade_date='20260831')
    svc.run_picks(pool2, trade_date='20260831')
    l = Ledger(str(tmp_path / 't.db'))
    rows = l.list_picks('20260831')
    assert len(rows) == 1 and rows[0]['code'] == '600002'   # 最新一批覆盖

def test_theme_overlay_coverage_aware():
    from ashare_review.one_two_v2.picks import score_dimension
    w = _weights()
    # 小库(5概念) + 0 概念 → 中性 0（不惩罚）
    r = score_dimension('theme_overlay', _lu(), {'concept_count': 0, 'concept_coverage': 5}, w)
    assert r['score'] == 0 and '未覆盖' in r['reason']
    # 大库(100概念) + 0 概念 → 惩罚（真孤立）
    r2 = score_dimension('theme_overlay', _lu(), {'concept_count': 0, 'concept_coverage': 100}, w)
    assert r2['score'] < 0
    # 有概念 → 正分
    r3 = score_dimension('theme_overlay', _lu(), {'concept_count': 3, 'concept_coverage': 100}, w)
    assert r3['score'] > 0
