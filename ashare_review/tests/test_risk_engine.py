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
