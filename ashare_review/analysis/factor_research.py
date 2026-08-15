"""
因子研究框架 — Factor Research Framework

通用化、可复用的因子分析工具。对任意因子做统一的分桶统计，
输出胜率/均收益/大赚概率(>10%)/大亏概率(<-5%)，自动找最佳区间。

设计原则：
- 证据驱动：让数据决定因子有效性，而非人为设计权重
- 通用性：一个 analyze_factor() 函数处理所有因子
- 可复用：以后每发现一个新因子，直接调同一套框架
- 基线保护：不修改任何现有评分代码，V2 保持不变

使用方式：
  # 1. 内联调用（在回测脚本中）
  from ashare_review.analysis.factor_research import analyze_factor, print_report
  analysis = analyze_factor('pullback_pct', df_fixed)
  print_report(analysis)

  # 2. 批量分析
  from ashare_review.analysis.factor_research import analyze_all_factors, print_report
  for a in analyze_all_factors(df_fixed, ['pullback_pct', 'vol_shrink_ratio', ...]):
      print_report(a)

  # 3. 样本内/外拆分
  is_a, oos_a = analyze_factor('pullback_pct', df_fixed, split_date='2025-06-30')

  # 4. CLI 独立使用
  python -m ashare_review.analysis.factor_research --input trades.csv --factors "pullback_pct,vol_shrink_ratio"
"""

import sys, os, json, argparse, ast, warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

# ── Matplotlib (optional, for charts) ──
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ── 常量 ──────────────────────────────────────────────────────────────
DEFAULT_N_BUCKETS = 10
DEFAULT_BIG_WIN = 10.0    # >10% = big win
DEFAULT_BIG_LOSS = -5.0   # <-5% = big loss
MIN_SAMPLES_BUCKET = 5    # 单桶最少样本数
WINDOW_MIN_PCT = 0.15     # 最佳区间最少覆盖 15% 数据

# v2_factors 止跌K线分数 → 形态名称映射
KLINE_SCORE_TO_PATTERN = {7: 'engulf', 5: 'hammer', 3: 'doji'}

# 非因子列（批量分析时自动排除）
META_COLUMNS = {
    'code', 'name', 'buy_date', 'sell_date', 'signal_date', 'check_date',
    'exit_reason', 'exit_mode', 'conditions', 'industry', 'is_win',
    'gross_ret', 'net_ret', 'buy_price', 'sell_price',
    'pb_open', 'pb_high', 'pb_low', 'pb_close', 'pb_vol',
    'high_break', 'close', 'volume', 'ma5', 'ma10', 'ma20',
}

# ═══════════════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BucketResult:
    """单桶统计结果"""
    bucket_label: str       # e.g. "[1.0, 2.0)", "S", "hammer"
    count: int
    win_rate: float         # %
    avg_ret: float          # mean net_ret %
    median_ret: float       # median net_ret %
    big_win_prob: float     # % trades with net_ret > big_win_threshold
    big_loss_prob: float    # % trades with net_ret < big_loss_threshold
    bucket_min: float = None    # raw factor value lower bound (continuous)
    bucket_max: float = None    # raw factor value upper bound (continuous)
    bucket_center: float = None # midpoint for chart x-axis

    def to_dict(self) -> dict:
        return {
            'bucket': self.bucket_label,
            'count': self.count,
            'win_rate': round(self.win_rate, 1),
            'avg_ret': round(self.avg_ret, 2),
            'median_ret': round(self.median_ret, 2),
            'big_win_prob': round(self.big_win_prob, 1),
            'big_loss_prob': round(self.big_loss_prob, 1),
        }


@dataclass
class FactorAnalysis:
    """单因子完整分析结果"""
    factor_name: str
    factor_type: str         # 'continuous' | 'categorical'
    n_samples: int
    n_buckets: int
    buckets: List[BucketResult] = field(default_factory=list)
    best_interval: Optional[Tuple[float, float]] = None   # (lower, upper) or None
    best_interval_stats: Dict = field(default_factory=dict)  # {count, avg_ret, win_rate, big_win_prob}
    ic_correlation: Optional[float] = None
    overall_mean_ret: float = 0.0
    overall_win_rate: float = 0.0
    target: str = 'net_ret'
    big_win_threshold: float = DEFAULT_BIG_WIN
    big_loss_threshold: float = DEFAULT_BIG_LOSS

    def to_dataframe(self) -> pd.DataFrame:
        """将桶结果转为 DataFrame"""
        return pd.DataFrame([b.to_dict() for b in self.buckets])

    def to_summary_dict(self) -> dict:
        return {
            'factor': self.factor_name,
            'type': self.factor_type,
            'n': self.n_samples,
            'buckets': self.n_buckets,
            'best_interval': f'{self.best_interval[0]:.2f}~{self.best_interval[1]:.2f}' if self.best_interval else 'N/A',
            'best_avg_ret': round(self.best_interval_stats.get('avg_ret', 0), 2),
            'best_win_rate': round(self.best_interval_stats.get('win_rate', 0), 1),
            'best_big_win': round(self.best_interval_stats.get('big_win_prob', 0), 1),
            'ic': round(self.ic_correlation, 3) if self.ic_correlation is not None else 'N/A',
            'overall_avg_ret': round(self.overall_mean_ret, 2),
            'overall_win_rate': round(self.overall_win_rate, 1),
        }


@dataclass
class StabilityResult:
    """因子稳定性分析结果 — 按 groupby 维度拆分统计"""
    factor_name: str
    groupby: str = 'year'                    # 'year' | 'quarter' | 'month' | 'market_regime'
    n_groups: int = 0
    groups: List[Dict] = field(default_factory=list)  # [{label, n, avg_ret, win_rate, ic, big_win_prob}]
    stability_ratio: float = 0.0             # std(avg_ret) / abs(mean(avg_ret)), 越小越稳定
    ic_mean: float = 0.0                     # 跨周期平均 IC
    ic_std: float = 0.0                      # 跨周期 IC 标准差
    ic_stability: float = 0.0                # ic_mean / ic_std (IC 信息比), 越大越稳定
    ic_timeline: List[Dict] = field(default_factory=list)  # [{period, ic}] for IC timeline chart
    overall_n: int = 0

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.groups)

    def to_summary_dict(self) -> dict:
        return {
            'factor': self.factor_name,
            'groupby': self.groupby,
            'n_groups': self.n_groups,
            'stability_ratio': round(self.stability_ratio, 3),
            'ic_mean': round(self.ic_mean, 3),
            'ic_std': round(self.ic_std, 3),
            'ic_stability': round(self.ic_stability, 2),
            'overall_n': self.overall_n,
        }


@dataclass
class CorrelationMatrix:
    """因子相关性矩阵"""
    factors: List[str] = field(default_factory=list)
    pearson: pd.DataFrame = None        # Pearson 相关矩阵
    spearman: pd.DataFrame = None       # Spearman 秩相关矩阵
    high_corr_pairs: List[Dict] = field(default_factory=list)  # [{f1, f2, pearson, spearman, warning}]
    n_samples: int = 0

    def get_redundant_pairs(self, spearman_threshold: float = 0.70) -> List[Dict]:
        """返回 Spearman 秩相关超过阈值的冗余因子对。"""
        return [p for p in self.high_corr_pairs
                if p.get('spearman', 0) >= spearman_threshold]


@dataclass
class AlphaScorecard:
    """因子 Alpha 综合评分"""
    factor_name: str
    ic_score: float = 0.0           # IC 维度 (0-30): 基于 IC 绝对值
    stability_score: float = 0.0    # 稳定性维度 (0-25): 基于 IC 跨周期稳定性
    spread_score: float = 0.0       # 区分度维度 (0-20): 基于 Top/Bottom bucket 价差
    sample_score: float = 0.0       # 样本维度 (0-15): 基于样本量
    correlation_penalty: float = 0.0  # 相关性惩罚 (0-10): 与已有因子平均相关度
    total_score: float = 0.0        # 总分 (0-100)
    details: Dict = field(default_factory=dict)

    @staticmethod
    def compute(
        factor_name: str,
        analysis: 'FactorAnalysis' = None,
        stability: 'StabilityResult' = None,
        corr_matrix: 'CorrelationMatrix' = None,
    ) -> 'AlphaScorecard':
        """从分析结果中计算综合 Alpha 评分。"""
        sc = AlphaScorecard(factor_name=factor_name)

        # ── IC 维度 (0-30) ──
        if analysis is not None and analysis.ic_correlation is not None:
            abs_ic = abs(analysis.ic_correlation)
            if abs_ic >= 0.15:
                sc.ic_score = 30
            elif abs_ic >= 0.10:
                sc.ic_score = 22
            elif abs_ic >= 0.05:
                sc.ic_score = 14
            elif abs_ic >= 0.02:
                sc.ic_score = 7
            else:
                sc.ic_score = 2
            sc.details['ic'] = round(analysis.ic_correlation, 3)
        else:
            sc.details['ic'] = None

        # ── 稳定性维度 (0-25) ──
        if stability is not None and stability.n_groups >= 3:
            ic_stab = stability.ic_stability  # IC 信息比
            if ic_stab >= 3.0:
                sc.stability_score = 25
            elif ic_stab >= 2.0:
                sc.stability_score = 20
            elif ic_stab >= 1.0:
                sc.stability_score = 14
            elif ic_stab >= 0.5:
                sc.stability_score = 8
            else:
                sc.stability_score = 3
            sc.details['ic_stability'] = round(ic_stab, 2)
            sc.details['n_groups'] = stability.n_groups
        else:
            sc.stability_score = 0
            sc.details['ic_stability'] = None

        # ── 区分度维度 (0-20): Top-Bottom bucket avg_ret spread ──
        if analysis is not None and len(analysis.buckets) >= 3:
            top_ret = analysis.buckets[-1].avg_ret
            bot_ret = analysis.buckets[0].avg_ret
            spread = top_ret - bot_ret
            if spread >= 4.0:
                sc.spread_score = 20
            elif spread >= 2.5:
                sc.spread_score = 16
            elif spread >= 1.5:
                sc.spread_score = 12
            elif spread >= 0.8:
                sc.spread_score = 7
            elif spread > 0:
                sc.spread_score = 3
            else:
                sc.spread_score = 0  # 负区分度/无区分度
            sc.details['spread'] = round(spread, 2)
        else:
            sc.details['spread'] = None

        # ── 样本维度 (0-15) ──
        if analysis is not None:
            n = analysis.n_samples
            if n >= 5000:
                sc.sample_score = 15
            elif n >= 2000:
                sc.sample_score = 12
            elif n >= 1000:
                sc.sample_score = 9
            elif n >= 500:
                sc.sample_score = 6
            elif n >= 200:
                sc.sample_score = 3
            else:
                sc.sample_score = 1
            sc.details['n_samples'] = n
        else:
            sc.details['n_samples'] = 0

        # ── 相关性惩罚 (0-10): 评分越高相关度越低越好 ──
        if corr_matrix is not None and factor_name in corr_matrix.factors:
            idx = corr_matrix.factors.index(factor_name)
            # 取该因子与所有其他因子的平均绝对 Spearman 相关
            if corr_matrix.spearman is not None:
                row = corr_matrix.spearman.iloc[idx].drop(factor_name, errors='ignore')
                avg_corr = row.abs().mean() if len(row) > 0 else 0
                if avg_corr < 0.10:
                    sc.correlation_penalty = 0    # 高度独立 — 无惩罚
                elif avg_corr < 0.25:
                    sc.correlation_penalty = 3
                elif avg_corr < 0.40:
                    sc.correlation_penalty = 6
                elif avg_corr < 0.60:
                    sc.correlation_penalty = 8
                else:
                    sc.correlation_penalty = 10   # 与大多数因子高度相关
                sc.details['avg_spearman'] = round(avg_corr, 3)
            else:
                sc.details['avg_spearman'] = None
        else:
            sc.details['avg_spearman'] = None

        sc.total_score = min(round(
            sc.ic_score +
            sc.stability_score +
            sc.spread_score +
            sc.sample_score -
            sc.correlation_penalty
        ), 100)
        sc.total_score = max(sc.total_score, 0)

        return sc


