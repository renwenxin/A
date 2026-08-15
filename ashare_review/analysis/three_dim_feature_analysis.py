"""板块热度 + 市场情绪 + 股票股性 三维特征提取 — 全部721笔交易"""
import pandas as pd
import numpy as np
import json, os, sys, time, struct
from collections import defaultdict

sys.path.insert(0, '.')
from ashare_review.data.tdx_reader import TdxReader, RECORD_SIZE

# ===== Load trades =====
df = pd.read_excel('D:/cursor/project/ashare_review/data/vol180_breakout_backtest_250d.xlsx',
                   sheet_name='交易明细', skiprows=1)
cols = ['序号','信号日','买入日','代码','评分','信号涨幅%','信号价','突破幅度%',
        '量比MAVOL180','压力位','MAVOL180','买入价','卖出日','卖出价',
        '持有天数','净收益%','结果','退出原因','是否连板','卖出日涨跌%','信号理由']
df.columns = cols[:len(df.columns)]
df = df.iloc[1:].copy()
df = df.dropna(subset=['代码'])
df['代码'] = df['代码'].astype(str).str.zfill(6)
df['净收益%'] = pd.to_numeric(df['净收益%'], errors='coerce')
df['信号日'] = pd.to_datetime(df['信号日']).dt.strftime('%Y%m%d')

print(f'Total trades: {len(df)}')
print(f'ZT: {len(df[df["是否连板"]=="是"])}, Non-ZT: {len(df[df["是否连板"]=="否"])}')

# ===== Load caches =====
data_dir = 'D:/cursor/project/ashare_review/data'

# sector_daily_stats
with open(os.path.join(data_dir, 'sector_daily_stats.json'), 'r', encoding='utf-8') as f:
    sector_stats = json.load(f)
print(f'Sector stats: {len(sector_stats)} dates')

# gainers_7pct
with open(os.path.join(data_dir, 'gainers_7pct.json'), 'r', encoding='utf-8') as f:
    gainers = json.load(f)
print(f'Gainers: {len(gainers)} dates')

# industry_map
imap_path = os.path.join(data_dir, 'industry_map.json')
industry_map = {}
if os.path.exists(imap_path):
    with open(imap_path, 'r', encoding='utf-8') as f:
        industry_map = json.load(f)
print(f'Industry map: {len(industry_map)} stocks')

# ===== TDX reader for stock features =====
tdx = TdxReader()

def count_limit_ups_for_code(code, lookback=250):
    """Count recent limit-ups from .day file"""
    threshold = 0.095
    if code.startswith(('300','301','688')): threshold = 0.199
    if code.startswith(('8','4')): threshold = 0.299
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith(('8','4')): market = 'bj'
    fpath = os.path.join(tdx._market_dir(market), f'{market}{code}.day')
    if not os.path.exists(fpath): return 0
    try:
        fsize = os.path.getsize(fpath)
        read_size = min(RECORD_SIZE * lookback, fsize)
        with open(fpath, 'rb') as f:
            f.seek(fsize - read_size)
            tail = f.read(read_size)
        records = len(tail) // RECORD_SIZE
        count, prev_close = 0, None
        for i in range(records):
            offset = i * RECORD_SIZE
            close = struct.unpack('I', tail[offset+16:offset+20])[0] / 100.0
            if prev_close and prev_close > 0:
                if (close - prev_close) / prev_close >= threshold:
                    count += 1
            prev_close = close
        return count
    except: return 0

def get_stock_features(code, signal_date):
    """Extract stock-level features"""
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith(('8','4')): market = 'bj'
    feats = {}
    try:
        df_stock = tdx.read_daily(code, market)
        if df_stock is None or df_stock.empty or len(df_stock) < 20:
            return feats
        from datetime import datetime
        target = datetime.strptime(signal_date, '%Y%m%d').date()
        df_stock = df_stock[df_stock['trade_date'].apply(
            lambda x: (x.date() if hasattr(x, 'date') else x) <= target)]
        if len(df_stock) < 60: return feats

        closes = df_stock['close'].values.astype(float)
        highs = df_stock['high'].values.astype(float)
        volumes = df_stock['volume'].values.astype(float)
        amounts = df_stock['amount'].values.astype(float) if 'amount' in df_stock.columns else volumes * closes
        idx = len(df_stock) - 1

        # Year, 60-day, 20-day limit-up counts
        feats['lu_250d'] = count_limit_ups_for_code(code, 250)
        feats['lu_60d'] = count_limit_ups_for_code(code, 60)
        feats['lu_20d'] = count_limit_ups_for_code(code, 20)

        # Market cap estimate
        avg_price = closes[idx]
        avg_vol = np.mean(volumes[-20:])
        feats['est_float_cap'] = round(avg_price * avg_vol * 250 / 1e8, 1)
        feats['price'] = round(closes[idx], 2)

        # Historical max consecutive limit-ups (simplified: count consecutive days >=9.5% in last 250d)
        pct_chg = np.diff(closes) / closes[:-1] * 100
        threshold = 9.5
        max_consec = 0; curr = 0
        for c in pct_chg[-250:]:
            if c >= threshold: curr += 1; max_consec = max(max_consec, curr)
            else: curr = 0
        feats['max_consec_zt'] = max_consec

        # Is at 250-day high?
        if len(closes) >= 250:
            high_250 = np.max(highs[-250:])
            feats['is_250d_high'] = 1 if closes[idx] >= high_250 * 0.97 else 0
        else:
            feats['is_250d_high'] = 0

        # Days since last limit-up
        days_since = 100
        for i in range(min(60, len(pct_chg))):
            if pct_chg[-(i+1)] >= threshold:
                days_since = i
                break
        feats['days_since_last_zt'] = days_since

        # Average amount (liquidity)
        feats['avg_amount_20d_yi'] = round(np.mean(amounts[-20:]) / 1e8, 1)

    except Exception as e:
        pass
    return feats

