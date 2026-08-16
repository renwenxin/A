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
