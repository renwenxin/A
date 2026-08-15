"""Extract 3-dim features for all 721 trades - compact version"""
import json, os, struct, sys, time, numpy as np, pandas as pd
from collections import defaultdict

sys.path.insert(0, '.')
from ashare_review.data.tdx_reader import TdxReader, RECORD_SIZE

# Load trades
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

# Load caches
data_dir = 'D:/cursor/project/ashare_review/data'
with open(os.path.join(data_dir, 'sector_daily_stats.json'), 'r', encoding='utf-8') as f:
    sector_stats = json.load(f)
with open(os.path.join(data_dir, 'gainers_7pct.json'), 'r', encoding='utf-8') as f:
    gainers = json.load(f)
with open(os.path.join(data_dir, 'industry_map.json'), 'r', encoding='utf-8') as f:
    industry_map = json.load(f)

print(f'Trades: {len(df)} | Sector dates: {len(sector_stats)} | Gainers dates: {len(gainers)} | Industry: {len(industry_map)} stocks')

tdx = TdxReader()

def count_limit_ups(code, lookback=250):
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

def extract_features(code, signal_date):
    f = {}
    ds = signal_date[:8]
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith(('8','4')): market = 'bj'

    # Stock features
    try:
        df_stock = tdx.read_daily(code, market)
        if df_stock is not None and not df_stock.empty and len(df_stock) >= 60:
            from datetime import datetime
            target = datetime.strptime(signal_date, '%Y%m%d').date()
            df_stock = df_stock[df_stock['trade_date'].apply(
                lambda x: (x.date() if hasattr(x, 'date') else x) <= target)]
            if len(df_stock) >= 60:
                idx = len(df_stock) - 1
                closes = df_stock['close'].values.astype(float)
                highs = df_stock['high'].values.astype(float)
                volumes = df_stock['volume'].values.astype(float)
                amounts = df_stock['amount'].values.astype(float) if 'amount' in df_stock.columns else volumes * closes

                f['lu_250d'] = count_limit_ups(code, 250)
                f['lu_60d'] = count_limit_ups(code, 60)
                f['lu_20d'] = count_limit_ups(code, 20)

                avg_price = closes[idx]
                avg_vol = np.mean(volumes[-20:])
                f['est_float_cap'] = round(float(avg_price * avg_vol * 250 / 1e8), 1)
                f['price'] = round(float(closes[idx]), 2)
                f['avg_amount_20d_yi'] = round(float(np.mean(amounts[-20:]) / 1e8), 1)

                pct_chg_all = np.diff(closes) / closes[:-1] * 100
                max_consec = 0; curr = 0
                for c in pct_chg_all[-250:]:
                    if c >= 9.5: curr += 1; max_consec = max(max_consec, curr)
                    else: curr = 0
                f['max_consec_zt'] = max_consec

                if len(closes) >= 250:
                    high_250 = np.max(highs[-250:])
                    f['is_250d_high'] = 1 if closes[idx] >= high_250 * 0.97 else 0
                else:
                    f['is_250d_high'] = 0

                days_since = 100
                for i in range(min(60, len(pct_chg_all))):
                    if pct_chg_all[-(i+1)] >= 9.5:
                        days_since = i; break
                f['days_since_last_zt'] = days_since

                # Pre-breakout
                pre_vol = volumes[-11:-1]; pre_close = closes[-11:-1]
                pre_high = highs[-11:-1]
                pre_low = df_stock['low'].values.astype(float)[-11:-1]
                x = np.arange(10)
                vm = np.mean(pre_vol); pm = np.mean(pre_close)
                f['pre_vol_slope'] = round(float(np.polyfit(x, pre_vol, 1)[0] / vm * 100), 2) if vm > 0 else 0
                f['pre_price_slope'] = round(float(np.polyfit(x, pre_close, 1)[0] / pm * 100), 2) if pm > 0 else 0
                vl3 = np.mean(pre_vol[-3:]); vf7 = np.mean(pre_vol[:7])
                f['pre_vol_expansion'] = round(float(vl3 / vf7), 2) if vf7 > 0 else 1
                rh = np.max(pre_high[-5:]) - np.min(pre_low[-5:])
                rf = np.max(pre_high[:5]) - np.min(pre_low[:5])
                f['pre_price_compression'] = round(float(rh / rf), 2) if rf > 0 else 1
                pc = np.diff(pre_close) / pre_close[:-1] * 100
                f['pre_sum_chg'] = round(float(np.sum(pc)), 2)
                f['pre_pos_days'] = int(np.sum(pc > 0))
                f['pre_close_pos'] = round(float((closes[-2] - np.min(pre_low)) / (np.max(pre_high) - np.min(pre_low))), 2) if np.max(pre_high) > np.min(pre_low) else 0.5
    except: pass

    # Sector features
    industry = industry_map.get(code, '')
    if industry and ds in sector_stats:
        today = sector_stats[ds].get(industry)
        if today:
            f['sec_zt_count'] = today.get('zt_count', 0)
            f['sec_avg_gain'] = round(today.get('avg_gain', 0), 2)
            f['sec_has_zhongjun'] = 1 if today.get('has_zhongjun', False) else 0
            f['sec_stock_count'] = today.get('count', 0)
            all_secs = sector_stats[ds]
            max_zt = max((s.get('zt_count', 0) for s in all_secs.values()), default=0)
            f['sec_is_top1'] = 1 if (today.get('zt_count', 0) >= max_zt and max_zt >= 3) else 0
            ranked = sorted(all_secs.items(), key=lambda x: x[1].get('zt_count', 0), reverse=True)
            f['sec_rank'] = next((i+1 for i, (sec, _) in enumerate(ranked) if sec == industry), 99)
            ds_list = sorted(sector_stats.keys())
            try:
                ds_idx = ds_list.index(ds)
                f['sec_pos_days_3d'] = sum(1 for i in range(max(0, ds_idx-3), ds_idx)
                    if sector_stats.get(ds_list[i], {}).get(industry, {}).get('avg_gain', 0) > 0)
            except: pass

    # Market features
    day_gainers = gainers.get(ds, [])
    f['mkt_7pct_count'] = len(day_gainers)
    f['mkt_zt_count'] = sum(1 for g in day_gainers if g.get('change_pct', 0) >= 9.5)
    if day_gainers:
        f['mkt_avg_7pct_gain'] = round(np.mean([g.get('change_pct', 0) for g in day_gainers]), 2)
    else:
        f['mkt_avg_7pct_gain'] = 0
    f['mkt_hot_sector_count'] = sum(1 for s in sector_stats.get(ds, {}).values() if s.get('zt_count', 0) >= 3)

    try:
        df_sh = tdx.read_daily('999999', 'sh')
        if df_sh is not None and len(df_sh) >= 60:
            from datetime import datetime
            target = datetime.strptime(signal_date, '%Y%m%d').date()
            df_sh = df_sh[df_sh['trade_date'].apply(lambda x: (x.date() if hasattr(x, 'date') else x) <= target)]
            if len(df_sh) >= 60:
                ma60 = float(df_sh['close'].rolling(60).mean().iloc[-1])
                f['mkt_sh_above_ma60'] = 1 if float(df_sh['close'].iloc[-1]) > ma60 else 0
    except: pass

    return f

