"""连板前兆分析：对比连板组 vs 非连板组突破前10天的行为差异"""
import pandas as pd
import numpy as np
import json, os, sys, time
from scipy import stats

sys.path.insert(0, '.')
from ashare_review.data.tdx_reader import TdxReader

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

cache_path = 'D:/cursor/project/ashare_review/data/pre_breakout_behavior.json'

if os.path.exists(cache_path):
    with open(cache_path, 'r') as f:
        all_data = json.load(f)
    print(f'Loaded cached: {len(all_data["zt"])} ZT, {len(all_data["no_zt"])} Non-ZT')
else:
    tdx = TdxReader()

    def read_pre_breakout(code, signal_date_str, days_before=11):
        market = 'sh' if code.startswith('6') else 'sz'
        if code.startswith(('8','4')): market = 'bj'
        try:
            df_stock = tdx.read_daily(code, market)
            if df_stock is None or df_stock.empty or len(df_stock) < 20: return None
            from datetime import datetime
            target = datetime.strptime(signal_date_str, '%Y%m%d').date()
            df_stock = df_stock[df_stock['trade_date'].apply(
                lambda x: (x.date() if hasattr(x, 'date') else x) <= target)]
            if len(df_stock) < days_before + 5: return None
            return df_stock.iloc[-days_before:].copy()
        except: return None

    def calc_behavior(df_pre):
        n = len(df_pre)
        if n < 11: return None
        closes = df_pre['close'].values.astype(float)
        volumes = df_pre['volume'].values.astype(float)
        highs = df_pre['high'].values.astype(float)
        lows = df_pre['low'].values.astype(float)
        amounts = df_pre['amount'].values.astype(float) if 'amount' in df_pre.columns else volumes * closes

        pre_vol = volumes[-11:-1]
        pre_close = closes[-11:-1]
        pre_amt = amounts[-11:-1]
        pre_high = highs[-11:-1]
        pre_low = lows[-11:-1]

        pre_chg = np.diff(pre_close) / pre_close[:-1] * 100
        pre_chg = np.insert(pre_chg, 0, 0)
        pre_amp = (pre_high - pre_low) / pre_close * 100

        x = np.arange(10)
        vol_mean = np.mean(pre_vol)
        vol_slope_norm = np.polyfit(x, pre_vol, 1)[0] / vol_mean if vol_mean > 0 else 0
        price_mean = np.mean(pre_close)
        price_slope_norm = np.polyfit(x, pre_close, 1)[0] / price_mean if price_mean > 0 else 0
        amt_mean = np.mean(pre_amt)
        amt_slope_norm = np.polyfit(x, pre_amt, 1)[0] / amt_mean if amt_mean > 0 else 0

        vol_last3 = np.mean(pre_vol[-3:])
        vol_first7 = np.mean(pre_vol[:7])
        range_last5 = np.max(pre_high[-5:]) - np.min(pre_low[-5:])
        range_first5 = np.max(pre_high[:5]) - np.min(pre_low[:5])

        m = {
            'vol_mean_10d': float(vol_mean),
            'vol_slope_norm': float(round(vol_slope_norm * 100, 4)),
            'price_slope_norm': float(round(price_slope_norm * 100, 4)),
            'avg_abs_chg_10d': float(round(np.mean(np.abs(pre_chg)), 4)),
            'sum_chg_10d': float(round(np.sum(pre_chg), 4)),
            'avg_amp_10d': float(round(np.mean(pre_amp), 4)),
            'max_amp_10d': float(round(np.max(pre_amp), 4)),
            'avg_amt_10d': float(amt_mean),
            'amt_slope_norm': float(round(amt_slope_norm * 100, 4)),
            'vol_expansion': float(round(vol_last3 / vol_first7 if vol_first7 > 0 else 1, 4)),
            'price_compression': float(round(range_last5 / range_first5 if range_first5 > 0 else 1, 4)),
            'accumulation_signal': int(1 if (vol_slope_norm > 0.02 and abs(price_slope_norm) < 0.005) else 0),
            'pos_days_10d': int(np.sum(pre_chg > 0)),
            'max_chg_10d': float(round(np.max(pre_chg), 4)),
            'min_chg_10d': float(round(np.min(pre_chg), 4)),
            'close_pos_10d': float(round((closes[-2] - np.min(pre_low)) / (np.max(pre_high) - np.min(pre_low))
                                   if np.max(pre_high) > np.min(pre_low) else 0.5, 4)),
        }
        for i in range(10):
            m[f'vol_d{i-10}'] = float(pre_vol[i])
            m[f'chg_d{i-10}'] = float(round(pre_chg[i], 4))
            m[f'amp_d{i-10}'] = float(round(pre_amp[i], 4))
        return m

    zt_metrics = []
    no_zt_metrics = []
    total = len(df)
    t0 = time.time()
    for idx, (_, trade) in enumerate(df.iterrows()):
        if (idx + 1) % 100 == 0:
            print(f'  {idx+1}/{total} ({time.time()-t0:.0f}s)...', flush=True)
        code = trade['代码']
        signal_date = str(trade['信号日']).replace('-', '')[:8]
        df_pre = read_pre_breakout(code, signal_date, 11)
        if df_pre is None: continue
        m = calc_behavior(df_pre)
        if m is None: continue
        if trade['是否连板'] == '是':
            zt_metrics.append(m)
        else:
            no_zt_metrics.append(m)

    all_data = {'zt': zt_metrics, 'no_zt': no_zt_metrics}
    with open(cache_path, 'w') as f:
        json.dump(all_data, f)
    print(f'Cached: {len(zt_metrics)} ZT, {len(no_zt_metrics)} Non-ZT')