def get_sector_features(code, signal_date):
    """Extract sector-level features for the stock's industry on signal date"""
    feats = {}
    industry = industry_map.get(code, '')
    if not industry: return feats
    feats['industry'] = industry

    ds = signal_date[:8]
    today = sector_stats.get(ds, {}).get(industry)
    if today is None: return feats

    feats['sec_zt_count'] = today.get('zt_count', 0)
    feats['sec_avg_gain'] = today.get('avg_gain', 0)
    feats['sec_has_zhongjun'] = 1 if today.get('has_zhongjun', False) else 0
    feats['sec_stock_count'] = today.get('count', 0)

    # Is this the #1 hot sector of the day?
    all_secs = sector_stats.get(ds, {})
    if all_secs:
        max_zt = max((s.get('zt_count', 0) for s in all_secs.values()), default=0)
        feats['sec_is_top1'] = 1 if (feats['sec_zt_count'] >= max_zt and max_zt >= 3) else 0
        # Rank
        ranked = sorted(all_secs.items(), key=lambda x: x[1].get('zt_count', 0), reverse=True)
        rank = next((i+1 for i, (sec, _) in enumerate(ranked) if sec == industry), 99)
        feats['sec_rank'] = rank

    # Consecutive strength: how many of last 3 days had positive gain
    ds_list = sorted(sector_stats.keys())
    try:
        ds_idx = ds_list.index(ds)
    except ValueError:
        return feats
    pos_days = 0
    for i in range(max(0, ds_idx-3), ds_idx):
        prev_ds = ds_list[i]
        sec = sector_stats.get(prev_ds, {}).get(industry)
        if sec and sec.get('avg_gain', 0) > 0:
            pos_days += 1
    feats['sec_pos_days_3d'] = pos_days

    return feats

def get_market_features(signal_date):
    """Extract market-wide features for the signal date"""
    feats = {}
    ds = signal_date[:8]

    # Total 7%+ gainers as market heat proxy
    day_gainers = gainers.get(ds, [])
    feats['mkt_7pct_count'] = len(day_gainers)

    # Count limit-ups (>=9.5%)
    zt_count = sum(1 for g in day_gainers if g.get('change_pct', 0) >= 9.5)
    feats['mkt_zt_count'] = zt_count

    # Average gain of 7%+ stocks
    if day_gainers:
        feats['mkt_avg_7pct_gain'] = round(np.mean([g.get('change_pct', 0) for g in day_gainers]), 2)
    else:
        feats['mkt_avg_7pct_gain'] = 0

    # Number of sectors with >=3 ZT
    if ds in sector_stats:
        hot_sectors = sum(1 for s in sector_stats[ds].values() if s.get('zt_count', 0) >= 3)
        feats['mkt_hot_sector_count'] = hot_sectors
    else:
        feats['mkt_hot_sector_count'] = 0

    # SH index MA60 status
    try:
        df_sh = tdx.read_daily('999999', 'sh')
        if df_sh is not None and len(df_sh) >= 60:
            from datetime import datetime
            target = datetime.strptime(signal_date, '%Y%m%d').date()
            df_sh = df_sh[df_sh['trade_date'].apply(
                lambda x: (x.date() if hasattr(x, 'date') else x) <= target)]
            if len(df_sh) >= 60:
                close = float(df_sh['close'].iloc[-1])
                ma60 = float(df_sh['close'].rolling(60).mean().iloc[-1])
                feats['mkt_sh_above_ma60'] = 1 if close > ma60 else 0
    except: pass

    return feats

# ===== EXTRACT ALL FEATURES =====
cache_path = os.path.join(data_dir, 'three_dim_features.json')

if os.path.exists(cache_path):
    with open(cache_path, 'r') as f:
        all_features = json.load(f)
    print(f'\nLoaded cached features: {len(all_features)} trades')
