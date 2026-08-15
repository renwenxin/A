"""尾盘选股战法 全市场回测 — 超跌选股法 + 平台突破选股法 (BV15xgn6GEnA)

逻辑(与 screening/tail_session.py 保持一致,向量化):
  战法一 超跌:  近60日高点回撤≥25% + 当日涨幅>4% + 收盘站上当日VWAP(分时均线)
  战法二 平台:  收盘站上60日箱体上沿 + 放量≥1.5倍 + 前期平台振幅≤18% + 排除上吊线

执行:
  信号日 T 尾盘买入(收盘价+滑点) → T+1 卖出
  卖出方式: 开盘 / 收盘 / 最高(理想),各统计一套
  核心指标: 胜率 / 平均收益 / 盈亏比 / 次日涨停率 / 等权组合累计净值

用法:
  python ashare_review/analysis/tail_session_backtest.py [--limit 500] [--days 250]
"""
import sys, os, json, argparse, time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import numpy as np
import pandas as pd

# Windows 终端 GBK 编码下输出中文
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ashare_review.data.tdx_reader import TdxReader

FEE = 0.0005 * 2        # 佣金(简化单边0.05%,双边)
SLIPPAGE_BUY = 0.001    # 尾盘买入滑点 +0.1%
SLIPPAGE_SELL = 0.001   # 次日卖出滑点 -0.1%
TOTAL_COST = FEE + SLIPPAGE_BUY + SLIPPAGE_SELL  # ≈0.35%


def board_threshold(code: str) -> float:
    """涨停阈值: 主板10% / 创业板·科创20% / 北交所30%"""
    if code.startswith(('300', '301', '688')):
        return 0.20
    if code.startswith(('8', '4')):
        return 0.30
    return 0.10


def is_a_stock(code: str) -> bool:
    return code[0] in ('0', '3', '6')


def compute_signals(df: pd.DataFrame, code: str, params: Dict) -> pd.DataFrame:
    """对单只股票计算尾盘信号,返回含次日收益的 DataFrame"""
    if len(df) < 300:
        return pd.DataFrame()
    df = df.sort_values('trade_date').reset_index(drop=True)
    close = df['close'].astype(float)
    if (close <= 0).any():
        return pd.DataFrame()

    w_dd = params['drawdown_window']
    w_box = params['box_window']
    g_min = params['daily_gain_min'] / 100.0
    dd_min = params['drawdown_min'] / 100.0
    vr_min = params['vol_ratio_min']
    pw_max = params['platform_width_max'] / 100.0

    prev_close = close.shift(1)
    daily_gain = close / prev_close - 1.0

    window_high = df['high'].rolling(w_dd).max().shift(1)
    drawdown = close / window_high - 1.0

    vwap = df['amount'].astype(float) / df['volume'].astype(float).replace(0, np.nan)
    above_vwap = close > vwap

    vol_ma5 = df['volume'].rolling(5).mean().shift(1)
    vol_ratio = df['volume'] / vol_ma5

    box_top = df['close'].rolling(w_box).max().shift(1)
    plat_low = df['low'].rolling(w_box).min().shift(1)
    plat_high = df['high'].rolling(w_box).max().shift(1)
    plat_width = (plat_high - plat_low) / plat_low

    hi = df['high'].astype(float)
    lo = df['low'].astype(float)
    op = df['open'].astype(float)
    body = (close - op).abs()
    upper_shadow = hi - np.maximum(op, close)
    shooting = (upper_shadow >= 2.5 * body) & (upper_shadow / close.replace(0, np.nan) >= 0.03)

    rise_from_low = close / df['low'].rolling(250).min().shift(1) - 1.0

    # ── 信号 ──
    oversold = (drawdown <= -dd_min) & (daily_gain > g_min) & above_vwap
    platform = (close > box_top) & (vol_ratio >= vr_min) & (plat_width <= pw_max) & (~shooting)

    if not (oversold | platform).any():
        return pd.DataFrame()

    sig = pd.DataFrame(index=df.index)
    sig['code'] = code
    sig['trade_date'] = df['trade_date']
    sig['close'] = close
    sig['daily_gain'] = daily_gain * 100
    sig['drawdown'] = drawdown * 100
    sig['vol_ratio'] = vol_ratio
    sig['plat_width'] = plat_width * 100
    sig['rise_from_low'] = rise_from_low * 100
    sig['above_vwap'] = above_vwap

    nxt_open = df['open'].shift(-1).astype(float)
    nxt_close = df['close'].shift(-1).astype(float)
    nxt_high = df['high'].shift(-1).astype(float)
    # T+1 收益(相对信号日收盘,净收益扣成本)
    sig['open_ret'] = (nxt_open / close - 1.0) - TOTAL_COST
    sig['close_ret'] = (nxt_close / close - 1.0) - TOTAL_COST
    sig['high_ret'] = (nxt_high / close - 1.0) - TOTAL_COST
    # T+1 是否涨停(相对信号日收盘,即次日涨停阈值)
    th = board_threshold(code)
    sig['next_limit'] = (nxt_close / close - 1.0) >= th * 0.98
    sig['board_th'] = th
    # T+1 开盘是否跳空高开
    sig['next_gap_up'] = nxt_open / close - 1.0 > 0.005

    out = []
    for idx in sig.index:
        if oversold.loc[idx]:
            row = sig.loc[idx].copy()
            row['signal'] = '超跌'
            out.append(row)
        if platform.loc[idx]:
            row = sig.loc[idx].copy()
            row['signal'] = '平台突破'
            out.append(row)
    return pd.DataFrame(out) if out else pd.DataFrame()