zt_m = all_data['zt']
no_m = all_data['no_zt']

print()
print('=' * 85)
print('  连板前兆分析：突破前10天行为对比')
print('=' * 85)
print(f'  连板组: {len(zt_m)}笔 | 非连板组: {len(no_m)}笔')
print()

summary_keys = [
    ('vol_slope_norm', '成交量趋势(斜率%%)', ''),
    ('price_slope_norm', '股价趋势(斜率%%)', ''),
    ('avg_abs_chg_10d', '日均波动%', ''),
    ('sum_chg_10d', '10日累计涨幅%', ''),
    ('avg_amp_10d', '日均振幅%', ''),
    ('max_amp_10d', '最大单日振幅%', ''),
    ('avg_amt_10d', '均成交额(亿)', ''),
    ('amt_slope_norm', '成交额趋势(斜率%%)', ''),
    ('vol_expansion', '量能扩张比(近3/前7)', ''),
    ('price_compression', '价格压缩比(近5/前5)', ''),
    ('accumulation_signal', '蓄势吸筹信号率', '量升价平'),
    ('pos_days_10d', '阳线天数(共10日)', ''),
    ('max_chg_10d', '最大单日涨幅%', ''),
    ('min_chg_10d', '最大单日跌幅%', '(越小越好)'),
    ('close_pos_10d', '价格区间位置(0-1)', '0=低 1=高'),
]

print(f'  {"指标":<26s} {"连板组":>10s} {"非连板组":>10s} {"差异":>8s} {"T值":>7s} {"显著":>4s}')
print(f'  {"-"*70}')
sig_results = []
for key, name, note in summary_keys:
    zt_vals = np.array([m[key] for m in zt_m])
    no_vals = np.array([m[key] for m in no_m])
    zt_mean = np.mean(zt_vals)
    no_mean = np.mean(no_vals)
    diff = zt_mean - no_mean
    try:
        t_stat, p_val = stats.ttest_ind(zt_vals, no_vals)
        sig = '***' if p_val < 0.01 else ('**' if p_val < 0.05 else ('*' if p_val < 0.1 else ''))
    except:
        t_stat, p_val, sig = 0, 1, ''
    is_sig = p_val < 0.1
    if is_sig:
        sig_results.append((name, zt_mean, no_mean, diff, p_val))
    marker = '⭐' if is_sig else '  '
    print(f'{marker} {name:<24s} {zt_mean:>10.4f} {no_mean:>10.4f} {diff:>+8.4f} {t_stat:>+7.2f} {sig:>4s}')

print()
print('=' * 60)
print('  统计显著的差异特征 (p<0.1)')
print('=' * 60)
for name, zt_mean, no_mean, diff, p_val in sorted(sig_results, key=lambda x: x[4]):
    direction = '更高' if diff > 0 else '更低'
    print(f'  {name}: 连板组{direction} ({zt_mean:.4f} vs {no_mean:.4f}), p={p_val:.4f}')

# === DAY-BY-DAY ANALYSIS ===
print()
print('=' * 85)
print('  逐日行为对比 (D-10 = 突破前第10天, D-1 = 突破前1天)')
print('=' * 85)

print()
print('  --- 逐日量能 (相对各组均值归一化) ---')
print(f'  {"Day":<8s} {"连板组":>10s} {"非连板组":>10s} {"差异%":>8s} {"图形":>30s}')
print(f'  {"-"*70}')
zt_vol_mean = np.mean([m['vol_mean_10d'] for m in zt_m])
no_vol_mean = np.mean([m['vol_mean_10d'] for m in no_m])
for i in range(10):
    day_label = f'D{i-10}'
    key = f'vol_d{i-10}'
    zt_v = np.mean([m[key] for m in zt_m]) / zt_vol_mean
    no_v = np.mean([m[key] for m in no_m]) / no_vol_mean
    diff_pct = (zt_v - no_v) * 100
    bar = ('🟢' * int(zt_v * 20) if zt_v > no_v else '🔴' * int(no_v * 20))[:30]
    print(f'  {day_label:<8s} {zt_v:>10.4f} {no_v:>10.4f} {diff_pct:>+7.2f}%  {bar}')