else:
    all_features = []
    total = len(df)
    t0 = time.time()
    for idx, (_, trade) in enumerate(df.iterrows()):
        if (idx + 1) % 50 == 0:
            print(f'  {idx+1}/{total} ({time.time()-t0:.0f}s)...', flush=True)

        code = trade['代码']
        signal_date = str(trade['信号日'])

        feats = {'code': code, 'signal_date': signal_date, 'is_zt': 1 if trade['是否连板'] == '是' else 0}

        # Stock
        stock_f = get_stock_features(code, signal_date)
        feats.update(stock_f)
        # Sector
        sector_f = get_sector_features(code, signal_date)
        feats.update(sector_f)
        # Market
        market_f = get_market_features(signal_date)
        feats.update(market_f)

        all_features.append(feats)

    with open(cache_path, 'w') as f:
        json.dump(all_features, f, ensure_ascii=False)
    print(f'Cached: {len(all_features)} trades')

# ===== COMPARE ZT vs NON-ZT =====
zt_feats = [f for f in all_features if f.get('is_zt') == 1]
no_feats = [f for f in all_features if f.get('is_zt') == 0]
print(f'\nFeatures extracted: {len(zt_feats)} ZT, {len(no_feats)} Non-ZT')

# All numeric feature keys
numeric_keys = [k for k in all_features[0].keys()
                if k not in ('code', 'signal_date', 'is_zt', 'industry')
                and isinstance(all_features[0].get(k), (int, float))]

from scipy import stats as scipy_stats

print()
print('=' * 85)
print('  三维特征：连板 vs 非连板 全面对比')
print('=' * 85)

sig_features = []
for key in numeric_keys:
    zt_vals = [f.get(key, 0) for f in zt_feats]
    no_vals = [f.get(key, 0) for f in no_feats]

    # Skip if all same
    if np.std(zt_vals + no_vals) < 1e-6: continue

    zt_mean = np.mean(zt_vals)
    no_mean = np.mean(no_vals)
    diff = zt_mean - no_mean

    try:
        t_stat, p_val = scipy_stats.ttest_ind(zt_vals, no_vals)
    except:
        continue

    if p_val < 0.10:  # statistically significant
        sig_features.append((key, zt_mean, no_mean, diff, p_val, t_stat))

sig_features.sort(key=lambda x: x[4])

print(f'\n  {"特征":<30s} {"连板组":>10s} {"非连板组":>10s} {"差异":>8s} {"p值":>8s} {"方向":>10s}')
print(f'  {"-"*80}')
for key, zt_mean, no_mean, diff, p_val, t_stat in sig_features:
    direction = '更高⬆' if diff > 0 else '更低⬇'
    sig = '***' if p_val < 0.01 else ('**' if p_val < 0.05 else '*')
    name = key
    if zt_mean > 1000:
        z_str = f'{zt_mean/1e8:>.1f}亿'
        n_str = f'{no_mean/1e8:>.1f}亿'
    elif key.endswith('_count') or key.endswith('_days') or 'days_' in key:
        z_str = f'{zt_mean:.1f}'
        n_str = f'{no_mean:.1f}'
    else:
        z_str = f'{zt_mean:.2f}'
        n_str = f'{no_mean:.2f}'
    print(f'  {sig} {name:<27s} {z_str:>10s} {n_str:>10s} {diff:>+8.2f} {p_val:>8.4f} {direction:>10s}')

print(f'\n  共 {len(sig_features)} 个显著特征 (p<0.1)')

# ===== Top distinguishing features by category =====
print()
print('=' * 85)
print('  按类别汇总 TOP 区分特征')
print('=' * 85)

categories = {
    '板块热度': ['sec_zt_count', 'sec_avg_gain', 'sec_has_zhongjun', 'sec_is_top1', 'sec_rank', 'sec_pos_days_3d', 'sec_stock_count'],
    '市场情绪': ['mkt_7pct_count', 'mkt_zt_count', 'mkt_avg_7pct_gain', 'mkt_hot_sector_count', 'mkt_sh_above_ma60'],
    '股票股性': ['lu_250d', 'lu_60d', 'lu_20d', 'est_float_cap', 'price', 'max_consec_zt', 'is_250d_high', 'days_since_last_zt', 'avg_amount_20d_yi'],
}

for cat, keys in categories.items():
    print(f'\n  --- {cat} ---')
    cat_sig = [(k, *next((x[1:] for x in sig_features if x[0] == k), (0,0,0,1,0))) for k in keys if k in numeric_keys]
    cat_sig.sort(key=lambda x: x[4])
    for key, zt_mean, no_mean, diff, p_val, t_stat in cat_sig:
        sig = '***' if p_val < 0.01 else ('**' if p_val < 0.05 else ('*' if p_val < 0.1 else ' '))
        direction = '更高' if diff > 0 else '更低'
        print(f'    {sig} {key:<30s} {zt_mean:>10.4f} vs {no_mean:>10.4f}  {direction} (p={p_val:.4f})')

print()
print('Done.')
