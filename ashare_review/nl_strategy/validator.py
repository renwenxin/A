# ashare_review/nl_strategy/validator.py
"""策略参数合法性校验"""
from .spec import StrategySpec, StrategyCondition, VALID_CONDITIONS


def validate_spec(spec: StrategySpec) -> list[str]:
    """校验 StrategySpec，返回错误列表（空=合法）"""
    errors = []
    if not spec.name:
        errors.append('策略名称为空')
    if not spec.conditions:
        errors.append('策略条件为空')
    for i, cond in enumerate(spec.conditions):
        if cond.type not in VALID_CONDITIONS:
            errors.append(f'条件{i}: 未知类型 {cond.type}')
            continue
    if spec.max_results < 1 or spec.max_results > 100:
        errors.append(f'max_results 需在1-100之间，当前{spec.max_results}')
    if spec.universe not in ('all', 'csi300', 'zz500', 'gem', 'main'):
        errors.append(f'未知universe: {spec.universe}')
    return errors