print()
print('  --- 逐日涨跌幅% ---')
print(f'  {"Day":<8s} {"连板组":>10s} {"非连板组":>10s} {"差异":>8s}')
print(f'  {"-"*40}')
for i in range(10):
    day_label = f'D{i-10}'
    key = f'chg_d{i-10}'
    zt_c = np.mean([m[key] for m in zt_m])
    no_c = np.mean([m[key] for m in no_m])
    print(f'  {day_label:<8s} {zt_c:>+10.4f} {no_c:>+10.4f} {zt_c-no_c:>+8.4f}')

print()
print('  --- 逐日振幅% ---')
print(f'  {"Day":<8s} {"连板组":>10s} {"非连板组":>10s} {"差异":>8s}')
print(f'  {"-"*40}')
for i in range(10):
    day_label = f'D{i-10}'
    key = f'amp_d{i-10}'
    zt_a = np.mean([m[key] for m in zt_m])
    no_a = np.mean([m[key] for m in no_m])
    print(f'  {day_label:<8s} {zt_a:>10.4f} {no_a:>10.4f} {zt_a-no_a:>+8.4f}')

# === ACCUMULATION PATTERN DETECTION ===
print()
print('=' * 85)
print('  蓄势吸筹模式：量升价平 (连续N天量增但价不涨)')
print('=' * 85)

def detect_accumulation(metrics, vol_threshold=0.02, price_threshold=0.003):
    """Detect: last 3 days volume rising but price flat"""
    vols = [metrics[f'vol_d{i-10}'] for i in range(7, 10)]  # D-3, D-2, D-1
    closes = [metrics[f'chg_d{i-10}'] for i in range(7, 10)]
    vol_up = all(vols[i] > vols[i-1] for i in range(1, len(vols)))
    price_flat = all(abs(c) < 1.5 for c in closes)
    return 1 if (vol_up and price_flat) else 0

for label, group, metrics_list in [('连板组', 'ZT', zt_m), ('非连板组', 'NO', no_m)]:
    acc_count = sum(detect_accumulation(m) for m in metrics_list)
    print(f'  {label}: {acc_count}/{len(metrics_list)} ({acc_count/len(metrics_list)*100:.1f}%) 个股出现蓄势吸筹')

# Broader: last 5 days any 3 consecutive days of vol up + price flat
def detect_any_accumulation(metrics):
    for start in range(6):  # check days -10 to -4 as start
        vols = [metrics[f'vol_d{start+i-10}'] for i in range(4)]
        chgs = [metrics[f'chg_d{start+i-10}'] for i in range(3)]
        vol_rising = all(vols[i] < vols[i+1] for i in range(3))
        price_flat = all(abs(c) < 1.5 for c in chgs)
        if vol_rising and price_flat:
            return 1
    return 0

print()
print('  --- 更宽泛检测：10日内任意连续3天量增+价稳 ---')
for label, metrics_list in [('连板组', zt_m), ('非连板组', no_m)]:
    acc = sum(detect_any_accumulation(m) for m in metrics_list)
    print(f'  {label}: {acc}/{len(metrics_list)} ({acc/len(metrics_list)*100:.1f}%)')

# === VOLUME SPIKE BEFORE BREAKOUT ===
print()
print('=' * 85)
print('  突破前1-2天异常放量检测')
print('=' * 85)
for label, metrics_list in [('连板组', zt_m), ('非连板组', no_m)]:
    # D-2 volume > 1.5x avg of D-10 to D-3
    spike_count = 0
    for m in metrics_list:
        d2_vol = m['vol_d-2']
        avg_prev = np.mean([m[f'vol_d{i-10}'] for i in range(0, 8)])  # D-10 to D-3
        if avg_prev > 0 and d2_vol / avg_prev > 1.5:
            spike_count += 1
    print(f'  {label}: D-2异常放量(>1.5x前8日均量): {spike_count}/{len(metrics_list)} ({spike_count/len(metrics_list)*100:.1f}%)')

# === PRICE POSITION IN RANGE ===
print()
print('=' * 85)
print('  突破前价格位置分布')
print('=' * 85)
for label, metrics_list in [('连板组', zt_m), ('非连板组', no_m)]:
    positions = [m['close_pos_10d'] for m in metrics_list]
    low_pos = sum(1 for p in positions if p < 0.33)
    mid_pos = sum(1 for p in positions if 0.33 <= p < 0.67)
    high_pos = sum(1 for p in positions if p >= 0.67)
    n = len(positions)
    print(f'  {label}: 低位(<33%) {low_pos}/{n} ({low_pos/n*100:.1f}%) | 中位 {mid_pos}/{n} ({mid_pos/n*100:.1f}%) | 高位(>67%) {high_pos}/{n} ({high_pos/n*100:.1f}%)')