# ═══════════════════════════════════════════════════════════════════════════════
# 因子值提取器
# ═══════════════════════════════════════════════════════════════════════════════

class FactorExtractor:
    """从交易记录 DataFrame 中提取因子值。

    支持三种解析模式（按优先级尝试）：
      1. 直接列名：'pullback_pct', 'net_ret', 'score'
      2. 嵌套字典打点访问：'v2_factors.距年高', 'features.dist_250d'
      3. 特殊映射：'trigger_type' → 从 v2_factors.止跌K线 分数反推形态名

    对于嵌套字典列（v2_factors / features），自动处理：
      - 原生 dict 值（回测直接输出）
      - 字符串化的 dict（从 CSV/Excel 重新加载后）
    """

    EXPR_SEPARATORS = set('+-*/()%')  # 表达式检测

    @classmethod
    def extract(cls, factor_name: str, df: pd.DataFrame) -> pd.Series:
        """从 DataFrame 提取因子值序列。

        Args:
            factor_name: 因子名，如 'pullback_pct' 或 'v2_factors.距年高'
            df: 交易记录 DataFrame

        Returns:
            pd.Series of factor values

        Raises:
            ValueError: 因子名在数据中找不到
        """
        # ── 模式 1: 直接列名 ──
        if factor_name in df.columns:
            return df[factor_name].copy()

        # ── 模式 2: 嵌套字典打点访问 ──
        if '.' in factor_name:
            col, key = factor_name.split('.', 1)
            if col in df.columns:
                return cls._extract_nested(col, key, df)

        # ── 模式 3: trigger_type 特殊映射 ──
        if factor_name == 'trigger_type':
            return cls._extract_trigger_type(df)

        # ── 模式 4: 尝试表达式 ──
        if any(c in factor_name for c in cls.EXPR_SEPARATORS):
            return cls._extract_expression(factor_name, df)

        # ── 未找到 ──
        available = [c for c in df.columns if not c.startswith('_')]
        raise ValueError(
            f"Factor '{factor_name}' not found in data.\n"
            f"Available columns ({len(available)}): {', '.join(available[:30])}"
            f"{'...' if len(available) > 30 else ''}"
        )

    @classmethod
    def _extract_nested(cls, column: str, key: str, df: pd.DataFrame) -> pd.Series:
        """从嵌套字典列中提取指定 key 的值。"""
        series = df[column]
        result = pd.Series(np.nan, index=df.index, dtype=float)

        for i, val in series.items():
            d = cls._safe_parse_dict(val)
            if isinstance(d, dict):
                v = d.get(key)
                if v is not None:
                    try:
                        result.iloc[i] = float(v)
                    except (ValueError, TypeError):
                        result.iloc[i] = v
        return result

    @classmethod
    def _extract_trigger_type(cls, df: pd.DataFrame) -> pd.Series:
        """从 v2_factors.止跌K线 反推 K线形态名称。"""
        score_series = cls._extract_nested('v2_factors', '止跌K线', df)
        return score_series.map(lambda s: KLINE_SCORE_TO_PATTERN.get(int(s), 'none') if pd.notna(s) else 'unknown')

    @classmethod
    def _extract_expression(cls, expr: str, df: pd.DataFrame) -> pd.Series:
        """计算表达式。优先 pandas 表达式；回退到安全的嵌套列访问（不使用 eval）。"""
        import re as _re
        expr = expr.strip()
        # 安全守卫：拒绝双下划线 / import 等危险模式
        if '__' in expr or 'import' in expr or 'exec' in expr:
            raise ValueError(f"拒绝的表达式: {expr}")
        # 简单实现：尝试 pd.eval
        try:
            return df.eval(expr, engine='python')
        except Exception:
            pass
        # 回退：仅允许 标识符[.标识符]* 的嵌套列访问
        if _re.fullmatch(r'[\w\u4e00-\u9fff]+(?:\.[\w\u4e00-\u9fff]+)*', expr):
            parts = expr.split('.')
            series = df[parts[0]]
            for part in parts[1:]:
                if isinstance(series, pd.DataFrame):
                    series = series[part]
                elif series.dtype == object:
                    series = series.map(lambda v: v.get(part) if isinstance(v, dict) else pd.NA)
                else:
                    break
            return series
        raise ValueError(f"Cannot evaluate expression: {expr}")

    @classmethod
    def _safe_parse_dict(cls, val) -> dict:
        """安全解析值，返回 dict。处理 NaN/None/str/dict 等各种类型。

        Args:
            val: 可能是 dict, str, float(nan), None

        Returns:
            dict (可能为空)
        """
        if isinstance(val, dict):
            return val
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return {}
        if isinstance(val, str):
            val = val.strip()
            if not val or val in ('{}', 'nan', 'None'):
                return {}
            # 尝试 1: json.loads
            try:
                return json.loads(val)
            except (json.JSONDecodeError, ValueError):
                pass
            # 尝试 2: ast.literal_eval (处理 Python dict 字符串)
            try:
                result = ast.literal_eval(val)
                if isinstance(result, dict):
                    return result
            except (ValueError, SyntaxError):
                pass
            # （已移除 eval 兜底：非字面量表达式不解析，避免任意代码执行）
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# 内部分桶和统计
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_factor_type(series: pd.Series, force_type: str = None) -> str:
    """自动检测因子类型：continuous 或 categorical。

    规则：
      - dtype 为 object/string → categorical
      - 唯一值数量 <= 8 且都是离散数值 → 视为 categorical
      - 否则 → continuous
    """
    if force_type:
        return force_type

    clean = series.dropna()
    if len(clean) == 0:
        return 'continuous'

    # 字符串类型 → categorical
    if clean.dtype == object or pd.api.types.is_string_dtype(clean.dtype):
        return 'categorical'

    # 少量离散值 → categorical
    n_unique = clean.nunique()
    if n_unique <= 8:
        # 检查是否都是离散整数
        try:
            vals = clean.astype(float)
            # 如果都是整数且范围小 → categorical
            if (vals == vals.round()).all() and vals.max() - vals.min() <= 20:
                return 'categorical'
        except (ValueError, TypeError):
            return 'categorical'

    return 'continuous'


def _categorize_continuous(values: pd.Series, n_buckets: int = 10,
                           method: str = 'quantile') -> Tuple[pd.Series, List[str], List[Tuple[float, float]]]:
    """将连续因子值分为 n_buckets 个区间。

    Returns:
        (bucket_idx_series, bucket_labels, bucket_bounds)
        - bucket_idx_series: int Series, index into labels/bounds (0-based)
        - bucket_labels: str list
        - bucket_bounds: [(low, high), ...]
    """
    clean = values.dropna()
    if len(clean) == 0:
        return pd.Series(index=values.index, dtype=int), [], []

    if method == 'equal_width':
        try:
            cat, bins = pd.cut(values, bins=n_buckets, retbins=True, duplicates='drop')
        except Exception:
            cat, bins = pd.cut(values, bins=min(n_buckets, 5), retbins=True, duplicates='drop')
    elif method == 'auto':
        try:
            cat, bins = pd.qcut(values, q=n_buckets, retbins=True, duplicates='drop')
            if len(cat.cat.categories) < max(3, n_buckets // 2):
                raise ValueError("Too few unique buckets from qcut")
        except Exception:
            cat, bins = pd.cut(values, bins=n_buckets, retbins=True, duplicates='drop')
    else:  # quantile (default)
        try:
            cat, bins = pd.qcut(values, q=n_buckets, retbins=True, duplicates='drop')
        except Exception:
            cat, bins = pd.cut(values, bins=n_buckets, retbins=True, duplicates='drop')

    # Build labels and bounds from bin edges
    labels = []
    bounds = []
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i + 1]
        labels.append(f'[{lo:.2f}, {hi:.2f})')
        bounds.append((lo, hi))

    # Convert categorical to integer index (NaN → -1, then filtered in _compute_bucket_stats)
    bucket_idx = pd.Series(cat.cat.codes, index=values.index)  # -1 for NaN
    bucket_idx = bucket_idx.where(values.notna(), -1)

    return bucket_idx, labels, bounds


def _categorize_categorical(values: pd.Series) -> Tuple[pd.Series, List[str]]:
    """将分类因子按唯一值分组。

    Returns:
        (bucket_idx_series, category_labels)
        - bucket_idx_series: int Series, 0-based index into category_labels
        - category_labels: str list of category names
    """
    clean = values.dropna()
    cats = sorted(clean.unique())
    cat_labels = [str(c) for c in cats]  # ensure string labels
    cat_to_idx = {c: i for i, c in enumerate(cats)}
    idx = pd.Series(values.map(lambda x: cat_to_idx.get(x, -1)), index=values.index)
    return idx, cat_labels