def stats(df: pd.DataFrame, ret_col: str, label: str) -> Dict:
    if df is None or df.empty:
        return {'label': label, 'n': 0}
    rets = df[ret_col].dropna()
    n = len(rets)
    if n == 0:
        return {'label': label, 'n': 0}
    wins = (rets > 0).sum()
    losses = rets[rets <= 0]
    gains = rets[rets > 0]
    avg_win = gains.mean() if len(gains) else 0
    avg_loss = losses.mean() if len(losses) else 0
    pl_ratio = (avg_win / abs(avg_loss)) if avg_loss < 0 else float('inf')
    return {
        'label': label, 'n': n,
        'win_rate': wins / n * 100,
        'avg_ret': rets.mean() * 100,
        'med_ret': rets.median() * 100,
        'pl_ratio': pl_ratio,
        'limit_rate': df['next_limit'].mean() * 100 if 'next_limit' in df else 0,
    }


def print_stats_table(rows: List[Dict]):
    print(f"{'卖出方式':<8}{'信号数':>6}{'胜率%':>8}{'均收益%':>9}{'中位%':>8}{'盈亏比':>8}{'次日涨停%':>10}")
    for r in rows:
        if r['n'] == 0:
            print(f"{r['label']:<8}{'—':>6}")
            continue
        print(f"{r['label']:<8}{r['n']:>6}{r['win_rate']:>8.1f}{r['avg_ret']:>9.2f}"
              f"{r['med_ret']:>8.2f}{r['pl_ratio']:>8.2f}{r['limit_rate']:>10.1f}")