# Extract all
all_features = []
total = len(df)
t0 = time.time()
for idx, (_, trade) in enumerate(df.iterrows()):
    if (idx + 1) % 50 == 0:
        print(f'  {idx+1}/{total} ({time.time()-t0:.0f}s)...', flush=True)
    code = trade['代码']
    sd = str(trade['信号日'])
    feats = {'code': code, 'signal_date': sd, 'is_zt': 1 if trade['是否连板'] == '是' else 0}
    feats.update(extract_features(code, sd))
    all_features.append(feats)

out_path = os.path.join(data_dir, 'three_dim_features.json')
with open(out_path, 'w', encoding='utf-8', errors='replace') as f:
    json.dump(all_features, f, ensure_ascii=False)
print(f'\nSaved {len(all_features)} trades to {out_path}')

# Quick check
for key in ['mkt_7pct_count','mkt_zt_count','mkt_hot_sector_count','sec_zt_count',
            'lu_250d','lu_60d','lu_20d','est_float_cap','price','is_250d_high',
            'pre_price_compression','pre_vol_expansion','pre_sum_chg','pre_pos_days']:
    vals = [f.get(key, 0) for f in all_features if f.get(key) is not None]
    if vals:
        print(f'  {key}: {sum(1 for v in vals if v!=0)}/{len(vals)} non-zero, mean={np.mean(vals):.2f}')
