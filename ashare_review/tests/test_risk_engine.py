"""风控规则引擎单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- Task 1: 默认配置与校验 ----------

def test_default_config_complete():
    from ashare_review.risk.rules import DEFAULT_CONFIG, REGIMES
    for pid in ('vol180', 'zt_replica'):
        cfg = DEFAULT_CONFIG[pid]
        for key in ('stop_loss_pct', 'per_position_pct', 'max_positions',
                    'max_new_per_day', 'drawdown_breaker_pct',
                    'drawdown_recover_pct', 'regime_scale'):
            assert key in cfg, (pid, key)
        assert set(cfg['regime_scale'].keys()) == set(REGIMES)
        assert cfg['regime_scale'].get('退潮下跌') == 0.0   # 退潮禁开仓


def test_default_config_matches_current_constants():
    """默认配置 = 现状常量（行为零变化保证）"""
    from ashare_review.risk.rules import DEFAULT_CONFIG
    assert DEFAULT_CONFIG['vol180']['stop_loss_pct'] == -6.0
    assert DEFAULT_CONFIG['zt_replica']['stop_loss_pct'] == -5.0
    for pid in ('vol180', 'zt_replica'):
        assert DEFAULT_CONFIG[pid]['per_position_pct'] == 10.0
        assert DEFAULT_CONFIG[pid]['max_positions'] == 10
        assert DEFAULT_CONFIG[pid]['max_new_per_day'] == 3


def test_validate_config():
    from ashare_review.risk.rules import validate_config, DEFAULT_CONFIG
    # 合法配置通过
    errs = validate_config('vol180', DEFAULT_CONFIG['vol180'])
    assert errs == []
    # 止损越界（-50%）
    bad = dict(DEFAULT_CONFIG['vol180'], stop_loss_pct=-50.0)
    assert any('stop_loss_pct' in e for e in validate_config('vol180', bad))
    # 仓位越界
    bad2 = dict(DEFAULT_CONFIG['vol180'], per_position_pct=120.0)
    assert any('per_position_pct' in e for e in validate_config('vol180', bad2))
    # regime 系数负数
    bad3 = dict(DEFAULT_CONFIG['vol180'])
    bad3['regime_scale'] = dict(bad3['regime_scale'], **{'强势趋势': -0.5})
    assert any('regime_scale' in e for e in validate_config('vol180', bad3))
    # 缺字段 → 报缺字段（不静默）
    bad4 = {k: v for k, v in DEFAULT_CONFIG['vol180'].items() if k != 'max_positions'}
    assert any('max_positions' in e for e in validate_config('vol180', bad4))


# ---------- Task 2: 边界与健壮性 ----------

def test_validate_config_edge_cases():
    from ashare_review.risk.rules import validate_config, DEFAULT_CONFIG
    base = DEFAULT_CONFIG['vol180']
    # 精确边界：stop -30 过、-30.1 拒
    assert validate_config('vol180', dict(base, stop_loss_pct=-30.0)) == []
    assert any('stop_loss_pct' in e for e in validate_config('vol180', dict(base, stop_loss_pct=-30.1)))
    # per_position 1/50 过、0/50.1 拒
    assert validate_config('vol180', dict(base, per_position_pct=1.0)) == []
    assert validate_config('vol180', dict(base, per_position_pct=50.0)) == []
    assert any('per_position_pct' in e for e in validate_config('vol180', dict(base, per_position_pct=50.1)))
    # drawdown 0 拒（开区间）、50 过
    assert any('drawdown_breaker_pct' in e for e in validate_config('vol180', dict(base, drawdown_breaker_pct=0)))
    assert validate_config('vol180', dict(base, drawdown_breaker_pct=50.0)) == []
    # stop_loss 0 拒（上界开）
    assert any('stop_loss_pct' in e for e in validate_config('vol180', dict(base, stop_loss_pct=0)))
    # bool 拒绝
    assert any('per_position_pct' in e for e in validate_config('vol180', dict(base, per_position_pct=True)))
    assert any('max_positions' in e for e in validate_config('vol180', dict(base, max_positions=True)))
    # 整数语义字段拒绝小数
    assert any('max_positions' in e for e in validate_config('vol180', dict(base, max_positions=10.5)))
    assert any('max_new_per_day' in e for e in validate_config('vol180', dict(base, max_new_per_day=2.7)))
    # 非 dict 输入
    assert validate_config('vol180', 'nope') != []
    # regime_scale 缺键
    bad = dict(base); bad['regime_scale'] = {'强势趋势': 1.0}
    assert any('regime_scale' in e for e in validate_config('vol180', bad))


# ---------- Task 2: 配置存储 ----------

def test_store_default_fallback(tmp_path):
    from ashare_review.risk.store import RiskStore
    s = RiskStore(str(tmp_path / 'nope.json'))
    cfg = s.get('vol180')
    assert cfg['stop_loss_pct'] == -6.0          # 文件不存在 → 默认
    assert s.get('zt_replica')['stop_loss_pct'] == -5.0


def test_store_save_and_load(tmp_path):
    from ashare_review.risk.store import RiskStore
    path = str(tmp_path / 'risk.json')
    s = RiskStore(path)
    s.set('vol180', {'stop_loss_pct': -4.0, 'per_position_pct': 8.0})
    s2 = RiskStore(path)
    cfg = s2.get('vol180')
    assert cfg['stop_loss_pct'] == -4.0
    assert cfg['per_position_pct'] == 8.0
    # 未设置的部分回退默认（缺字段合并）
    assert cfg['max_positions'] == 10


def test_store_corrupt_json_falls_back(tmp_path):
    from ashare_review.risk.store import RiskStore
    p = tmp_path / 'risk.json'
    p.write_text('{broken json', encoding='utf-8')
    s = RiskStore(str(p))
    assert s.get('vol180')['stop_loss_pct'] == -6.0



def test_store_set_invalid_raises(tmp_path):
    from ashare_review.risk.store import RiskStore
    s = RiskStore(str(tmp_path / 'risk.json'))
    try:
        s.set('vol180', {'stop_loss_pct': -50.0})
        assert False, '应抛 ValueError'
    except ValueError:
        pass
    try:
        s.set('nope', {})
        assert False, '应抛 ValueError'
    except ValueError:
        pass


def test_store_get_schema_invalid_falls_back(tmp_path):
    """合法 JSON 但 schema 非法 → 回退默认并告警"""
    import logging
    from ashare_review.risk.store import RiskStore
    p = tmp_path / 'risk.json'
    p.write_text('{"vol180": {"regime_scale": 5, "stop_loss_pct": -50.0}}', encoding='utf-8')
    s = RiskStore(str(p))
    cfg = s.get('vol180')
    assert cfg['stop_loss_pct'] == -6.0          # 回退默认（-4 被丢弃）
    assert cfg['regime_scale']['强势趋势'] == 1.0


def test_store_get_unknown_portfolio(tmp_path):
    from ashare_review.risk.store import RiskStore
    s = RiskStore(str(tmp_path / 'risk.json'))
    try:
        s.get('nope')
        assert False, '应抛 ValueError'
    except ValueError:
        pass


# ---------- Task 3: 开仓判定 ----------

def _cfg(**kw):
    from ashare_review.risk.rules import DEFAULT_CONFIG
    c = dict(DEFAULT_CONFIG['vol180'])
    c.update(kw)
    return c


def test_evaluate_normal_open():
    from ashare_review.risk.evaluate import evaluate
    cfg = _cfg()
    r = evaluate(cfg, {'positions': 3, 'opened_today': 1,
                       'total_value': 1_050_000, 'history_peak': 1_100_000}, '强势趋势')
    assert r['can_open'] is True
    assert r['blocked_reasons'] == []
    assert r['suggested_size_pct'] == 10.0
    assert r['regime_scale'] == 1.0
    # 回撤 (110-105)/110 = 4.5% < 8% → 放行


def test_evaluate_drawdown_breaker():
    from ashare_review.risk.evaluate import evaluate
    cfg = _cfg()
    # 回撤 = (110-101)/110 = 8.18% ≥ 8% → 拦
    r = evaluate(cfg, {'positions': 1, 'opened_today': 0,
                       'total_value': 1_010_000, 'history_peak': 1_100_000}, '强势趋势')
    assert r['can_open'] is False
    assert any('回撤' in s for s in r['blocked_reasons'])
    # 恰好 = 8% → 触发（(110-101.2)/110=8%）
    r2 = evaluate(cfg, {'positions': 1, 'opened_today': 0,
                        'total_value': 1_012_000, 'history_peak': 1_100_000}, '强势趋势')
    assert r2['can_open'] is False
    # 无 breaker_tripped（默认 False，纯阈值路径）回撤 3.18% < 8% → 放行
    r3 = evaluate(cfg, {'positions': 1, 'opened_today': 0,
                        'total_value': 1_065_000, 'history_peak': 1_100_000}, '强势趋势')
    assert r3['can_open'] is True
    assert r3['breaker_tripped'] is False


def test_evaluate_hysteresis():
    """熔断触发后需回撤 < 恢复线(4%) 才解除（滞回）"""
    from ashare_review.risk.evaluate import evaluate
    cfg = _cfg()
    # 触发熔断
    r1 = evaluate(cfg, {'positions': 0, 'opened_today': 0, 'total_value': 1_010_000,
                        'history_peak': 1_100_000, 'breaker_tripped': False}, '强势趋势')
    assert r1['can_open'] is False and r1['breaker_tripped'] is True
    # 回撤 6%（< 熔断 8% 但 > 恢复线 4%）→ 仍拦截
    r2 = evaluate(cfg, {'positions': 0, 'opened_today': 0, 'total_value': 1_034_000,
                        'history_peak': 1_100_000, 'breaker_tripped': True}, '强势趋势')
    assert r2['can_open'] is False
    assert any('恢复线' in s for s in r2['blocked_reasons'])
    # 回撤 3%（< 恢复线 4%）→ 解除
    r3 = evaluate(cfg, {'positions': 0, 'opened_today': 0, 'total_value': 1_067_000,
                        'history_peak': 1_100_000, 'breaker_tripped': True}, '强势趋势')
    assert r3['can_open'] is True and r3['breaker_tripped'] is False


def test_evaluate_nan_total_blocks():
    """净值 NaN → 保守拦截（不绕过熔断）"""
    from ashare_review.risk.evaluate import evaluate
    cfg = _cfg()
    r = evaluate(cfg, {'positions': 0, 'opened_today': 0,
                       'total_value': float('nan'), 'history_peak': 1_000_000}, '强势趋势')
    assert r['can_open'] is False
    assert any('净值' in s for s in r['blocked_reasons'])


def test_evaluate_regime_scale():
    from ashare_review.risk.evaluate import evaluate
    cfg = _cfg()
    # 题材轮动 → 0.7 → 建议 7%
    r = evaluate(cfg, {'positions': 0, 'opened_today': 0,
                       'total_value': 1_000_000, 'history_peak': 1_000_000}, '题材轮动')
    assert r['can_open'] is True and r['suggested_size_pct'] == 7.0
    # 退潮下跌 scale=0 → 禁开仓
    r2 = evaluate(cfg, {'positions': 0, 'opened_today': 0,
                        'total_value': 1_000_000, 'history_peak': 1_000_000}, '退潮下跌')
    assert r2['can_open'] is False
    assert any('退潮' in s for s in r2['blocked_reasons'])
    # 未知 regime → 1.0 不误拦
    r3 = evaluate(cfg, {'positions': 0, 'opened_today': 0,
                        'total_value': 1_000_000, 'history_peak': 1_000_000}, '未知行情')
    assert r3['can_open'] is True and r3['regime_scale'] == 1.0


def test_evaluate_limits_and_multi():
    from ashare_review.risk.evaluate import evaluate
    cfg = _cfg()
    r = evaluate(cfg, {'positions': 10, 'opened_today': 3,
                       'total_value': 900_000, 'history_peak': 1_100_000}, '退潮下跌')
    assert r['can_open'] is False
    reasons = '；'.join(r['blocked_reasons'])
    assert '回撤' in reasons and '退潮' in reasons and '持仓数' in reasons and '新开' in reasons


def test_stop_loss_pct():
    from ashare_review.risk.evaluate import stop_loss_pct
    assert stop_loss_pct({'stop_loss_pct': -6.0}) == -6.0



# ---------- Task 4: Vol180 接入 ----------

class FakeTdx2:
    """可控日线：code -> DataFrame(trade_date/open/high/low/close/volume)"""
    def __init__(self, data):
        self.data = data  # {code: [(date, open, close), ...]}

    def read_daily(self, code, market):
        import pandas as pd
        from datetime import datetime
        bars = self.data.get(str(code))
        if not bars:
            return pd.DataFrame()
        rows = [{'trade_date': datetime.strptime(d, '%Y-%m-%d').date(),
                 'open': o, 'high': o, 'low': c, 'close': c, 'volume': 100}
                for d, o, c in bars]
        return pd.DataFrame(rows).sort_values('trade_date').reset_index(drop=True)


def _vol180_portfolio(tmp_path, monkeypatch, config_path=None):
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    import tempfile
    path = config_path or str(tmp_path / 'risk.json')
    monkeypatch.setenv('RISK_CONFIG', path)
    p = Vol180SimPortfolio()
    p._state['holding'] = {}          # 清空真实状态，避免污染
    return p


def test_vol180_stop_loss_default_unchanged(tmp_path, monkeypatch):
    """默认配置 -6%：跌 5% 不止损，跌 7% 止损（与现状一致）"""
    from ashare_review.risk.store import RiskStore
    path = str(tmp_path / 'risk.json')
    RiskStore(path).set('vol180', {})   # 写默认
    p = _vol180_portfolio(tmp_path, monkeypatch, path)
    p.tdx = FakeTdx2({'600001': [('2026-08-10', 10.0, 10.0), ('2026-08-11', 10.0, 9.5),
                                 ('2026-08-12', 10.0, 9.3)]})   # 最新 9.3 → -7%
    p._state['holding'] = {'600001': {'buy_date': '2026-08-10', 'buy_price': 10.0, 'had_zt': False}}
    sell = p._check_sell_vol180('600001', p._state['holding']['600001'], '2026-08-12')
    assert sell is not None and '止损' in sell['sell_reason']
    # 跌 5%：最新 9.5 → 不止损
    p.tdx = FakeTdx2({'600001': [('2026-08-10', 10.0, 10.0), ('2026-08-11', 10.0, 10.0),
                                 ('2026-08-12', 10.0, 9.5)]})
    sell2 = p._check_sell_vol180('600001', p._state['holding']['600001'], '2026-08-12')
    assert sell2 is None


def test_vol180_stop_loss_config_changes_behavior(tmp_path, monkeypatch):
    """改配置止损 -3%：跌 5% 即触发（验证配置真正生效）"""
    from ashare_review.risk.store import RiskStore
    path = str(tmp_path / 'risk.json')
    RiskStore(path).set('vol180', {'stop_loss_pct': -3.0})
    p = _vol180_portfolio(tmp_path, monkeypatch, path)
    p.tdx = FakeTdx2({'600001': [('2026-08-10', 10.0, 10.0), ('2026-08-11', 10.0, 10.0),
                                 ('2026-08-12', 10.0, 9.5)]})   # -5% ≥ 3% 线
    p._state['holding'] = {'600001': {'buy_date': '2026-08-10', 'buy_price': 10.0, 'had_zt': False}}
    sell = p._check_sell_vol180('600001', p._state['holding']['600001'], '2026-08-12')
    assert sell is not None and '止损' in sell['sell_reason']


# ---------- Task 5: ZTReplica 接入 ----------

def test_zt_replica_stop_loss_config(tmp_path, monkeypatch):
    """默认 -5%：跌 4% 不止损；改配置 -3% 后跌 4% 触发"""
    from ashare_review.risk.store import RiskStore
    from ashare_review.tools.zt_replica_portfolio import ZTReplicaSimPortfolio
    path = str(tmp_path / 'risk.json')
    RiskStore(path).set('zt_replica', {})   # 默认 -5%
    monkeypatch.setenv('RISK_CONFIG', path)
    p = ZTReplicaSimPortfolio()
    p.tdx = FakeTdx2({'600001': [('2026-08-10', 10.0, 10.0), ('2026-08-11', 10.0, 10.0),
                                 ('2026-08-12', 10.0, 9.6)]})   # -4%
    pos = {'buy_date': '2026-08-10', 'buy_price': 10.0, 'had_zt': False, 'highest_close': 10.0}
    sell = p._check_sell('600001', pos, '2026-08-12')
    assert sell is None                       # -4% > -5% → 不止损
    # 改 -3%
    RiskStore(path).set('zt_replica', {'stop_loss_pct': -3.0})
    p2 = ZTReplicaSimPortfolio()
    p2.tdx = FakeTdx2({'600001': [('2026-08-10', 10.0, 10.0), ('2026-08-11', 10.0, 10.0),
                                  ('2026-08-12', 10.0, 9.6)]})
    sell2 = p2._check_sell('600001', pos, '2026-08-12')
    assert sell2 is not None and '止损' in sell2['sell_reason']
