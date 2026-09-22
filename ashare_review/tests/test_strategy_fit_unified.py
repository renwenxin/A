# -*- coding: utf-8 -*-
"""strategy_fit 统一口径判定的离线单元测试（不联网、不读工作簿）。

只针对 `_verdict_unified` 的 9 条判定顺序：用合成输入逐条命中，
并验证四个稳健性轴（跨版本 / 跨阈值 / 跨时点 / 跨口径）**各自单独翻转**都必须判「不稳」。

末尾几支还从合成逐笔端到端跑一遍 **跨口径轴的真身**（`_matrix_from_trades` +
`_regime_matrix_from_trades` + `_cross_caliber`），证明它不再是恒真的空轴：
  · 某广度状态自己的笔均为正，但它的成交大多落在 regime 口径评分为负的档位上 → ok False → 不稳；
  · 两边同向为正 → ok True → 不因这条轴判「不稳」。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ashare_review.analysis.strategy_fit import (  # noqa: E402
    MIN_N_ANY, MIN_N_STATS, STATES, _cross_caliber, _flipped, _matrix_from_trades,
    _regime_matrix_from_trades, _verdict_unified,
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


# ══════════════ 跨口径轴的真身（合成逐笔，端到端） ══════════════
# 旧实现把「该格成交」再按 regime 分桶、取子桶笔均的 n 加权平均 —— 同一批成交的两种分桶，
# 加权平均**恒等于**该格笔均，ok 恒为 True，这条轴永远不会判「不稳」。
# 新实现的期望侧来自 regime 口径在**全样本**上的各档笔均，因此可以真的翻号。

CELL_STATE = '强势主升'
FILL_STATE = '弱势震荡'


def _df(rows):
    """rows = [(key, ret, regime)] → 与 _load_trades 同结构的 DataFrame"""
    return pd.DataFrame(dict(key=[r[0] for r in rows], ret=[float(r[1]) for r in rows],
                             regime=[r[2] for r in rows]))


def _universe(cell_rows, other_rows):
    """合成一个战法的全样本：cell_rows 落在 CELL_STATE，other_rows 落在 FILL_STATE。

    返回 (cross_cell, breadth_cell, regime_matrix, cell_df) —— 完全走真实调用链：
    _matrix_from_trades / _regime_matrix_from_trades / _cross_caliber。
    """
    rows = list(cell_rows) + list(other_rows)
    trades = {'启动突破V3': _df(rows)}
    states = {}
    for k, _, _ in cell_rows:
        states[k] = CELL_STATE
    for k, _, _ in other_rows:
        states[k] = FILL_STATE
    m_main = _matrix_from_trades(trades, states)
    m_regime = _regime_matrix_from_trades(trades)
    cross = _cross_caliber(trades, states, m_main, m_regime)['启动突破V3'][CELL_STATE]
    return cross, m_main['启动突破V3'][CELL_STATE], m_regime['启动突破V3'], _df(cell_rows)


def test_cross_caliber_can_flip_sign_and_drive_unstable():
    # 这一格（启动突破V3 × 强势主升）：20 笔，笔均 +1.00%（广度口径为正）
    cell = [('c%d' % i, 1.0, '强势趋势') for i in range(10)] + \
           [('c1%d' % i, 1.0, '震荡观望') for i in range(10)]
    # 但同一战法的全样本里，这两档 regime 自己的笔均都是 -1.00%
    other = [('d%d' % i, -3.0, '强势趋势') for i in range(10)] + \
            [('d1%d' % i, -3.0, '震荡观望') for i in range(10)]
    cross, breadth_cell, m_regime, cell_df = _universe(cell, other)

    # 证据侧：regime 口径自己的各档笔均（全样本）确实为负
    assert m_regime['强势趋势']['avg'] == -1.0 and m_regime['强势趋势']['n'] == 20
    assert m_regime['震荡观望']['avg'] == -1.0 and m_regime['震荡观望']['n'] == 20

    assert breadth_cell['avg'] == 1.0 and breadth_cell['n'] == 20
    assert cross['breadth_avg'] == 1.0
    assert cross['regime_avg'] == -1.0          # 全为负档 → 加权期望为负
    assert cross['n_regimes'] == 2
    assert cross['ok'] is False                 # 两个口径对同一批成交的环境判断相反

    # 旧算法在这里只会得到 +1.00% ≡ 该格笔均（符号永远相同）——证明这才是真正被修掉的东西
    gs = list(cell_df.groupby('regime'))
    old = np.average([g['ret'].mean() for _, g in gs], weights=[len(g) for _, g in gs])
    assert abs(float(old) - breadth_cell['avg']) < 1e-9

    # 该格因此被判「不稳」，原因是跨口径
    r = _verdict_unified(_cell(n=20, win=60.0, avg=cross['breadth_avg']),
                         _stab(), _sens(), _conv(), cross)
    assert r['level'] == '不稳'
    assert '跨口径' in r['reason']
    assert '+1.00' in r['reason'] and '-1.00' in r['reason']


def test_cross_caliber_same_sign_does_not_trigger_unstable():
    # 反向场景：该格笔均 +0.75%，其 regime 构成在全样本上也都是正的 → 同向
    cell = [('c%d' % i, 1.0, '强势趋势') for i in range(10)] + \
           [('c1%d' % i, 0.5, '题材轮动') for i in range(10)]
    other = [('d%d' % i, 2.0, '强势趋势') for i in range(10)] + \
            [('d1%d' % i, 1.2, '题材轮动') for i in range(10)]
    cross, breadth_cell, m_regime, _ = _universe(cell, other)

    assert m_regime['强势趋势']['avg'] > 0 and m_regime['题材轮动']['avg'] > 0
    assert breadth_cell['avg'] > 0 and cross['regime_avg'] > 0
    assert cross['n_regimes'] == 2
    assert cross['ok'] is True

    r = _verdict_unified(_cell(n=20, win=60.0, avg=cross['breadth_avg']),
                         _stab(), _sens(), _conv(), cross)
    assert r['level'] != '不稳'
    assert '跨口径' not in r['reason']


def test_cross_caliber_without_regime_stats_is_none_not_flip():
    # regime 口径全样本缺该战法的统计（无 avg_regime）→ 样本不足，不算翻转，不能判「不稳」
    cell = [('c%d' % i, 1.0, '强势趋势') for i in range(10)] + \
           [('c1%d' % i, -0.5, '震荡观望') for i in range(10)]
    rows = _df(cell)
    m_main = _matrix_from_trades({'启动突破V3': rows}, {k: CELL_STATE for k, _, _ in cell})
    cross = _cross_caliber({'启动突破V3': rows},
                           {k: CELL_STATE for k, _, _ in cell}, m_main, {})['启动突破V3'][CELL_STATE]
    assert cross['regime_avg'] is None and cross['n_regimes'] == 0
    assert cross['ok'] is None
    r = _verdict_unified(_cell(n=20, win=60.0, avg=cross['breadth_avg']),
                         _stab(), _sens(), _conv(), cross)
    assert r['level'] != '不稳'


def test_regime_matrix_from_trades_matches_stats_definition():
    # avg_regime 的口径 = 「按 regime 分组、逐笔等权的笔均」，与 _stats_from 同一套聚合
    trades = {'启动突破V3': _df([('a', 3.0, '强势趋势'), ('b', -1.0, '强势趋势'),
                                 ('c', 2.0, '震荡观望'), ('d', -2.0, '震荡观望'),
                                 ('e', 5.0, '冰点超跌')])}
    m = _regime_matrix_from_trades(trades)['启动突破V3']
    assert set(m) == set(STATES)
    assert m['强势趋势'] == dict(n=2, win=50.0, avg=1.0, pf=3.0)
    assert m['震荡观望']['avg'] == 0.0
    assert m['冰点超跌'] == dict(n=1, win=100.0, avg=5.0, pf=None)
    assert m['弱市回调'] == dict(n=0, win=None, avg=None, pf=None)
