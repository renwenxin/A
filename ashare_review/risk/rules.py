"""风控规则 — 默认配置与校验"""
from typing import Dict, List

REGIMES = ['强势趋势', '题材轮动', '震荡观望', '弱市回调', '退潮下跌', '冰点超跌']

# 默认缩放系数：沿用 v3_backtest regime_weights（退潮=0 禁开仓）
DEFAULT_REGIME_SCALE = {'强势趋势': 1.0, '题材轮动': 0.7, '震荡观望': 0.3,
                        '弱市回调': 0.2, '退潮下跌': 0.0, '冰点超跌': 0.3}

DEFAULT_CONFIG: Dict[str, Dict] = {
    'vol180': {
        'stop_loss_pct': -6.0,
        'per_position_pct': 10.0,
        'max_positions': 10,
        'max_new_per_day': 3,
        'drawdown_breaker_pct': 8.0,
        'drawdown_recover_pct': 4.0,
        'regime_scale': dict(DEFAULT_REGIME_SCALE),
    },
    'zt_replica': {
        'stop_loss_pct': -5.0,
        'per_position_pct': 10.0,
        'max_positions': 10,
        'max_new_per_day': 3,
        'drawdown_breaker_pct': 8.0,
        'drawdown_recover_pct': 4.0,
        'regime_scale': dict(DEFAULT_REGIME_SCALE),
    },
}

_KEYS = ['stop_loss_pct', 'per_position_pct', 'max_positions', 'max_new_per_day',
         'drawdown_breaker_pct', 'drawdown_recover_pct']


def validate_config(portfolio_id: str, cfg: dict) -> List[str]:
    """校验配置，返回错误列表（空=合法）。"""
    errors = []
    if not isinstance(cfg, dict):
        return ['配置必须是对象']
    for key in _KEYS:
        if key not in cfg:
            errors.append(f'缺少字段: {key}')
            continue
        v = cfg[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errors.append(f'{key} 必须为数值')
            continue
        if key in ('max_positions', 'max_new_per_day'):
            if isinstance(v, bool) or not isinstance(v, int):
                errors.append(f'{key} 必须为整数')
                continue
        if key in ('stop_loss_pct',) and not (-30.0 <= v < 0):
            errors.append(f'stop_loss_pct 需在 [-30, 0) 之间（当前 {v}）')
        elif key == 'per_position_pct' and not (1.0 <= v <= 50.0):
            errors.append(f'per_position_pct 需在 1~50 之间（当前 {v}）')
        elif key in ('max_positions', 'max_new_per_day') and not (1 <= v <= 50):
            errors.append(f'{key} 需在 1~50 之间（当前 {v}）')
        elif key in ('drawdown_breaker_pct', 'drawdown_recover_pct') and not (0 < v <= 50):
            errors.append(f'{key} 需在 (0, 50] 之间（当前 {v}）')
    rs = cfg.get('regime_scale')
    if not isinstance(rs, dict):
        errors.append('缺少字段: regime_scale')
    else:
        for r in REGIMES:
            if r not in rs:
                errors.append(f'regime_scale 缺少: {r}')
            elif not isinstance(rs[r], (int, float)) or rs[r] < 0:
                errors.append(f'regime_scale[{r}] 必须 ≥0（当前 {rs.get(r)}）')
    if ('drawdown_breaker_pct' in cfg and 'drawdown_recover_pct' in cfg
            and isinstance(cfg['drawdown_breaker_pct'], (int, float))
            and isinstance(cfg['drawdown_recover_pct'], (int, float))
            and not isinstance(cfg['drawdown_breaker_pct'], bool)
            and not isinstance(cfg['drawdown_recover_pct'], bool)
            and cfg['drawdown_recover_pct'] >= cfg['drawdown_breaker_pct']):
        errors.append('drawdown_recover_pct 必须小于 drawdown_breaker_pct')
    return errors
