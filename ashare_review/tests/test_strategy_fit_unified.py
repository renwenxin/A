# -*- coding: utf-8 -*-
"""strategy_fit 统一口径判定的离线单元测试（不联网、不读工作簿）。

只针对 `_verdict_unified` 的 9 条判定顺序：用合成输入逐条命中，
并验证四个稳健性轴（跨版本 / 跨阈值 / 跨时点 / 跨口径）**各自单独翻转**都必须判「不稳」。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ashare_review.analysis.strategy_fit import (  # noqa: E402
    MIN_N_ANY, MIN_N_STATS, _flipped, _verdict_unified,
)


def _cell(n=100, win=60.0, avg=2.0, pf=3.0):
    return dict(n=n, win=win, avg=avg, pf=pf)


def _stab(n_pos=3, n_neg=0, n_ver=3, avg_min=1.0, avg_max=3.0):
    return dict(n_ver=n_ver, n_pos=n_pos, n_neg=n_neg, avg_min=avg_min, avg_max=avg_max,
                n_min=10, n_max=30, consistent=(n_neg == 0))


def _sens(sign_stable=True, avg_min=1.0, avg_max=3.0):
    return dict(sign_stable=sign_stable, avg_min=avg_min, avg_max=avg_max)


def _conv(stable=True, t1=2.0, same=1.0):
    return dict(stable=stable, t1=t1, same=same)


def _cross(ok=True, breadth_avg=2.0, regime_avg=1.5, n_regimes=3):
    return dict(ok=ok, breadth_avg=breadth_avg, regime_avg=regime_avg, n_regimes=n_regimes)


def _run(v=None, stab=None, sens=None, conv=None, cross=None):
    """默认：四个轴全部通过、样本充足、胜率 60% → 首选。只改要测的那一项。"""
    return _verdict_unified(
        _cell() if v is None else v,
        _stab() if stab is None else stab,
        _sens() if sens is None else sens,
        _conv() if conv is None else conv,
        _cross() if cross is None else cross)


# ── 第 1 步：样本不足（也是最高优先级） ──
def test_step1_no_or_tiny_sample():
    assert _run(v=dict(n=0, win=None, avg=None, pf=None))['level'] == '样本不足'
    assert _run(v={})['level'] == '样本不足'
    r = _run(v=_cell(n=MIN_N_ANY - 1, win=90.0, avg=9.9))
    assert r['level'] == '样本不足'
    assert str(MIN_N_ANY) in r['reason']


def test_step1_beats_all_four_flipped_axes():
    r = _run(v=_cell(n=3, win=90.0, avg=9.0), stab=_stab(1, 2, 3),
             sens=_sens(False), conv=_conv(False), cross=_cross(False))
    assert r['level'] == '样本不足'


# ── 四个稳健性轴：各自单独翻转 → 不稳 ──
def test_cross_version_alone_is_unstable():
    r = _run(stab=_stab(n_pos=2, n_neg=1, n_ver=3, avg_min=-1.5, avg_max=2.5))
    assert r['level'] == '不稳'
    assert '-1.5' in r['reason'] and '2.5' in r['reason']


def test_cross_timepoint_alone_is_unstable():
    r = _run(conv=_conv(stable=False, t1=2.16, same=-1.72))
    assert r['level'] == '不稳'
    assert 'T-1' in r['reason'] and '同日' in r['reason']
    assert '+2.16' in r['reason'] and '-1.72' in r['reason']


def test_cross_threshold_alone_is_unstable():
    r = _run(sens=_sens(sign_stable=False, avg_min=-0.85, avg_max=0.44))
    assert r['level'] == '不稳'
    assert '-0.85' in r['reason'] and '0.44' in r['reason']


def test_cross_caliber_alone_is_unstable():
    r = _run(cross=_cross(ok=False, breadth_avg=2.16, regime_avg=-1.72, n_regimes=5))
    assert r['level'] == '不稳'
    assert '+2.16' in r['reason'] and '-1.72' in r['reason']
    assert '5' in r['reason']


def test_single_backtest_version_is_not_a_flip():
    # n_ver<=1 时无从谈"跨版本不一致"
    assert _run(stab=_stab(n_pos=1, n_neg=0, n_ver=1))['level'] == '首选'


def test_axis_none_means_insufficient_not_flip():
    # 三轴样本不足（None）不算翻转，结论回落到正常灯
    r = _run(sens=dict(sign_stable=None, avg_min=None, avg_max=None),
             conv=dict(stable=None, t1=None, same=None),
             cross=dict(ok=None, breadth_avg=None, regime_avg=None, n_regimes=0))
    assert r['level'] == '首选'


def test_numpy_bool_axes_still_flip():
    # pandas/numpy 比较得到 numpy.bool_，`is False` 恒不成立 → 必须用宽松判断
    assert _run(conv=dict(stable=np.bool_(False), t1=2.0, same=-1.0))['level'] == '不稳'
    assert _run(sens=dict(sign_stable=np.bool_(False), avg_min=-1.0, avg_max=0.5))['level'] == '不稳'
    assert _run(cross=dict(ok=np.bool_(False), breadth_avg=1.0, regime_avg=-1.0,
                           n_regimes=2))['level'] == '不稳'
    assert _flipped(np.bool_(False)) is True
    assert _flipped(np.bool_(True)) is False
    assert _flipped(None) is False


# ── 轴的优先级（顺序即优先级，第一条命中为止） ──
def test_axis_priority_version_then_timepoint_then_threshold():
    r = _run(stab=_stab(1, 2, 3), conv=_conv(False), sens=_sens(False), cross=_cross(False))
    assert '跨版本' in r['reason']
    r = _run(conv=_conv(False), sens=_sens(False), cross=_cross(False))
    assert '跨时点' in r['reason']
    r = _run(sens=_sens(False), cross=_cross(False))
    assert '跨阈值' in r['reason']
    r = _run(cross=_cross(False))
    assert '跨口径' in r['reason']


# ── 第 6~9 步：停用 / 首选 / 可用 / 谨慎 ──
def test_step6_stop_when_avg_not_positive():
    r = _run(v=_cell(n=MIN_N_STATS, win=40.0, avg=-0.5))
    assert r['level'] == '停用'
    assert '-0.50' in r['reason']
    # 负期望但样本不足 → 样本不足，不是停用
    r2 = _run(v=_cell(n=MIN_N_ANY, win=40.0, avg=-0.5))
    assert r2['level'] == '样本不足'
    # avg == 0 也算非正
    assert _run(v=_cell(n=MIN_N_STATS, win=40.0, avg=0.0))['level'] == '停用'


def test_step7_first_choice_needs_n_and_win():
    assert _run(v=_cell(n=MIN_N_STATS, win=50.0, avg=1.0))['level'] == '首选'
    assert _run(v=_cell(n=MIN_N_STATS, win=49.9, avg=1.0))['level'] == '可用'
    # 胜率够但 n<30 → 降级为可用
    assert _run(v=_cell(n=MIN_N_ANY, win=50.0, avg=1.0))['level'] == '可用'


def test_step8_usable():
    r = _run(v=_cell(n=100, win=46.0, avg=0.8))
    assert r['level'] == '可用'
    assert '46.0' in r['reason']


def test_step9_caution_keeps_payoff_ratio_wording():
    r = _run(v=_cell(n=100, win=40.0, avg=0.8, pf=2.5))
    assert r['level'] == '谨慎'
    assert '盈亏比' in r['reason'] and '2.50' in r['reason']