def _compute_bucket_stats(factor_values: pd.Series, target_values: pd.Series,
                          bucket_assignment: pd.Series, bucket_labels: List[str],
                          bucket_bounds: List[Tuple[float, float]] = None,
                          big_win: float = DEFAULT_BIG_WIN,
                          big_loss: float = DEFAULT_BIG_LOSS) -> List[BucketResult]:
    """对每个桶计算统计指标。

    bucket_assignment: int Series (0..n_buckets-1, -1 for NaN)
    bucket_labels: name for each bucket index
    """
    results = []
    n = len(bucket_labels)

    for i in range(n):
        mask = bucket_assignment == i
        sub_target = target_values[mask.values].dropna()

        if len(sub_target) < MIN_SAMPLES_BUCKET:
            continue

        count = len(sub_target)
        wins = (sub_target > 0).sum()
        big_wins = (sub_target > big_win).sum()
        big_losses = (sub_target < big_loss).sum()

        lo, hi = (bucket_bounds[i] if bucket_bounds
                  else (float(i), float(i + 1)))

        results.append(BucketResult(
            bucket_label=bucket_labels[i],
            count=count,
            win_rate=wins / count * 100,
            avg_ret=sub_target.mean(),
            median_ret=sub_target.median(),
            big_win_prob=big_wins / count * 100,
            big_loss_prob=big_losses / count * 100,
            bucket_min=lo,
            bucket_max=hi,
            bucket_center=(lo + hi) / 2,
        ))

    return results


def _find_best_interval(buckets: List[BucketResult],
                        min_samples: int) -> Tuple[int, int, float]:
    """滑动窗口找最佳连续区间（最高平均收益）。

    Returns:
        (start_idx, end_idx, avg_ret)
    """
    if not buckets:
        return (0, 0, 0.0)

    best_avg = -float('inf')
    best_range = (0, 0)
    n = len(buckets)

    for i in range(n):
        cum_count = 0
        cum_weighted_ret = 0.0
        for j in range(i, n):
            cum_count += buckets[j].count
            cum_weighted_ret += buckets[j].avg_ret * buckets[j].count
            if cum_count >= min_samples:
                avg = cum_weighted_ret / cum_count
                if avg > best_avg:
                    best_avg = avg
                    best_range = (i, j)

    return (*best_range, best_avg)


def _compute_ic(factor_values: pd.Series, target_values: pd.Series) -> Optional[float]:
    """计算因子值与目标收益的 Pearson 相关系数（IC）。"""
    mask = factor_values.notna() & target_values.notna()
    if mask.sum() < 10:
        return None
    fv = pd.to_numeric(factor_values[mask], errors='coerce')
    tv = pd.to_numeric(target_values[mask], errors='coerce')
    mask2 = fv.notna() & tv.notna()
    if mask2.sum() < 10:
        return None
    return fv[mask2].corr(tv[mask2])


# ═══════════════════════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_factor(
    factor_name: str,
    data: pd.DataFrame,
    target: str = 'net_ret',
    n_buckets: int = DEFAULT_N_BUCKETS,
    method: str = 'quantile',          # 'quantile' | 'equal_width' | 'auto'
    big_win_threshold: float = DEFAULT_BIG_WIN,
    big_loss_threshold: float = DEFAULT_BIG_LOSS,
    min_samples_per_bucket: int = MIN_SAMPLES_BUCKET,
    window_min_pct: float = WINDOW_MIN_PCT,
    force_type: str = None,            # 'continuous' | 'categorical' | None(auto)
) -> FactorAnalysis:
    """分析任意因子对目标收益的预测能力。

    Args:
        factor_name: 因子名。支持：
            - 直接列名：'pullback_pct', 'vol_shrink_ratio', 'score'
            - 嵌套字典打点：'v2_factors.距年高', 'v2_factors.回踩幅度', 'features.dist_250d'
            - 形态映射：'trigger_type' (自动从 v2_factors.止跌K线 反推)
        data: 交易记录 DataFrame (来自回测输出)
        target: 目标收益列名 (默认 'net_ret')
        n_buckets: 连续因子的分桶数 (默认 10)
        method: 分桶策略 — 'quantile' | 'equal_width' | 'auto'
        big_win_threshold: 大赚阈值 (默认 >10%)
        big_loss_threshold: 大亏阈值 (默认 <-5%)
        min_samples_per_bucket: 单桶最少样本数 (少于该值则跳过)
        window_min_pct: 最佳区间最少覆盖数据比例 (默认 15%)
        force_type: 强制指定因子类型

    Returns:
        FactorAnalysis 对象

    Raises:
        ValueError: 因子名在数据中找不到
    """
    # ── 1. 提取因子值 ──
    try:
        factor_values = FactorExtractor.extract(factor_name, data)
    except ValueError as e:
        raise ValueError(f"Cannot extract factor '{factor_name}': {e}")

    # ── 2. 提取目标收益 ──
    if target not in data.columns:
        raise ValueError(f"Target column '{target}' not found. Available: {list(data.columns[:20])}...")
    target_values = data[target]

    # ── 3. 去除 NaN ──
    valid_mask = factor_values.notna() & target_values.notna()
    if valid_mask.sum() < 20:
        return FactorAnalysis(
            factor_name=factor_name,
            factor_type='continuous',
            n_samples=valid_mask.sum(),
            n_buckets=0,
        )

    fv = factor_values[valid_mask]
    tv = target_values[valid_mask]
    n_samples = len(fv)

    # ── 4. 检测因子类型 ──
    ftype = _detect_factor_type(fv, force_type)

    # ── 5. 分桶 ──
    if ftype == 'categorical':
        bucket_assignment, cat_labels = _categorize_categorical(fv)
        bucket_labels = cat_labels
        bucket_bounds = [(i, i + 1) for i in range(len(cat_labels))]
    else:
        bucket_assignment, bucket_labels, bucket_bounds = _categorize_continuous(
            fv, n_buckets=n_buckets, method=method
        )

    if not bucket_labels:
        return FactorAnalysis(
            factor_name=factor_name,
            factor_type=ftype,
            n_samples=n_samples,
            n_buckets=0,
            overall_mean_ret=tv.mean(),
            overall_win_rate=(tv > 0).mean() * 100,
        )

    # ── 6. 计算每桶统计 ──
    buckets = _compute_bucket_stats(
        fv, tv, bucket_assignment, bucket_labels, bucket_bounds,
        big_win=big_win_threshold, big_loss=big_loss_threshold,
    )
    # 过滤样本不足的桶
    buckets = [b for b in buckets if b.count >= min_samples_per_bucket]

    if not buckets:
        return FactorAnalysis(
            factor_name=factor_name,
            factor_type=ftype,
            n_samples=n_samples,
            n_buckets=0,
            overall_mean_ret=tv.mean(),
            overall_win_rate=(tv > 0).mean() * 100,
        )

    # ── 7. 找最佳区间 ──
    min_samples_for_interval = max(
        min_samples_per_bucket,
        int(n_samples * window_min_pct),
    )
    start_idx, end_idx, best_avg = _find_best_interval(buckets, min_samples_for_interval)

    # 构建最佳区间统计
    best_range_buckets = buckets[start_idx:end_idx + 1]
    best_count = sum(b.count for b in best_range_buckets)
    best_wins = sum(b.count * b.win_rate / 100 for b in best_range_buckets)
    best_big_wins = sum(b.count * b.big_win_prob / 100 for b in best_range_buckets)

    best_interval = (
        buckets[start_idx].bucket_min,
        buckets[end_idx].bucket_max,
    )
    best_stats = {
        'count': best_count,
        'avg_ret': round(best_avg, 2),
        'win_rate': round(best_wins / best_count * 100, 1) if best_count > 0 else 0,
        'big_win_prob': round(best_big_wins / best_count * 100, 1) if best_count > 0 else 0,
        'start_bucket': start_idx,
        'end_bucket': end_idx,
    }

    # ── 8. 计算 IC ──
    ic = _compute_ic(fv, tv) if ftype == 'continuous' else None

    # ── 9. 构建结果 ──
    return FactorAnalysis(
        factor_name=factor_name,
        factor_type=ftype,
        n_samples=n_samples,
        n_buckets=len(buckets),
        buckets=buckets,
        best_interval=best_interval,
        best_interval_stats=best_stats,
        ic_correlation=ic,
        overall_mean_ret=tv.mean(),
        overall_win_rate=(tv > 0).mean() * 100,
        target=target,
        big_win_threshold=big_win_threshold,
        big_loss_threshold=big_loss_threshold,
    )


def analyze_factor_split(
    factor_name: str,
    data: pd.DataFrame,
    target: str = 'net_ret',
    split_date: str = None,
    split_ratio: float = None,
    **kwargs,
) -> Tuple[FactorAnalysis, FactorAnalysis]:
    """带 IS/OOS 拆分的因子分析。

    Args:
        split_date: 信号日期在此之前为 IS（如 '2025-06-30'）
        split_ratio: 前 split_ratio 比例为 IS（如 0.7）

    Returns:
        (in_sample_analysis, out_of_sample_analysis)
    """
    if split_date and 'signal_date' in data.columns:
        is_mask = pd.to_datetime(data['signal_date']) < pd.to_datetime(split_date)
        is_data = data[is_mask].copy()
        oos_data = data[~is_mask].copy()
        is_label = f'IS (<{split_date})'
        oos_label = f'OOS (>={split_date})'
    elif split_ratio and 0 < split_ratio < 1:
        if 'signal_date' in data.columns:
            data = data.sort_values('signal_date')
        split_idx = int(len(data) * split_ratio)
        is_data = data.iloc[:split_idx].copy()
        oos_data = data.iloc[split_idx:].copy()
        is_label = f'IS (前{int(split_ratio*100)}%)'
        oos_label = f'OOS (后{int((1-split_ratio)*100)}%)'
    else:
        raise ValueError("Must provide split_date or split_ratio for IS/OOS split")

    if len(is_data) < 20:
        warnings.warn(f"In-sample data too small ({len(is_data)} trades), results may be unstable")
    if len(oos_data) < 20:
        warnings.warn(f"Out-of-sample data too small ({len(oos_data)} trades), results may be unstable")

    is_analysis = analyze_factor(factor_name, is_data, target=target, **kwargs)
    oos_analysis = analyze_factor(factor_name, oos_data, target=target, **kwargs)

    # 标记 label
    is_analysis.factor_name = f'{factor_name} [{is_label}]'
    oos_analysis.factor_name = f'{factor_name} [{oos_label}]'

    return is_analysis, oos_analysis


