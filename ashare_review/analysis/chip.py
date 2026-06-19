"""筹码分布分析 — 基于历史量价数据模拟筹码迁移

核心方法：每日换手将部分筹码转移到新价格区间，
其余筹码保持在原有成本区间。经过足够长的换手周期后，
筹码分布图反映当前持仓成本结构。

四大形态识别（龙哥筹码峰战法）：
- 形态一：低位单峰密集 — 主升浪起爆前兆（核心买点）
- 形态二：底部锁仓+上涨多峰 — 坚定持有最强信号
- 形态三：高位单峰密集+筹码松动 — 主力出货终极信号（核心卖点）
- 形态四：多峰套牢+低位未形成新峰 — 坚决观望警戒信号
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple


def _native(val):
    """numpy → python 原生类型"""
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return float(val)
    if isinstance(val, np.bool_):
        return bool(val)
    if isinstance(val, np.ndarray):
        return val.tolist()
    return val


# ------------------------------------------------------------------
# 筹码分布计算
# ------------------------------------------------------------------
def calc_chip_distribution(df: pd.DataFrame, bins: int = 80,
                           decay_days: int = 500) -> Dict:
    """基于历史日线数据模拟筹码成本分布

    算法：模拟每一天的换手，将成交量按价格区间分配。
    旧筹码按剩余未换手比例保留在新分布中。

    Parameters
    ----------
    df : DataFrame with columns [open, high, low, close, volume]
    bins : 价格区间数量
    decay_days : 筹码衰减周期（超过此天数的筹码视为已充分换手）

    Returns
    -------
    dict with:
        price_bins: 价格区间列表
        chip_dist: 各价格区间筹码占比
        avg_cost: 平均成本
        concentration: 筹码集中度（单峰占比）
        peak_price: 峰值价格区间
        above_ratio: 获利盘占比
    """
    if len(df) < 60:
        return _empty_chip()

    use_days = min(decay_days, len(df))
    recent = df.iloc[-use_days:].copy()
    n = len(recent)

    # 价格范围（覆盖全部历史区间）
    price_high = recent['high'].max()
    price_low = recent['low'].min()
    if price_high <= price_low:
        return _empty_chip()
    price_range = np.linspace(price_low * 0.9, price_high * 1.1, bins + 1)
    bin_width = price_range[1] - price_range[0]

    # 用总股本估算（流通股本用float_market_cap反推，这里简化用累计换手率）
    # 初始筹码均匀分布
    chip_dist = np.zeros(bins)

    for i in range(n):
        row = recent.iloc[i]
        open_p, high_p, low_p, close_p = row['open'], row['high'], row['low'], row['close']
        vol = row['volume']

        # 估算当日换手率（需要流通股本，这里用成交量归一化）
        # 日换手 = vol / float_shares，简化：vol占比来模拟转移

        # 衰减因子：越早的筹码越少（每日按平均换手衰减）
        avg_turnover_rate = 0.02  # 假设日均换手2%
        decay = 1 - avg_turnover_rate

        if i > 0:
            chip_dist = chip_dist * decay

        # 当日成交转移到当前价格区间（按CLOSE分配）
        close_idx = min(bins - 1, max(0, np.searchsorted(price_range, close_p) - 1))
        # 将当日成交量按比例加到筹码分布上
        if vol > 0:
            # 归一化成交量作为筹码增量
            vol_norm = vol / recent['volume'].max()
            # 高斯分布：以收盘价为中心，振幅为sigma
            sigma_pct = 0.02  # 2%的价格波动范围
            sigma = close_p * sigma_pct / bin_width
            for j in range(bins):
                center = (price_range[j] + price_range[j+1]) / 2
                # 高斯权重
                dist = abs(center - close_p) / (close_p + 0.001)
                weight = np.exp(-dist**2 / (2 * sigma_pct**2))
                chip_dist[j] += vol_norm * weight * avg_turnover_rate / bins

    # 归一化
    total = chip_dist.sum()
    if total > 0:
        chip_dist = chip_dist / total
    else:
        return _empty_chip()

    # 计算衍生指标
    price_centers = (price_range[:-1] + price_range[1:]) / 2
    avg_cost = float(np.average(price_centers, weights=chip_dist))

    # 筹码集中度：最大单峰周边的筹码占比
    peak_idx = int(np.argmax(chip_dist))
    peak_zone = slice(max(0, peak_idx - 5), min(bins, peak_idx + 6))
    concentration = float(chip_dist[peak_zone].sum())

    # 获利盘占比（以最新收盘价为基准）
    latest_close = float(recent['close'].iloc[-1])
    below_close = price_centers <= latest_close
    above_ratio = float(chip_dist[below_close].sum() * 100)

    return {
        'price_bins': [round(float(p), 2) for p in price_range.tolist()],
        'chip_dist': [round(float(d), 6) for d in chip_dist.tolist()],
        'avg_cost': round(avg_cost, 2),
        'concentration': round(concentration, 2),
        'peak_price': round(float(price_centers[peak_idx]), 2),
        'above_ratio': round(above_ratio, 1),
        'latest_close': round(latest_close, 2),
    }


def _empty_chip() -> Dict:
    return {
        'price_bins': [], 'chip_dist': [],
        'avg_cost': 0, 'concentration': 0,
        'peak_price': 0, 'above_ratio': 0,
        'latest_close': 0,
    }


# ------------------------------------------------------------------
# 四大形态识别
# ------------------------------------------------------------------
def detect_chip_patterns(df: pd.DataFrame,
                         lookback: int = 500) -> List[Dict]:
    """检测筹码峰四大形态

    返回检测到的所有形态及其信号
    """
    chip = calc_chip_distribution(df, decay_days=lookback)
    if not chip['price_bins']:
        return []

    patterns = []
    concentration = chip['concentration']
    above_ratio = chip['above_ratio']
    avg_cost = chip['avg_cost']
    latest_close = chip['latest_close']
    peak_price = chip['peak_price']
    chip_dist = np.array(chip['chip_dist'])
    price_centers = (np.array(chip['price_bins'][:-1]) + np.array(chip['price_bins'][1:])) / 2

    # 判断所处位置：高位/低位（用最近250日价格范围）
    recent_250 = df.iloc[-min(250, len(df)):]
    price_low_250 = float(recent_250['low'].min())
    price_high_250 = float(recent_250['high'].max())
    price_range_250 = price_high_250 - price_low_250
    if price_range_250 <= 0:
        return []

    position_pct = (latest_close - price_low_250) / price_range_250 * 100

    # 检测峰值数量
    peak_indices = _find_peaks(chip_dist, min_height=0.01)

    # ===== 形态一：低位单峰密集 — 核心买点 =====
    is_low = position_pct <= 35
    has_single_peak = len(peak_indices) == 1 or (len(peak_indices) >= 1 and concentration >= 0.60)
    above_peak_ratio = 0.0
    if peak_indices and is_low:
        main_peak = peak_indices[0] if isinstance(peak_indices[0], (int, np.integer)) else 0
        above_peak = price_centers > price_centers[main_peak] * 1.1
        above_peak_ratio = float(chip_dist[above_peak].sum())
        # 上方套牢盘占比 < 10%
        if has_single_peak and concentration >= 0.65 and above_peak_ratio < 0.10:
            # 检查放量突破
            vol_break = _check_volume_breakout(df)
            patterns.append({
                'pattern': '低位单峰密集',
                'signal': '买入',
                'confidence': '高' if vol_break and concentration >= 0.70 else '中',
                'description': (
                    f'筹码集中度{concentration*100:.0f}%，上方套牢盘{above_peak_ratio*100:.0f}%，'
                    f'底部换手充分，主力吸筹完成。'
                    f'{"已放量突破筹码峰，可介入。" if vol_break else "等待放量突破筹码峰上沿。"}'
                ),
                'action': (
                    '放量突破筹码峰上沿后买入，止损设在峰位上沿下方3%'
                    if not vol_break else
                    '突破已确认，可在回踩筹码峰上沿时低吸，止损不破峰位'
                ),
                'metrics': {
                    'concentration': round(concentration * 100, 0),
                    'above_ratio': round(above_peak_ratio * 100, 0),
                    'volume_breakout': vol_break,
                    'peak_price': chip['peak_price'],
                }
            })

    # ===== 形态二：底部锁仓+上涨多峰 — 持有信号 =====
    # 底部筹码峰是否稳固（用更早期数据）
    if is_low or position_pct <= 50:
        # 简化为检测低位区间筹码是否仍然集中
        low_zone_mask = price_centers <= price_low_250 * 1.2
        bottom_chip_ratio = float(chip_dist[low_zone_mask].sum())
        if bottom_chip_ratio >= 0.30 and latest_close > avg_cost:
            patterns.append({
                'pattern': '底部筹码锁仓',
                'signal': '持有',
                'confidence': '高' if bottom_chip_ratio >= 0.40 else '中',
                'description': (
                    f'底部筹码占比{bottom_chip_ratio*100:.0f}%，主力锁仓态度坚决，'
                    f'当前价格在平均成本上方，健康换手中。'
                ),
                'action': '坚定持有，新密集峰形成时可适当加仓；警惕底部筹码大幅萎缩',
                'metrics': {
                    'bottom_chip_ratio': round(bottom_chip_ratio * 100, 0),
                    'avg_cost': chip['avg_cost'],
                }
            })

    # ===== 形态三：高位单峰密集+筹码松动 — 核心卖点 =====
    is_high = position_pct >= 75
    if is_high:
        # 底部筹码已上移的迹象：低位筹码占比少，高位筹码集中
        low_zone_mask = price_centers <= price_low_250 * 1.3
        bottom_chip_ratio = float(chip_dist[low_zone_mask].sum())
        if bottom_chip_ratio < 0.20 and concentration >= 0.50:
            # 检查放量滞涨或缩量下跌
            vol_stall = _check_volume_stall(df)
            price_drop = _check_recent_drop(df)
            danger = vol_stall or price_drop
            patterns.append({
                'pattern': '高位单峰密集·筹码松动',
                'signal': '卖出',
                'confidence': '高' if danger else '中',
                'description': (
                    f'底部筹码仅剩{bottom_chip_ratio*100:.0f}%，已大幅上移至高位，'
                    f'持仓结构从主力控盘转为散户松动持有。'
                    f'{"同时出现放量滞涨/缩量下跌，主力出货迹象明显。" if danger else "密切关注底部筹码变化。"}'
                ),
                'action': '立即分批止盈离场，切勿恋战；反抽是最后的减仓机会',
                'metrics': {
                    'bottom_chip_ratio': round(bottom_chip_ratio * 100, 0),
                    'concentration': round(concentration * 100, 0),
                    'vol_stall': vol_stall,
                    'price_drop': price_drop,
                }
            })

    # ===== 形态四：多峰套牢+低位未形成新峰 — 观望信号 =====
    if len(peak_indices) >= 2:
        peaks_above = [p for p in peak_indices if price_centers[p] > latest_close]
        if len(peaks_above) >= 1 and concentration < 0.45:
            patterns.append({
                'pattern': '多峰套牢',
                'signal': '观望',
                'confidence': '中',
                'description': (
                    f'上方存在{len(peaks_above)}个筹码密集峰，为强阻力位，'
                    f'下方尚未形成新的单峰密集，不会有新一轮行情产生。'
                ),
                'action': '停止建仓，耐心等待上峰消失、低位形成新单峰密集后再关注',
                'metrics': {
                    'trap_peaks': len(peaks_above),
                    'concentration': round(concentration * 100, 0),
                }
            })

    # 附带获利盘指标
    if above_ratio >= 90:
        patterns.append({
            'pattern': '获利盘>90%',
            'signal': '警示',
            'confidence': '中',
            'description': f'获利盘占比{above_ratio:.0f}%，警惕主力拉高出货。',
            'action': '适当减仓锁利，设宽幅止损',
            'metrics': {'above_ratio': above_ratio},
        })

    return patterns


def _find_peaks(dist: np.ndarray, min_height: float = 0.01) -> List[int]:
    """找筹码分布的峰值索引"""
    peaks = []
    for i in range(1, len(dist) - 1):
        if dist[i] > dist[i-1] and dist[i] > dist[i+1] and dist[i] >= min_height:
            peaks.append(i)
    return peaks


def _check_volume_breakout(df: pd.DataFrame, lookback: int = 5) -> bool:
    """检查最近是否放量突破（近5日均量 > 20日均量的1.5倍）"""
    if len(df) < 25:
        return False
    try:
        recent_vol = df['volume'].iloc[-lookback:].mean()
        ma20_vol = df['volume'].iloc[-25:-lookback].mean()
        return recent_vol > ma20_vol * 1.5
    except Exception:
        return False


def _check_volume_stall(df: pd.DataFrame, days: int = 10) -> bool:
    """检查放量滞涨：最近10天量放大但价格不涨"""
    if len(df) < days + 10:
        return False
    try:
        recent_vol = df['volume'].iloc[-days:].mean()
        prior_vol = df['volume'].iloc[-days-10:-days].mean()
        recent_pct = float(df['close'].iloc[-1] / df['close'].iloc[-days] - 1)
        return prior_vol > 0 and recent_vol / prior_vol > 1.2 and abs(recent_pct) < 0.03
    except Exception:
        return False


def _check_recent_drop(df: pd.DataFrame, days: int = 5) -> bool:
    """检查近期是否持续下跌"""
    if len(df) < days + 1:
        return False
    try:
        recent_close = df['close'].iloc[-1]
        prior_close = df['close'].iloc[-days]
        return float(recent_close / prior_close - 1) < -0.05
    except Exception:
        return False


# ------------------------------------------------------------------
# 成本分析（辅助日K承接判断）
# ------------------------------------------------------------------
def calc_cost_analysis(df: pd.DataFrame) -> Dict:
    """计算成本相关指标，用于承接分析

    返回：平均成本、筹码密集区、支撑压力位
    """
    chip = calc_chip_distribution(df)
    if not chip['price_bins']:
        return {'avg_cost': 0, 'cost_support': 0, 'chip_pressure': 0}

    recent = df.iloc[-60:]
    latest_close = float(recent['close'].iloc[-1])

    return {
        'avg_cost': chip['avg_cost'],
        'cost_support': chip['peak_price'],  # 筹码峰作为支撑/压力参考
        'chip_pressure': round(max(0, chip['peak_price'] - latest_close) / latest_close * 100, 1),
        'above_ratio': chip['above_ratio'],  # 获利盘占比
        'concentration': chip['concentration'],
    }