def run_portfolio(sig: pd.DataFrame, ret_col: str) -> Dict:
    """等权组合: 每天收盘买入全部信号票,次日开盘/收盘卖出,累乘净值"""
    if sig.empty:
        return {}
    s = sig.copy()
    s = s.dropna(subset=[ret_col])   # 剔除无次日数据的信号(最后一天)
    s['d'] = pd.to_datetime(s['trade_date'])
    daily = s.groupby('d')[ret_col].mean()
    # 防单日极端收益导致净值<0(幂运算NaN),净值下限截断到0.01
    daily_safe = daily.clip(lower=-0.999)
    nav = (1 + daily_safe).cumprod()
    peak = nav.cummax()
    max_dd = ((nav - peak) / peak).min() * 100
    n_days = len(daily)
    total = (nav.iloc[-1] - 1) * 100
    ann = (nav.iloc[-1] ** (250 / n_days) - 1) * 100 if (n_days and nav.iloc[-1] > 0) else float('nan')
    up_days = (daily > 0).sum() / n_days * 100
    return {
        'trades': len(s), 'days': n_days, 'total_ret': total,
        'annual_ret': ann, 'max_drawdown': max_dd,
        'day_win_rate': up_days,
        'avg_daily_pos': len(s) / n_days if n_days else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='限制扫描股票数(调试)')
    ap.add_argument('--days', type=int, default=250, help='回测交易天数')
    ap.add_argument('--drawdown-window', type=int, default=60)
    ap.add_argument('--drawdown-min', type=float, default=25.0)
    ap.add_argument('--daily-gain-min', type=float, default=4.0)
    ap.add_argument('--box-window', type=int, default=60)
    ap.add_argument('--vol-ratio-min', type=float, default=1.5)
    ap.add_argument('--platform-width-max', type=float, default=18.0)
    args = ap.parse_args()

    params = {
        'drawdown_window': args.drawdown_window, 'drawdown_min': args.drawdown_min,
        'daily_gain_min': args.daily_gain_min, 'box_window': args.box_window,
        'vol_ratio_min': args.vol_ratio_min, 'platform_width_max': args.platform_width_max,
    }

    tdx = TdxReader()
    stocks = [(c, m) for c, m in tdx.list_stocks() if m != 'bj' and is_a_stock(c)]
    if args.limit:
        stocks = stocks[:args.limit]

    # 名称映射(用于 ST 过滤,可选)
    name_map = {}
    nm_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'stock_name_map.json')
    if os.path.exists(nm_path):
        try:
            with open(nm_path, 'r', encoding='utf-8') as f:
                name_map = json.load(f)
        except Exception:
            pass

    # 确定回测日期范围: 取最近 args.days 个交易日
    end_date = datetime.now().date()
    all_signals = []
    scanned = 0
    t0 = time.time()

    for i, (code, market) in enumerate(stocks):
        try:
            df = tdx.read_daily(code, market)
        except Exception:
            continue
        if df.empty or len(df) < 300:
            continue
        name = name_map.get(code, '')
        if name.startswith(('ST', '*ST', 'SST', 'S*ST')):
            continue
        # 只保留最近 args.days 个交易日做信号日
        df = df.tail(args.days + 300).reset_index(drop=True)
        # 计算(需要前面 300 条做指标 warmup,信号日限制在后 days 天)
        sig = compute_signals(df, code, params)
        if not sig.empty:
            cutoff = df['trade_date'].iloc[-args.days]
            sig = sig[pd.to_datetime(sig['trade_date']) >= pd.to_datetime(cutoff)]
            if not sig.empty:
                all_signals.append(sig)
        scanned += 1
        if scanned % 1000 == 0:
            el = time.time() - t0
            print(f'  扫描 {scanned}/{len(stocks)} · 信号 {sum(len(s) for s in all_signals)} 条 · {el:.0f}s', flush=True)

    if not all_signals:
        print('无信号')
        return
    S = pd.concat(all_signals, ignore_index=True)
    print(f'\n扫描 {scanned} 只 · 信号 {len(S)} 条 · {time.time()-t0:.0f}s')

    # ── 按卖出方式总览 ──
    print('\n═══ 尾盘选股 · 全信号总览(买入=信号日收盘) ═══')
    print_stats_table([
        stats(S, 'open_ret', 'T+1开盘'),
        stats(S, 'close_ret', 'T+1收盘'),
        stats(S, 'high_ret', 'T+1最高'),
    ])

    # ── 按战法分层 ──
    print('\n═══ 分战法(T+1 开盘卖出) ═══')
    print_stats_table([
        stats(S[S['signal'] == '超跌'], 'open_ret', '超跌'),
        stats(S[S['signal'] == '平台突破'], 'open_ret', '平台突破'),
    ])

    # ── 平台突破: 位置高低分层(验证"位置高只轻仓") ──
    plat = S[S['signal'] == '平台突破']
    if not plat.empty:
        print('\n═══ 平台突破 · 按距250日低点涨幅分层(T+1 开盘) ═══')
        print_stats_table([
            stats(plat[plat['rise_from_low'] < 50], 'open_ret', '<50%(中低位)'),
            stats(plat[plat['rise_from_low'] >= 50], 'open_ret', '≥50%(高位)'),
        ])
        # 有无上吊线不该出现在信号里(已排除),只展示量能强弱分层
        print('\n═══ 平台突破 · 按放量倍数分层(T+1 开盘) ═══')
        print_stats_table([
            stats(plat[plat['vol_ratio'] < 2], 'open_ret', '1.5~2倍量'),
            stats(plat[plat['vol_ratio'] >= 2], 'open_ret', '≥2倍量(量爆)'),
        ])

    # ── 组合模拟 ──
    print('\n═══ 等权组合模拟(次日开盘卖出) ═══')
    comb = run_portfolio(S, 'open_ret')
    if comb:
        print(f"交易 {comb['trades']} 次 · {comb['days']} 天 · 平均每日持仓 {comb['avg_daily_pos']:.1f} 只")
        print(f"累计收益 {comb['total_ret']:.1f}% · 年化 {comb['annual_ret']:.1f}% · 最大回撤 {comb['max_drawdown']:.1f}% · 盈利日占比 {comb['day_win_rate']:.1f}%")


if __name__ == '__main__':
    main()