def analyze_all_factors(
    data: pd.DataFrame,
    factors: List[str] = None,
    target: str = 'net_ret',
    sort_by: str = 'big_win_diff',
    **kwargs,
) -> List[FactorAnalysis]:
    """批量分析所有因子。

    Args:
        data: 交易记录 DataFrame
        factors: 因子名列表。若为 None，自动发现所有非元数据的列
        target: 目标收益列名
        sort_by: 排序方式 — 'big_win_diff' | 'ic' | 'spread'
        **kwargs: 传递给 analyze_factor

    Returns:
        按信息增益排序的 FactorAnalysis 列表
    """
    if factors is None:
        # 自动发现：排除元数据列和 dict 列
        factors = []
        for col in data.columns:
            if col in META_COLUMNS or col.startswith('_'):
                continue
            # 跳过 dict 列（v2_factors, features 本身不能用，需打点访问）
            if data[col].dtype == object:
                sample = data[col].dropna().iloc[0] if len(data[col].dropna()) > 0 else None
                if isinstance(sample, dict):
                    # 展开嵌套字段
                    for key in _discover_dict_keys(data[col]):
                        factors.append(f'{col}.{key}')
                    continue
            factors.append(col)

    results = []
    for name in factors:
        try:
            analysis = analyze_factor(name, data, target=target, **kwargs)
            results.append(analysis)
        except ValueError as e:
            print(f"  [SKIP] {name}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"  [ERROR] {name}: {e}", file=sys.stderr)

    # 按信息增益排序（BigWin Diff = Top-Bucket - Bottom-Bucket）
    if sort_by == 'big_win_diff':
        def _big_win_diff(a: FactorAnalysis) -> float:
            if not a.buckets or len(a.buckets) < 2:
                return 0
            top = a.buckets[-1].big_win_prob
            bot = a.buckets[0].big_win_prob
            return top - bot
        results.sort(key=_big_win_diff, reverse=True)
    elif sort_by == 'ic' and all(a.ic_correlation is not None for a in results):
        results.sort(key=lambda a: abs(a.ic_correlation or 0), reverse=True)
    elif sort_by == 'spread':
        def _spread(a: FactorAnalysis) -> float:
            if not a.buckets or len(a.buckets) < 2:
                return 0
            return a.buckets[-1].avg_ret - a.buckets[0].avg_ret
        results.sort(key=_spread, reverse=True)

    return results


def _discover_dict_keys(series: pd.Series) -> List[str]:
    """发现嵌套 dict 列的所有 key。"""
    keys = set()
    for val in series.dropna().head(500):
        d = FactorExtractor._safe_parse_dict(val)
        if isinstance(d, dict):
            keys.update(d.keys())
        if len(keys) > 50:
            break
    return sorted(keys)


def compare_factors(analyses: List[FactorAnalysis]) -> pd.DataFrame:
    """跨因子对比表。

    Returns:
        DataFrame with columns:
        Factor | Type | N | Top AvgRet | Bot AvgRet | Spread | BestInt AvgRet | BigWin Diff | IC
    """
    rows = []
    for a in analyses:
        if not a.buckets:
            continue
        top = a.buckets[-1]
        bot = a.buckets[0]
        spread = top.avg_ret - bot.avg_ret
        big_win_diff = top.big_win_prob - bot.big_win_prob
        rows.append({
            'Factor': a.factor_name,
            'Type': a.factor_type,
            'N': a.n_samples,
            'Top_AvgRet': round(top.avg_ret, 2),
            'Bot_AvgRet': round(bot.avg_ret, 2),
            'Spread': round(spread, 2),
            'BestInt_AvgRet': round(a.best_interval_stats.get('avg_ret', 0), 2),
            'BigWin_Diff': round(big_win_diff, 1),
            'IC': round(a.ic_correlation, 3) if a.ic_correlation is not None else None,
        })
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# 因子稳定性分析 — 通用 groupby 框架
# ═══════════════════════════════════════════════════════════════════════════════

def _build_groupby_series(data: pd.DataFrame, groupby: str) -> pd.Series:
    """从 signal_date 构建分组标签 Series。

    支持: 'year' | 'quarter' | 'month' | 'year_month' | 'market_regime'
    对于 market_regime，需要 data 中有 market_regime 列。
    """
    if groupby == 'market_regime':
        if 'market_regime' in data.columns:
            return data['market_regime'].astype(str)
        # 回退：基于 signal_date 检测全市场状态需要外部数据，这里用简单启发式
        # 检查是否有 market_regime 列，没有则报错
        raise ValueError(
            "groupby='market_regime' requires 'market_regime' column in data.\n"
            "Tip: Add a market_regime column before calling (e.g. from index MA trend detection)."
        )

    if 'signal_date' not in data.columns:
        raise ValueError(f"groupby='{groupby}' requires 'signal_date' column in data")

    try:
        dt = pd.to_datetime(data['signal_date'])
    except Exception:
        dt = pd.to_datetime(data['signal_date'], errors='coerce')

    if groupby == 'year':
        return dt.dt.year.astype(str)
    elif groupby == 'quarter':
        return dt.dt.year.astype(str) + 'Q' + dt.dt.quarter.astype(str)
    elif groupby == 'month':
        return dt.dt.year.astype(str) + '-' + dt.dt.month.astype(str).str.zfill(2)
    elif groupby == 'year_month':
        return dt.dt.year.astype(str) + '-' + dt.dt.month.astype(str).str.zfill(2)
    else:
        raise ValueError(f"Unknown groupby: '{groupby}'. Supported: year, quarter, month, year_month, market_regime")


def analyze_stability(
    factor_name: str,
    data: pd.DataFrame,
    target: str = 'net_ret',
    groupby: str = 'year',               # 'year' | 'quarter' | 'month' | 'market_regime'
    min_samples_per_group: int = 20,     # 每组最少样本数
    **kwargs,
) -> StabilityResult:
    """分析因子在不同时间段/市场状态下的稳定性。

    对每个 group 分别计算 IC、均收益、胜率、大赚概率，
    然后汇总为跨周期稳定性指标。

    Args:
        factor_name: 因子名
        data: 交易记录 DataFrame (需含 'signal_date' 列)
        target: 目标收益列名
        groupby: 分组维度 — 'year' | 'quarter' | 'month' | 'market_regime'
        min_samples_per_group: 每组最少样本数

    Returns:
        StabilityResult 对象
    """
    sr = StabilityResult(factor_name=factor_name, groupby=groupby, overall_n=len(data))

    # 构建分组标签
    try:
        group_labels = _build_groupby_series(data, groupby)
    except ValueError as e:
        print(f"  [STABILITY SKIP] {factor_name}: {e}", file=sys.stderr)
        return sr

    # 提取因子值和目标
    try:
        fv = FactorExtractor.extract(factor_name, data)
    except ValueError:
        print(f"  [STABILITY SKIP] {factor_name}: cannot extract factor", file=sys.stderr)
        return sr

    if target not in data.columns:
        print(f"  [STABILITY SKIP] {factor_name}: target '{target}' not found", file=sys.stderr)
        return sr

    tv = data[target]

    # 按时间顺序遍历各 group
    groups_sorted = sorted(group_labels.dropna().unique())

    group_results = []
    ic_timeline = []

    for g in groups_sorted:
        mask = group_labels == g
        g_fv = fv[mask].dropna()
        g_tv = tv[mask].dropna()
        # 对齐
        valid = g_fv.index.intersection(g_tv.index)
        g_fv = g_fv.loc[valid]
        g_tv = g_tv.loc[valid]

        if len(g_fv) < min_samples_per_group:
            continue

        # IC
        ic_val = _compute_ic(g_fv, g_tv)

        # 分桶均收益（轻量版：只算 3 等分 bucket）
        try:
            fv_num = pd.to_numeric(g_fv, errors='coerce').dropna()
            tv_aligned = g_tv.loc[fv_num.index]
            if len(fv_num) >= 30:
                cat, _ = pd.qcut(fv_num, q=3, retbins=True, duplicates='drop', labels=False)
                top_mask = cat == cat.max()
                top_ret = tv_aligned[top_mask].mean() if top_mask.any() else g_tv.mean()
                big_wins = (tv_aligned[top_mask] > DEFAULT_BIG_WIN).mean() * 100 if top_mask.any() else 0
            else:
                top_ret = g_tv.mean()
                big_wins = (g_tv > DEFAULT_BIG_WIN).mean() * 100
        except Exception:
            top_ret = g_tv.mean()
            big_wins = (g_tv > DEFAULT_BIG_WIN).mean() * 100

        group_results.append({
            'period': g,
            'n': len(g_fv),
            'avg_ret': round(g_tv.mean(), 2),
            'win_rate': round((g_tv > 0).mean() * 100, 1),
            'ic': round(ic_val, 3) if ic_val is not None else None,
            'top_bucket_ret': round(top_ret, 2),
            'big_win_prob': round(big_wins, 1),
        })

        if ic_val is not None:
            ic_timeline.append({'period': g, 'ic': round(ic_val, 3)})

    sr.groups = group_results
    sr.n_groups = len(group_results)

    # 计算稳定性指标
    if len(group_results) >= 2:
        avg_ret_values = [g['avg_ret'] for g in group_results]
        ret_mean = np.mean(avg_ret_values)
        ret_std = np.std(avg_ret_values)
        sr.stability_ratio = ret_std / abs(ret_mean) if abs(ret_mean) > 0.001 else float('inf')

        ic_values = [g['ic'] for g in group_results if g['ic'] is not None]
        if len(ic_values) >= 2:
            sr.ic_mean = np.mean(ic_values)
            sr.ic_std = np.std(ic_values)
            sr.ic_stability = sr.ic_mean / sr.ic_std if sr.ic_std > 0.001 else float('inf')

    sr.ic_timeline = ic_timeline
    return sr


def analyze_all_stabilities(
    data: pd.DataFrame,
    factors: List[str],
    target: str = 'net_ret',
    groupby: str = 'year',
    **kwargs,
) -> List[StabilityResult]:
    """批量稳定性分析。"""
    results = []
    for name in factors:
        try:
            sr = analyze_stability(name, data, target=target, groupby=groupby, **kwargs)
            if sr.n_groups > 0:
                results.append(sr)
        except Exception as e:
            print(f"  [STABILITY ERROR] {name}: {e}", file=sys.stderr)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 因子相关性分析 — Pearson + Spearman 双矩阵
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_correlations(
    data: pd.DataFrame,
    factors: List[str] = None,
    spearman_threshold: float = 0.70,
    pearson_threshold: float = 0.80,
) -> CorrelationMatrix:
    """计算因子间的 Pearson 和 Spearman 相关性矩阵。

    自动发现高相关因子对并生成警告。

    Args:
        data: 交易记录 DataFrame
        factors: 因子名列表。若为 None，自动发现。
        spearman_threshold: Spearman 秩相关阈值，>= 此值视为冗余
        pearson_threshold: Pearson 线性相关阈值，>= 此值视为可能冗余

    Returns:
        CorrelationMatrix 对象
    """
    # 自动发现因子
    if factors is None:
        factors = []
        for col in data.columns:
            if col in META_COLUMNS or col.startswith('_'):
                continue
            if data[col].dtype == object:
                sample = data[col].dropna().iloc[0] if len(data[col].dropna()) > 0 else None
                if isinstance(sample, dict):
                    for key in _discover_dict_keys(data[col]):
                        factors.append(f'{col}.{key}')
                    continue
            factors.append(col)

    if len(factors) < 2:
        return CorrelationMatrix(factors=factors, n_samples=len(data))

    # 提取所有因子值到一个矩阵
    factor_matrix = {}
    for name in factors:
        try:
            vals = FactorExtractor.extract(name, data)
            vals = pd.to_numeric(vals, errors='coerce')
            if vals.notna().sum() >= 20:
                factor_matrix[name] = vals
        except Exception:
            pass

    if len(factor_matrix) < 2:
        return CorrelationMatrix(factors=list(factor_matrix.keys()), n_samples=len(data))

    # 构建 DataFrame
    fm_df = pd.DataFrame(factor_matrix)
    valid_mask = fm_df.notna().all(axis=1)
    fm_clean = fm_df[valid_mask]

    cm = CorrelationMatrix(
        factors=list(fm_clean.columns),
        n_samples=len(fm_clean),
    )

    if len(fm_clean.columns) < 2 or len(fm_clean) < 10:
        return cm

    # 计算 Pearson 和 Spearman
    cm.pearson = fm_clean.corr(method='pearson')
    cm.spearman = fm_clean.corr(method='spearman')

    # 发现高相关对
    factor_names = list(fm_clean.columns)
    for i in range(len(factor_names)):
        for j in range(i + 1, len(factor_names)):
            f1, f2 = factor_names[i], factor_names[j]
            pr = cm.pearson.loc[f1, f2]
            sr = cm.spearman.loc[f1, f2]

            # 判断是否冗余
            warning = None
            if abs(sr) >= spearman_threshold and abs(pr) >= pearson_threshold:
                warning = 'REDUNDANT: both Pearson and Spearman high — likely same signal'
            elif abs(sr) >= spearman_threshold:
                warning = 'MONOTONIC: Spearman high but Pearson moderate — nonlinear relationship, still overlapping'
            elif abs(pr) >= pearson_threshold:
                warning = 'LINEAR: Pearson high but Spearman moderate — check for outliers driving correlation'

            if warning or abs(sr) >= 0.50 or abs(pr) >= 0.60:
                cm.high_corr_pairs.append({
                    'f1': f1, 'f2': f2,
                    'pearson': round(pr, 3),
                    'spearman': round(sr, 3),
                    'warning': warning,
                })

    # 按 Spearman 绝对值降序排列
    cm.high_corr_pairs.sort(key=lambda x: abs(x['spearman']), reverse=True)

    return cm


# ═══════════════════════════════════════════════════════════════════════════════
# Alpha 综合评分
# ═══════════════════════════════════════════════════════════════════════════════

def _auto_discover_factors(data: pd.DataFrame) -> List[str]:
    """自动发现可分析的因子列表。"""
    factors = []
    for col in data.columns:
        if col in META_COLUMNS or col.startswith('_'):
            continue
        if data[col].dtype == object:
            sample = data[col].dropna().iloc[0] if len(data[col].dropna()) > 0 else None
            if isinstance(sample, dict):
                for key in _discover_dict_keys(data[col]):
                    factors.append(f'{col}.{key}')
                continue
        factors.append(col)
    return factors


def score_all_factors(
    data: pd.DataFrame,
    factors: List[str] = None,
    target: str = 'net_ret',
    groupby: str = 'year',
    **kwargs,
) -> pd.DataFrame:
    """对所有因子运行完整评估并输出 Alpha 评分排名。

    依次执行：
      1. analyze_all_factors()  → 分桶 + IC
      2. analyze_all_stabilities()  → 跨周期稳定性
      3. analyze_correlations()  → 相关性矩阵
      4. AlphaScorecard.compute()  → 综合评分

    Returns:
        按 total_score 降序的评分 DataFrame
    """
    if factors is None:
        factors = _auto_discover_factors(data)

    # 步骤 1: 基础分析
    print('Step 1/4: Analyzing all factors (bucketing + IC)...')
    analyses = analyze_all_factors(data, factors=factors, target=target, **kwargs)

    factor_names = [a.factor_name for a in analyses]

    # 步骤 2: 稳定性
    print(f'Step 2/4: Analyzing stability (groupby={groupby})...')
    stability_results = analyze_all_stabilities(data, factor_names, target=target, groupby=groupby)
    stab_lookup = {s.factor_name: s for s in stability_results}

    # 步骤 3: 相关性
    print('Step 3/4: Computing correlations...')
    corr_matrix = analyze_correlations(data, factors=factor_names)

    # 步骤 4: 综合评分
    print('Step 4/4: Computing Alpha scorecards...')
    scorecards = []
    for a in analyses:
        stab = stab_lookup.get(a.factor_name)
        sc = AlphaScorecard.compute(
            factor_name=a.factor_name,
            analysis=a,
            stability=stab,
            corr_matrix=corr_matrix,
        )
        scorecards.append(sc)

    # 排序
    scorecards.sort(key=lambda x: x.total_score, reverse=True)

    return pd.DataFrame([{
        'Factor': sc.factor_name,
        'Total': sc.total_score,
        'IC': sc.ic_score,
        'Stability': sc.stability_score,
        'Spread': sc.spread_score,
        'Sample': sc.sample_score,
        'CorrPenalty': -sc.correlation_penalty,
        'IC_val': sc.details.get('ic'),
        'IC_stab': sc.details.get('ic_stability'),
        'Spread_val': sc.details.get('spread'),
        'N': sc.details.get('n_samples'),
        'AvgCorr': sc.details.get('avg_spearman'),
    } for sc in scorecards])


# ═══════════════════════════════════════════════════════════════════════════════
# 终端输出
# ═══════════════════════════════════════════════════════════════════════════════

def print_report(analysis: FactorAnalysis, title: str = None) -> None:
    """打印单因子分析报告（终端表格）。"""
    if analysis.n_samples == 0:
        print(f"\n  Factor '{analysis.factor_name}': insufficient data (n=0)")
        return

    width = 74
    label = title or f'Factor: {analysis.factor_name}'
    ic_str = f'IC: {analysis.ic_correlation:+.3f}' if analysis.ic_correlation is not None else 'IC: N/A (categorical)'

    print()
    print('═' * width)
    print(f'  {label} ({analysis.factor_type}, N={analysis.n_samples})')
    print(f'  Target: {analysis.target}   {ic_str}')
    print('═' * width)

    if not analysis.buckets:
        print(f'  Insufficient data for bucketing '
              f'(Overall AvgRet={analysis.overall_mean_ret:+.2f}%, '
              f'WinRate={analysis.overall_win_rate:.1f}%)')
        print('═' * width)
        return

    # 表头
    hdr = f'  {"Bucket":<20s} {"Count":>5s} {"Win%":>6s} {"AvgRet":>7s} {"Median":>7s} {">10%":>6s} {"<-5%":>6s}'
    sep  = f'  {"─"*20} {"─"*5} {"─"*6} {"─"*7} {"─"*7} {"─"*6} {"─"*6}'
    print(hdr)
    print(sep)

    # 数据行
    best_start = analysis.best_interval_stats.get('start_bucket', -1)
    best_end = analysis.best_interval_stats.get('end_bucket', -1)
    for i, b in enumerate(analysis.buckets):
        marker = ' ★ BEST' if best_start <= i <= best_end else ''
        print(f'  {str(b.bucket_label):<20s} {b.count:>5d} {b.win_rate:>5.1f}% '
              f'{b.avg_ret:>+6.2f}% {b.median_ret:>+6.2f}% '
              f'{b.big_win_prob:>5.1f}% {b.big_loss_prob:>5.1f}%{marker}')

    # 最佳区间总结
    if analysis.best_interval:
        bi = analysis.best_interval
        bs = analysis.best_interval_stats
        print()
        print(f'  Best interval: [{bi[0]:.2f}, {bi[1]:.2f})  '
              f'(N={bs["count"]}, AvgRet={bs["avg_ret"]:+.2f}%, '
              f'WinRate={bs["win_rate"]:.1f}%, BigWin={bs["big_win_prob"]:.1f}%)')

    # 整体统计
    print(f'  Overall: AvgRet={analysis.overall_mean_ret:+.2f}%, '
          f'WinRate={analysis.overall_win_rate:.1f}%')
    print('═' * width)


def print_split_report(is_analysis: FactorAnalysis, oos_analysis: FactorAnalysis) -> None:
    """打印 IS/OOS 对比报告。"""
    width = 74
    print()
    print('═' * width)
    print('  IS/OOS Split Analysis')
    print('═' * width)

    # 并排对比摘要
    print(f'  {"":<20s} {"In-Sample":>20s} {"Out-of-Sample":>20s}')
    print(f'  {"─"*20} {"─"*20} {"─"*20}')
    rows = [
        ('Samples', f'{is_analysis.n_samples}', f'{oos_analysis.n_samples}'),
        ('Buckets', f'{is_analysis.n_buckets}', f'{oos_analysis.n_buckets}'),
        ('AvgRet', f'{is_analysis.overall_mean_ret:+.2f}%', f'{oos_analysis.overall_mean_ret:+.2f}%'),
        ('WinRate', f'{is_analysis.overall_win_rate:.1f}%', f'{oos_analysis.overall_win_rate:.1f}%'),
        ('IC', f'{is_analysis.ic_correlation:+.3f}' if is_analysis.ic_correlation else 'N/A',
         f'{oos_analysis.ic_correlation:+.3f}' if oos_analysis.ic_correlation else 'N/A'),
    ]
    if is_analysis.best_interval:
        rows.append(('BestInt AvgRet', f'{is_analysis.best_interval_stats["avg_ret"]:+.2f}%',
                      f'{oos_analysis.best_interval_stats.get("avg_ret", 0):+.2f}%'))

    for label, is_val, oos_val in rows:
        print(f'  {label:<20s} {is_val:>20s} {oos_val:>20s}')

    print('═' * width)

    # 打印各自的详细表格
    print_report(is_analysis, title='IN-SAMPLE')
    print_report(oos_analysis, title='OUT-OF-SAMPLE')


def print_comparison_table(analyses: List[FactorAnalysis]) -> None:
    """打印跨因子对比表。"""
    df = compare_factors(analyses)
    if df.empty:
        print("No factors to compare.")
        return

    print()
    print('═' * 90)
    print('  Factor Comparison (sorted by BigWin Diff)')
    print('═' * 90)

    hdr = f'  {"Factor":<25s} {"Type":>6s} {"N":>5s} {"Spread":>7s} {"BestInt":>8s} {"BigWinDiff":>10s} {"IC":>7s}'
    sep  = f'  {"─"*25} {"─"*6} {"─"*5} {"─"*7} {"─"*8} {"─"*10} {"─"*7}'
    print(hdr)
    print(sep)

    for _, row in df.iterrows():
        ic_str = f'{row["IC"]:+.3f}' if row['IC'] is not None and not pd.isna(row['IC']) else 'N/A'
        print(f'  {row["Factor"]:<25s} {row["Type"]:>6s} {int(row["N"]):>5d} '
              f'{row["Spread"]:>+6.2f}% {row["BestInt_AvgRet"]:>+7.2f}% '
              f'{row["BigWin_Diff"]:>+9.1f}% {ic_str:>7s}')

    print('═' * 90)
    print(f'  Spread = Top_AvgRet - Bot_AvgRet (monotonicity)')
    print(f'  BigWin Diff = Top_BigWin% - Bot_BigWin% (information gain)')
    print('═' * 90)


def print_stability(sr: StabilityResult) -> None:
    """打印因子稳定性报告。"""
    if sr.n_groups == 0:
        print(f"\n  Stability for '{sr.factor_name}': insufficient data")
        return

    width = 80
    print()
    print('═' * width)
    label = f'Stability: {sr.factor_name} (groupby={sr.groupby})'
    ic_str = f'IC stability: {sr.ic_stability:.2f}  ratio: {sr.stability_ratio:.3f}'
    print(f'  {label}')
    print(f'  {ic_str}')
    print('═' * width)

    hdr = f'  {"Period":<12s} {"N":>5s} {"AvgRet":>8s} {"Win%":>6s} {"TopRet":>8s} {"BigWin%":>8s} {"IC":>7s}'
    sep  = f'  {"─"*12} {"─"*5} {"─"*8} {"─"*6} {"─"*8} {"─"*8} {"─"*7}'
    print(hdr)
    print(sep)

    for g in sr.groups:
        ic_str = f'{g["ic"]:+.3f}' if g['ic'] is not None else 'N/A'
        print(f'  {g["period"]:<12s} {g["n"]:>5d} {g["avg_ret"]:>+7.2f}% '
              f'{g["win_rate"]:>5.1f}% {g["top_bucket_ret"]:>+7.2f}% '
              f'{g["big_win_prob"]:>7.1f}% {ic_str:>7s}')

    print()
    print(f'  Stability ratio (std/mean): {sr.stability_ratio:.3f}  (lower = more stable)')
    print(f'  IC stability (mean/std):   {sr.ic_stability:.2f}  (higher = more stable)')
    print(f'  Interpretation: ', end='')
    if sr.ic_stability >= 2.0 and sr.stability_ratio < 0.5:
        print('HIGHLY STABLE — suitable for production')
    elif sr.ic_stability >= 1.0 and sr.stability_ratio < 1.0:
        print('STABLE — suitable for model inclusion')
    elif sr.ic_stability >= 0.5:
        print('MODERATE — monitor for decay')
    else:
        print('UNSTABLE — not recommended for production')
    print('═' * width)


def print_correlation_report(cm: CorrelationMatrix,
                              spearman_threshold: float = 0.70,
                              max_pairs: int = 20) -> None:
    """打印因子相关性矩阵报告。"""
    if not cm.factors or len(cm.factors) < 2:
        print("\n  Correlation: insufficient factors for analysis")
        return

    width = 90
    print()
    print('═' * width)
    print(f'  Factor Correlation Report ({len(cm.factors)} factors, N={cm.n_samples})')
    print('═' * width)

    # 高相关对
    if cm.high_corr_pairs:
        # 只显示 Spearman >= 0.5 的对
        filtered = [p for p in cm.high_corr_pairs if abs(p['spearman']) >= 0.50]
        if filtered:
            hdr = f'  {"Factor A":<25s} {"Factor B":<25s} {"Pearson":>8s} {"Spearman":>9s} {"Note":<30s}'
            sep  = f'  {"─"*25} {"─"*25} {"─"*8} {"─"*9} {"─"*30}'
            print(hdr)
            print(sep)
            for p in filtered[:max_pairs]:
                note = '⚠ REDUNDANT' if abs(p['spearman']) >= spearman_threshold else ''
                print(f'  {p["f1"]:<25s} {p["f2"]:<25s} {p["pearson"]:>+7.3f} {p["spearman"]:>+8.3f} '
                      f'{note:<30s}')
        else:
            print('  No significant correlations found.')

        # 统计
        n_redundant = len([p for p in cm.high_corr_pairs if abs(p.get('spearman', 0)) >= spearman_threshold])
        print(f'\n  Total factor pairs checked: {len(cm.factors) * (len(cm.factors) - 1) // 2}')
        print(f'  Pairs with Spearman ≥ {spearman_threshold}: {n_redundant}')
        if n_redundant > 0:
            redundant = [p for p in cm.high_corr_pairs if abs(p.get('spearman', 0)) >= spearman_threshold]
            print(f'  Redundant pairs:')
            for p in redundant[:10]:
                print(f'    {p["f1"]} ↔ {p["f2"]}  (Spearman={p["spearman"]:+.3f})')
    else:
        print('  All factor pairs have low correlation (< 0.50 Spearman).')

    print('═' * width)


def print_alpha_scorecard(scorecards: pd.DataFrame) -> None:
    """打印 Alpha 评分排名表。"""
    if scorecards.empty:
        print("\n  No scorecards to display.")
        return

    df = scorecards.copy()
    # 格式化
    for col in ['IC_val', 'IC_stab', 'Spread_val', 'AvgCorr']:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: f'{x:+.3f}' if pd.notna(x) and isinstance(x, (int, float)) else str(x))

    width = 105
    print()
    print('═' * width)
    print('  Alpha Scorecard — Factor Ranking')
    print('═' * width)

    hdr = (f'  {"Factor":<22s} {"Total":>5s} {"IC":>4s} {"Stab":>4s} '
           f'{"Sprd":>4s} {"Smp":>4s} {"Corr":>4s} '
           f'{"IC_val":>7s} {"IC_stab":>7s} {"Spread":>7s} {"N":>5s}')
    sep  = (f'  {"─"*22} {"─"*5} {"─"*4} {"─"*4} '
            f'{"─"*4} {"─"*4} {"─"*4} '
            f'{"─"*7} {"─"*7} {"─"*7} {"─"*5}')
    print(hdr)
    print(sep)

    for _, row in df.iterrows():
        print(f'  {row["Factor"]:<22s} '
              f'{int(row["Total"]):>5d} '
              f'{int(row["IC"]):>4d} '
              f'{int(row["Stability"]):>4d} '
              f'{int(row["Spread"]):>4d} '
              f'{int(row["Sample"]):>4d} '
              f'{int(row["CorrPenalty"]):>4d} '
              f'{str(row.get("IC_val", "-")):>7s} '
              f'{str(row.get("IC_stab", "-")):>7s} '
              f'{str(row.get("Spread_val", "-")):>7s} '
              f'{str(row.get("N", "-")):>5s}')

    print('═' * width)
    print(f'  Scoring: IC(0-30) + Stability(0-25) + Spread(0-20) + Sample(0-15) - CorrPenalty(0-10) = Total(0-100)')
    print(f'  ≥80: Production-ready | ≥65: Model candidate | ≥50: Monitor | <50: Research only')
    print('═' * width)


