"""明日突破预测 — 历史特征校准

统计"压力位下方蓄势日"的各特征 → 次日实际突破率，确定评分权重。
候选池: limit_up_pool.json（年涨停≥10 主板，与 V3 同池）。
压力位近似: 前60日最高高点(shift 排除当日) —— 与 zigzag NN 高度相关，可向量化。
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import numpy as np
import pandas as pd
from ashare_review.data.tdx_reader import TdxReader

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
POOL_FILE = os.path.join(DATA_DIR, 'limit_up_pool.json')
N_LOOKBACK = 250      # 每只取最近250根
PRESSURE_WIN = 60     # 压力位 = 前60日高点

def main():
    tdx = TdxReader()
    with open(POOL_FILE, encoding='utf-8') as f:
        pool = json.load(f)['pool']
    limit_map = {s['code']: s.get('limit_count', 0) for s in pool}
    codes = list(limit_map.keys())
    print(f'候选池: {len(codes)} 只')

    # 收集所有蓄势日样本
    samples = []   # (near, vol_shrink, probe, ma_bull, limit_ge15, broke_next)
    t0 = time.time()
    for si, code in enumerate(codes):
        market = 'sh' if code.startswith('6') else 'sz'
        try:
            df = tdx.read_daily(code, market)
            if df is None or len(df) < PRESSURE_WIN + 40:
                continue
            df = df.iloc[-N_LOOKBACK:].reset_index(drop=True)
            high = df['high'].values.astype(float)
            low = df['low'].values.astype(float)
            close = df['close'].values.astype(float)
            vol = df['volume'].values.astype(float)
            n = len(df)
            # 压力位: 前60日高点(不含当日)
            pressure = np.full(n, np.nan)
            for i in range(PRESSURE_WIN, n):
                pressure[i] = np.max(high[i - PRESSURE_WIN:i])
            # 均线
            cs = pd.Series(close)
            ma5 = cs.rolling(5).mean().values
            ma10 = cs.rolling(10).mean().values
            ma20 = cs.rolling(20).mean().values
            vol_ma5 = pd.Series(vol).rolling(5).mean().shift(1).values
            lim = limit_map.get(code, 0)
            for i in range(PRESSURE_WIN, n - 1):
                p = pressure[i]
                if not np.isfinite(p) or p <= 0:
                    continue
                dist = (p - close[i]) / p * 100
                if not (0 < dist <= 10):
                    continue   # 只统计蓄势日(下方0~10%)
                near = dist <= 3.0
                vs = vol_ma5[i]
                vol_shrink = (vs > 0) and (vol[i] < vs * 0.7)
                probe = False
                s10 = max(0, i - 15)
                if np.max(high[s10:i]) >= p * 0.97:
                    probe = True
                mb = np.isfinite(ma5[i]) and np.isfinite(ma10[i]) and np.isfinite(ma20[i])                      and ma5[i] > ma10[i] > ma20[i]
                broke = close[i + 1] > p
                samples.append((near, vol_shrink, probe, mb, lim >= 15, broke))
        except Exception:
            continue
        if (si + 1) % 100 == 0:
            print(f'  {si+1}/{len(codes)} ...')
    print(f'蓄势日样本: {len(samples)}  ({time.time()-t0:.0f}s)')
    if not samples:
        print('无样本'); return

    arr = np.array(samples, dtype=float)
    base = arr[:, 5].mean() * 100
    print(f'\n基准: 蓄势日次日突破率 = {base:.1f}%  (n={len(arr)})')
    feats = [('near(距压力位≤3%)', 0), ('vol_shrink(地量<0.7×5日均)', 1),
             ('probe(近15日试盘摸高≥97%)', 2), ('ma_bull(5>10>20多头)', 3),
             ('limit≥15(股性)', 4)]
    print('\n=== 单特征 → 次日突破率 ===')
    for name, col in feats:
        mask = arr[:, col] == 1
        if mask.sum() >= 20:
            rate = arr[mask, 5].mean() * 100
            lift = rate - base
            print(f'  {name}: {rate:.1f}%  (n={int(mask.sum())}, lift {lift:+.1f}pp)')
    print('\n=== 组合特征 ===')
    combos = [
        ('near+vol_shrink', (0, 1)), ('near+probe', (0, 2)),
        ('near+ma_bull', (0, 3)), ('near+vol_shrink+probe', (0, 1, 2)),
        ('near+vol_shrink+ma_bull', (0, 1, 3)), ('near+probe+limit', (0, 2, 4)),
        ('probe+vol_shrink', (1, 2)), ('near+probe+vol_shrink+limit', (0, 2, 1, 4)),
    ]
    for name, cols in combos:
        mask = np.ones(len(arr), dtype=bool)
        for c in cols:
            mask &= (arr[:, c] == 1)
        if mask.sum() >= 10:
            rate = arr[mask, 5].mean() * 100
            print(f'  {name}: {rate:.1f}%  (n={int(mask.sum())}, lift {rate-base:+.1f}pp)')
    # 反向验证: 什么都不满足的
    mask_none = np.ones(len(arr), dtype=bool)
    for _, c in feats:
        mask_none &= (arr[:, c] == 0)
    if mask_none.sum() >= 10:
        print(f'\n  (无任何特征): {arr[mask_none,5].mean()*100:.1f}%  (n={int(mask_none.sum())})')

if __name__ == '__main__':
    main()
