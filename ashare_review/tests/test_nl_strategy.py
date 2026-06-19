"""自然语言策略模块单元测试"""
import pytest
from ashare_review.nl_strategy.spec import StrategySpec, StrategyCondition, VALID_CONDITIONS
from ashare_review.nl_strategy.templates import BUILTIN_TEMPLATES
from ashare_review.nl_strategy.validator import validate_spec


class TestStrategySpec:
    def test_creation(self):
        cond = StrategyCondition('volume_ratio', {'min': 1.5}, weight=1.0)
        spec = StrategySpec(name='测试', description='测试描述',
                           conditions=[cond], max_results=15)
        assert spec.name == '测试'
        assert len(spec.conditions) == 1

    def test_to_dict_and_back(self):
        spec = StrategySpec(
            name='测试策略',
            conditions=[
                StrategyCondition('ma_breakout', {'period': 20}),
                StrategyCondition('exclude_st', {}),
            ],
            universe='all', max_results=20,
        )
        d = spec.to_dict()
        restored = StrategySpec.from_dict(d)
        assert restored.name == '测试策略'
        assert len(restored.conditions) == 2


class TestTemplates:
    def test_all_templates_valid(self):
        assert len(BUILTIN_TEMPLATES) == 5
        for tid, spec in BUILTIN_TEMPLATES.items():
            errors = validate_spec(spec)
            assert errors == [], f'{tid}: {errors}'

    def test_template_conditions(self):
        vol = BUILTIN_TEMPLATES['vol_breakout']
        types = [c.type for c in vol.conditions]
        assert 'ma_breakout' in types
        assert 'volume_ratio' in types
        assert 'exclude_st' in types


class TestValidator:
    def test_valid_spec(self):
        spec = BUILTIN_TEMPLATES['auction_surge']
        assert validate_spec(spec) == []

    def test_empty_conditions(self):
        spec = StrategySpec(name='空', conditions=[])
        errors = validate_spec(spec)
        assert len(errors) > 0

    def test_unknown_condition_type(self):
        spec = StrategySpec(name='错误',
                           conditions=[StrategyCondition('not_exist', {})])
        errors = validate_spec(spec)
        assert len(errors) > 0