# ═══════════════════════════════════════════════════════════════════════════════
# 图表输出
# ═══════════════════════════════════════════════════════════════════════════════

def _setup_matplotlib_chinese():
    """设置 matplotlib 中文字体。"""
    if not HAS_MPL:
        return False

    # 尝试常用中文字体
    chinese_fonts = [
        'Microsoft YaHei', 'SimHei', 'PingFang SC',
        'WenQuanYi Micro Hei', 'WenQuanYi Zen Hei',
        'Noto Sans CJK SC', 'Noto Sans SC', 'Source Han Sans SC',
        'STHeiti', 'Heiti SC',
    ]
    import matplotlib.font_manager as fm
    available = {f.name for f in fm.fontManager.ttflist}

    for font in chinese_fonts:
        if font in available:
            plt.rcParams['font.sans-serif'] = [font, 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False
            return True

    # 无中文字体 — 使用英文标签
    plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    return False


def plot_factor_analysis(
    analysis: FactorAnalysis,
    output_dir: str = None,
    show: bool = False,
    prefix: str = '',
) -> Optional[str]:
    """生成因子分析图表（2-panel）。

    Panel 1: 柱状图 — x=桶中心, y=均收益（正绿负红）
    Panel 2: 双折线 — x=桶中心, y=大赚概率(蓝) vs 大亏概率(红)

    Returns:
        保存的 PNG 路径，或 None（若 matplotlib 不可用）
    """
    if not HAS_MPL:
        warnings.warn("matplotlib not available, skipping chart")
        return None
    if not analysis.buckets:
        return None

    has_chinese = _setup_matplotlib_chinese()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

    buckets = analysis.buckets
    centers = [b.bucket_center for b in buckets]
    avg_rets = [b.avg_ret for b in buckets]
    big_wins = [b.big_win_prob for b in buckets]
    big_losses = [b.big_loss_prob for b in buckets]
    counts = [b.count for b in buckets]
    labels = [b.bucket_label for b in buckets]

    # 缩短标签
    short_labels = []
    for lbl in labels:
        if len(lbl) > 12:
            # "[1.23, 4.56)" → "1.23~4.56"
            lbl = lbl.replace('[', '').replace(')', '').replace(', ', '~')
            if len(lbl) > 12:
                lbl = lbl[:11] + '…'
        short_labels.append(lbl)

    # ── Panel 1: 均收益柱状图 ──
    colors = ['#2ecc71' if r >= 0 else '#e74c3c' for r in avg_rets]
    bars = ax1.bar(range(len(buckets)), avg_rets, color=colors, alpha=0.85, edgecolor='white', linewidth=0.5)

    # 标注样本数
    for i, (bar, cnt) in enumerate(zip(bars, counts)):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                 f'n={cnt}',
                 ha='center', va='bottom' if bar.get_height() >= 0 else 'top',
                 fontsize=7, color='#555')

    # 高亮最佳区间
    bi_start = analysis.best_interval_stats.get('start_bucket', -1)
    bi_end = analysis.best_interval_stats.get('end_bucket', -1)
    if bi_start >= 0 and bi_end >= bi_start:
        ax1.axvspan(bi_start - 0.5, bi_end + 0.5, alpha=0.12, color='#f39c12', label='Best Interval')

    ax1.axhline(y=0, color='#888', linestyle='--', linewidth=0.8)
    ax1.set_xticks(range(len(buckets)))
    ax1.set_xticklabels(short_labels, rotation=45, ha='right', fontsize=8)
    ax1.set_ylabel('Avg Return (%)' if not has_chinese else '平均收益 (%)')
    ax1.set_title(f'{analysis.factor_name} — Avg Return by Bucket'
                  if not has_chinese else f'{analysis.factor_name} — 分桶均收益')
    ax1.grid(axis='y', alpha=0.3, linewidth=0.5)
    if bi_start >= 0:
        ax1.legend(fontsize=8, loc='best')

    # ── Panel 2: 大赚/大亏概率曲线 ──
    ax2.plot(range(len(buckets)), big_wins, 'o-', color='#3498db', linewidth=2,
             markersize=6, label='Big Win (>10%)' if not has_chinese else '大赚 (>10%)')
    ax2.plot(range(len(buckets)), big_losses, 's-', color='#e74c3c', linewidth=2,
             markersize=6, label='Big Loss (<-5%)' if not has_chinese else '大亏 (<-5%)')

    # 填充差值区域
    ax2.fill_between(range(len(buckets)), big_losses, big_wins, alpha=0.08, color='#3498db')

    ax2.set_xticks(range(len(buckets)))
    ax2.set_xticklabels(short_labels, rotation=45, ha='right', fontsize=8)
    ax2.set_ylabel('Probability (%)' if not has_chinese else '概率 (%)')
    ax2.set_title(f'{analysis.factor_name} — Big Win / Big Loss Probability'
                  if not has_chinese else f'{analysis.factor_name} — 大赚/大亏概率')
    ax2.legend(fontsize=9, loc='best')
    ax2.grid(alpha=0.3, linewidth=0.5)

    # 高亮最佳区间
    if bi_start >= 0 and bi_end >= bi_start:
        ax2.axvspan(bi_start - 0.5, bi_end + 0.5, alpha=0.12, color='#f39c12')

    plt.tight_layout()

    # 保存
    out_dir = Path(output_dir) if output_dir else Path.cwd() / 'data'
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = prefix + analysis.factor_name.replace('.', '_').replace(' ', '_').replace('[', '').replace(']', '')
    fpath = out_dir / f'factor_{safe_name}_analysis.png'
    fig.savefig(str(fpath), dpi=150, bbox_inches='tight', facecolor='white')
    print(f'  Chart saved: {fpath}')

    if show:
        plt.show()
    else:
        plt.close(fig)

    return str(fpath)


def plot_is_oos_comparison(is_analysis: FactorAnalysis, oos_analysis: FactorAnalysis,
                           output_dir: str = None, show: bool = False) -> Optional[str]:
    """IS/OOS 对比图：叠加大赚概率曲线。"""
    if not HAS_MPL:
        warnings.warn("matplotlib not available, skipping chart")
        return None
    if not is_analysis.buckets or not oos_analysis.buckets:
        return None

    _setup_matplotlib_chinese()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

    # ── Panel 1: 均收益对比 ──
    def _plot_bucket_bars(ax, analysis, color, label, offset=0):
        buckets = analysis.buckets
        width = 0.35
        x = [i + offset for i in range(len(buckets))]
        avg_rets = [b.avg_ret for b in buckets]
        bars = ax.bar(x, avg_rets, width, color=color, alpha=0.7, label=label)
        return bars

    all_labels = list(set(
        [b.bucket_label for b in is_analysis.buckets] +
        [b.bucket_label for b in oos_analysis.buckets]
    ))
    # 使用较短的标签
    short_labels = []
    for lbl in (all_labels[:12] if len(all_labels) > 12 else all_labels):
        if len(lbl) > 10:
            lbl = lbl.replace('[', '').replace(')', '').replace(', ', '~')
        short_labels.append(lbl)

    max_buckets = max(len(is_analysis.buckets), len(oos_analysis.buckets))

    _plot_bucket_bars(ax1, is_analysis, '#3498db', 'In-Sample', offset=-0.2)
    _plot_bucket_bars(ax1, oos_analysis, '#e67e22', 'Out-of-Sample', offset=0.2)

    ax1.axhline(y=0, color='#888', linestyle='--', linewidth=0.8)
    ax1.set_xticks(range(max_buckets))
    ax1.set_xticklabels(short_labels[:max_buckets], rotation=45, ha='right', fontsize=7)
    ax1.set_ylabel('Avg Return (%)')
    ax1.set_title(f'{is_analysis.factor_name.split(" [")[0]} — IS vs OOS Avg Return')
    ax1.legend(fontsize=8)
    ax1.grid(axis='y', alpha=0.3, linewidth=0.5)

    # ── Panel 2: 大赚概率对比 ──
    is_bw = [b.big_win_prob for b in is_analysis.buckets]
    oos_bw = [b.big_win_prob for b in oos_analysis.buckets]

    ax2.plot(range(len(is_bw)), is_bw, 'o-', color='#3498db', linewidth=2, markersize=6, label='IS Big Win')
    ax2.plot(range(len(oos_bw)), oos_bw, 's--', color='#e67e22', linewidth=2, markersize=6, label='OOS Big Win')

    # 填充稳定性区域
    min_len = min(len(is_bw), len(oos_bw))
    ax2.fill_between(range(min_len),
                     [is_bw[i] for i in range(min_len)],
                     [oos_bw[i] for i in range(min_len)],
                     alpha=0.08, color='#888')

    ax2.set_xticks(range(max_buckets))
    ax2.set_xticklabels(short_labels[:max_buckets], rotation=45, ha='right', fontsize=7)
    ax2.set_ylabel('Big Win Probability (%)')
    ax2.set_title(f'{is_analysis.factor_name.split(" [")[0]} — IS vs OOS Big Win Prob')
    ax2.legend(fontsize=9)
    ax2.grid(alpha=0.3, linewidth=0.5)

    plt.tight_layout()

    out_dir = Path(output_dir) if output_dir else Path.cwd() / 'data'
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = is_analysis.factor_name.split(' [')[0].replace('.', '_').replace(' ', '_')
    fpath = out_dir / f'factor_{safe_name}_is_oos.png'
    fig.savefig(str(fpath), dpi=150, bbox_inches='tight', facecolor='white')
    print(f'  Chart saved: {fpath}')

    if show:
        plt.show()
    else:
        plt.close(fig)

    return str(fpath)


def plot_stability_ic_timeline(
    stability: StabilityResult,
    output_dir: str = None,
    show: bool = False,
) -> Optional[str]:
    """绘制 IC 时序图 — 展示因子预测能力的时间稳定性。"""
    if not HAS_MPL:
        return None
    if not stability.ic_timeline:
        return None

    _setup_matplotlib_chinese()

    fig, ax = plt.subplots(1, 1, figsize=(14, 5))

    periods = [p['period'] for p in stability.ic_timeline]
    ic_values = [p['ic'] for p in stability.ic_timeline]

    colors = ['#2ecc71' if v >= 0 else '#e74c3c' for v in ic_values]
    bars = ax.bar(range(len(periods)), ic_values, color=colors, alpha=0.85, edgecolor='white', linewidth=0.5)

    for i, (bar, val) in enumerate(zip(bars, ic_values)):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f'{val:+.3f}',
                ha='center', va='bottom' if val >= 0 else 'top',
                fontsize=8, color='#555')

    mean_ic = np.mean(ic_values)
    ax.axhline(y=mean_ic, color='#f39c12', linestyle='--', linewidth=1.5,
               label=f'Mean IC={mean_ic:+.3f}')
    ax.axhline(y=0, color='#888', linestyle='-', linewidth=0.8)

    std_ic = np.std(ic_values)
    ax.axhspan(mean_ic - std_ic, mean_ic + std_ic, alpha=0.08, color='#f39c12',
               label=f'±1σ ({mean_ic - std_ic:+.3f} ~ {mean_ic + std_ic:+.3f})')

    ax.set_xticks(range(len(periods)))
    ax.set_xticklabels(periods, rotation=45, ha='right', fontsize=9)
    ax.set_ylabel('IC (Pearson r)')
    ax.set_title(f'{stability.factor_name} — IC Timeline (groupby={stability.groupby})')
    ax.legend(fontsize=8, loc='best')
    ax.grid(axis='y', alpha=0.3, linewidth=0.5)

    stab_rating = ('HIGHLY STABLE' if stability.ic_stability >= 2.0 else
                   'STABLE' if stability.ic_stability >= 1.0 else
                   'MODERATE' if stability.ic_stability >= 0.5 else 'UNSTABLE')
    ax.text(0.98, 0.95, f'IC Stability: {stability.ic_stability:.2f}\nRating: {stab_rating}',
            transform=ax.transAxes, ha='right', va='top',
            fontsize=10, bbox=dict(boxstyle='round,pad=0.5', facecolor='white', alpha=0.8))

    plt.tight_layout()

    out_dir = Path(output_dir) if output_dir else Path.cwd() / 'data'
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = stability.factor_name.replace('.', '_').replace(' ', '_')
    fpath = out_dir / f'stability_{safe_name}_{stability.groupby}.png'
    fig.savefig(str(fpath), dpi=150, bbox_inches='tight', facecolor='white')
    print(f'  IC Timeline saved: {fpath}')

    if show:
        plt.show()
    else:
        plt.close(fig)

    return str(fpath)


def plot_correlation_heatmap(
    corr_matrix: CorrelationMatrix,
    output_dir: str = None,
    show: bool = False,
    method: str = 'spearman',
) -> Optional[str]:
    """绘制因子相关性热力图。"""
    if not HAS_MPL:
        return None

    if method == 'spearman':
        mat = corr_matrix.spearman
        title = 'Factor Spearman Rank Correlation'
    else:
        mat = corr_matrix.pearson
        title = 'Factor Pearson Correlation'

    if mat is None or len(mat) < 2:
        return None

    _setup_matplotlib_chinese()

    n = len(mat)
    figsize = max(8, n * 0.6)
    fig, ax = plt.subplots(1, 1, figsize=(figsize, figsize * 0.85))

    labels = [lbl if len(lbl) <= 20 else lbl[:18] + '…' for lbl in mat.columns]

    im = ax.imshow(mat.values, cmap='RdBu_r', vmin=-1, vmax=1, aspect='auto')

    for i in range(n):
        for j in range(n):
            val = mat.iloc[i, j]
            color = 'white' if abs(val) > 0.6 else '#333'
            ax.text(j, i, f'{val:.2f}', ha='center', va='center', fontsize=7, color=color)

    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_title(title, fontsize=12)

    plt.colorbar(im, ax=ax, shrink=0.8, label='Correlation')
    plt.tight_layout()

    out_dir = Path(output_dir) if output_dir else Path.cwd() / 'data'
    out_dir.mkdir(parents=True, exist_ok=True)
    fpath = out_dir / f'factor_correlation_{method}.png'
    fig.savefig(str(fpath), dpi=150, bbox_inches='tight', facecolor='white')
    print(f'  Correlation heatmap saved: {fpath}')

    if show:
        plt.show()
    else:
        plt.close(fig)

    return str(fpath)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_args(argv: List[str] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description='因子研究框架 — 通用因子分桶分析工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 分析单个因子
  python factor_research.py --input trades.csv --factors pullback_pct

  # 批量分析，生成图表
  python factor_research.py --input trades.csv --factors "pullback_pct,vol_shrink_ratio,trigger_type" --charts

  # 样本内/外拆分
  python factor_research.py --input trades.csv --factors pullback_pct --split-date 2025-06-30

  # 分析所有可用因子
  python factor_research.py --input trades.csv --factors all --n-buckets 8
        """
    )
    p.add_argument('--input', '-i', required=True,
                   help='交易记录文件路径 (CSV / Excel / Parquet / Pickle)')
    p.add_argument('--factors', '-f', default='all',
                   help='因子名，逗号分隔。支持: 直接列名, v2_factors.xxx, features.xxx, trigger_type, "all"=自动发现')
    p.add_argument('--target', '-t', default='net_ret',
                   help='目标收益列名 (default: net_ret)')
    p.add_argument('--n-buckets', '-n', type=int, default=DEFAULT_N_BUCKETS,
                   help=f'连续因子分桶数 (default: {DEFAULT_N_BUCKETS})')
    p.add_argument('--method', '-m', default='quantile',
                   choices=['quantile', 'equal_width', 'auto'],
                   help='分桶策略 (default: quantile)')
    p.add_argument('--split-date', '-s', default=None,
                   help='IS/OOS 拆分日期，如 2025-06-30')
    p.add_argument('--split-ratio', '-r', type=float, default=None,
                   help='IS/OOS 拆分比例，如 0.7 (前70%%为IS)')
    p.add_argument('--charts', '-c', action='store_true',
                   help='生成 matplotlib 图表')
    p.add_argument('--output-dir', '-o', default=None,
                   help='图表输出目录 (default: ./data/)')
    p.add_argument('--big-win', type=float, default=DEFAULT_BIG_WIN,
                   help=f'大赚阈值%% (default: {DEFAULT_BIG_WIN})')
    p.add_argument('--big-loss', type=float, default=DEFAULT_BIG_LOSS,
                   help=f'大亏阈值%% (default: {DEFAULT_BIG_LOSS})')
    p.add_argument('--sheet', default=None,
                   help='Excel sheet name (for --input .xlsx)')
    p.add_argument('--csv', default=None,
                   help='Also export analysis results to CSV file')
    p.add_argument('--mode', default='analyze',
                   choices=['analyze', 'stability', 'correlation', 'scorecard'],
                   help='分析模式 (default: analyze)')
    p.add_argument('--groupby', default='year',
                   choices=['year', 'quarter', 'month', 'year_month', 'market_regime'],
                   help='稳定性分组维度 (default: year)')
    p.add_argument('--spearman-threshold', type=float, default=0.70,
                   help='Spearman 冗余阈值 (default: 0.70)')
    return p.parse_args(argv)


def _load_data(filepath: str, sheet: str = None) -> pd.DataFrame:
    """加载交易记录数据，支持 CSV/Excel/Parquet/Pickle。"""
    fp = Path(filepath)
    if not fp.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    suffix = fp.suffix.lower()
    if suffix == '.csv':
        return pd.read_csv(fp)
    elif suffix in ('.xlsx', '.xls'):
        return pd.read_excel(fp, sheet_name=sheet or 0)
    elif suffix in ('.parquet', '.pq'):
        return pd.read_parquet(fp)
    elif suffix in ('.pkl', '.pickle'):
        return pd.read_pickle(fp)
    else:
        # 尝试 CSV 兜底
        try:
            return pd.read_csv(fp)
        except Exception:
            raise ValueError(f"Unsupported file format: {suffix}. Supported: .csv, .xlsx, .parquet, .pkl")


def main(argv: List[str] = None):
    args = _parse_args(argv)

    # ── 加载数据 ──
    print(f'Loading: {args.input}')
    data = _load_data(args.input, sheet=args.sheet)
    print(f'  Loaded {len(data)} trades, {len(data.columns)} columns')

    # ── 解析因子列表 ──
    if args.factors == 'all':
        factors = None  # analyze_all_factors 会自动发现
    else:
        factors = [f.strip() for f in args.factors.split(',') if f.strip()]

    # ── 输出目录 ──
    output_dir = args.output_dir or str(Path(__file__).resolve().parent.parent / 'data')

    # ── 执行分析（按 mode 分发） ──
    kwargs = dict(
        target=args.target,
        n_buckets=args.n_buckets,
        method=args.method,
        big_win_threshold=args.big_win,
        big_loss_threshold=args.big_loss,
    )

    # == mode: stability ==
    if args.mode == 'stability':
        if factors is None:
            factors = _auto_discover_factors(data)
        print(f'\nStability Analysis (groupby={args.groupby})')
        for fname in factors:
            sr = analyze_stability(fname, data, target=args.target,
                                    groupby=args.groupby, **kwargs)
            if sr.n_groups > 0:
                print_stability(sr)
                if args.charts:
                    plot_stability_ic_timeline(sr, output_dir=output_dir)
        return

    # == mode: correlation ==
    if args.mode == 'correlation':
        print(f'\nCorrelation Analysis (Spearman threshold={args.spearman_threshold})')
        cm = analyze_correlations(data, factors=factors,
                                  spearman_threshold=args.spearman_threshold)
        print_correlation_report(cm, spearman_threshold=args.spearman_threshold)
        if args.charts:
            plot_correlation_heatmap(cm, output_dir=output_dir, method='spearman')
            if cm.pearson is not None and len(cm.pearson) > 1:
                plot_correlation_heatmap(cm, output_dir=output_dir, method='pearson')
        return

    # == mode: scorecard ==
    if args.mode == 'scorecard':
        print(f'\nAlpha Scorecard (groupby={args.groupby})')
        sc_df = score_all_factors(data, factors=factors, target=args.target,
                                   groupby=args.groupby, **kwargs)
        print_alpha_scorecard(sc_df)
        if args.charts:
            # Run correlation heatmap
            cm = analyze_correlations(data, factors=factors)
            plot_correlation_heatmap(cm, output_dir=output_dir, method='spearman')
        if args.csv:
            sc_df.to_csv(args.csv, index=False, encoding='utf-8-sig')
            print(f'\nScorecards exported → {args.csv}')
        return

    # == default mode: analyze (with IS/OOS support) ==
    if args.split_date or args.split_ratio:
        print(f'\nIS/OOS Split: {"date=" + args.split_date if args.split_date else f"ratio={args.split_ratio}"}')
        if factors is None:
            factors = [f.strip() for f in (
                'pullback_pct,vol_shrink_ratio,pullback_days,'
                'v2_factors.距年高,v2_factors.60日涨,v2_factors.信号强度,'
                'v2_factors.回踩幅度,v2_factors.缩量,v2_factors.止跌K线,v2_factors.回踩天数,'
                'v2_factors.位置共振,'
                'features.dist_250d,features.chg_60d,features.vol_ratio,features.est_cap'
            ).split(',')]
        for fname in factors:
            try:
                is_a, oos_a = analyze_factor_split(
                    fname, data,
                    split_date=args.split_date,
                    split_ratio=args.split_ratio,
                    **kwargs,
                )
                print_split_report(is_a, oos_a)
                if args.charts:
                    plot_is_oos_comparison(is_a, oos_a, output_dir=output_dir)
            except ValueError as e:
                print(f"  [SKIP] {fname}: {e}", file=sys.stderr)
    else:
        analyses = analyze_all_factors(data, factors=factors, **kwargs)
        print_comparison_table(analyses)
        for a in analyses:
            print_report(a)
        if args.charts:
            for a in analyses:
                if a.buckets:
                    plot_factor_analysis(a, output_dir=output_dir)
        if args.csv:
            all_buckets = []
            for a in analyses:
                df = a.to_dataframe()
                df.insert(0, 'factor', a.factor_name)
                all_buckets.append(df)
            if all_buckets:
                combined = pd.concat(all_buckets, ignore_index=True)
                combined.to_csv(args.csv, index=False, encoding='utf-8-sig')
                print(f'\nExported {len(combined)} bucket rows → {args.csv}')


if __name__ == '__main__':
    main()
