"""因子验证：Logistic回归 + 60天涨停分层 + 相关性矩阵 + 增益回测"""
import json, numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

with open('D:/cursor/project/ashare_review/data/three_dim_features.json', 'r', encoding='utf-8') as f:
    raw = json.load(f)

df = pd.DataFrame(raw)
df['is_zt'] = df['is_zt'].astype(int)
for col in df.columns:
    if col in ('code','signal_date','is_zt'): continue
    if df[col].dtype in (float, int):
        df[col] = df[col].fillna(df[col].median())

# Load trade returns
tdf = pd.read_excel('D:/cursor/project/ashare_review/data/vol180_breakout_backtest_250d.xlsx',
                   sheet_name='交易明细', skiprows=1)
tdf.columns = ['序号','信号日','买入日','代码','评分','信号涨幅%','信号价','突破幅度%',
        '量比MAVOL180','压力位','MAVOL180','买入价','卖出日','卖出价',
        '持有天数','净收益%','结果','退出原因','是否连板','卖出日涨跌%','信号理由'][:len(tdf.columns)]
tdf = tdf.iloc[1:].copy()
tdf = tdf.dropna(subset=['代码'])
tdf['净收益%'] = pd.to_numeric(tdf['净收益%'], errors='coerce')
tdf['代码'] = tdf['代码'].astype(str).str.zfill(6)
tdf['信号日'] = pd.to_datetime(tdf['信号日']).dt.strftime('%Y%m%d')
tdf['key'] = tdf['代码'] + '_' + tdf['信号日']
df['key'] = df['code'] + '_' + df['signal_date']

merged = df.merge(tdf[['key','净收益%']], on='key', how='left')
merged['净收益%'] = merged['净收益%'].fillna(0)

print(f'Data: {len(merged)} trades, ZT rate: {merged["is_zt"].mean()*100:.1f}%')

sig_keys = ['pre_price_compression','pre_sum_chg','pre_pos_days','pre_price_slope',
            'pre_close_pos','pre_vol_expansion','pre_vol_slope',
            'is_250d_high','lu_60d','lu_20d','max_consec_zt','mkt_sh_above_ma60']

# ============================================================
# 1. CORRELATION
# ============================================================
print()
print('='*85)
print('  一、因子相关性矩阵 (|r|>0.3)')
print('='*85)
corr = merged[sig_keys].corr()
for i in range(len(sig_keys)):
    for j in range(i+1, len(sig_keys)):
        v = corr.iloc[i, j]
        if abs(v) > 0.3:
            print(f'  {sig_keys[i]:<28s} ↔ {sig_keys[j]:<28s}  r={v:+.3f}')

# Count independent clusters
print(f'\n  -> 12个因子中有 {sum(1 for i in range(len(sig_keys)) for j in range(i+1,len(sig_keys)) if abs(corr.iloc[i,j])>0.5)} 对高度相关(|r|>0.5)')
print(f'  -> 实际独立维度估计: 5-6个')

# ============================================================
# 2. LOGISTIC REGRESSION
# ============================================================
print()
print('='*85)
print('  二、Logistic回归 Odds Ratio (控制所有变量后的独立贡献)')
print('='*85)
X = merged[sig_keys].values
y = merged['is_zt'].values
X_s = StandardScaler().fit_transform(X)
lr = LogisticRegression(max_iter=5000, C=1e10)
lr.fit(X_s, y)

print(f'  {"因子":<30s} {"Coefficient":>10s} {"OddsRatio":>10s} {"方向":>6s}')
print(f'  {"-"*60}')
for name, coef in sorted(zip(sig_keys, lr.coef_[0]), key=lambda x: abs(x[1]), reverse=True):
    odds = np.exp(coef)
    direction = '⬆' if coef > 0 else '⬇'
    print(f'  {name:<30s} {coef:>+10.4f} {odds:>10.4f}   {direction}')

# ============================================================
# 3. 60d ZT stratification
# ============================================================
print()
print('='*85)
print('  三、60天涨停分层 — 找最优区间')
print('='*85)
bins = [(0,1,'沉睡 0-1次'),(2,3,'低频 2-3次'),(4,5,'正常 4-5次'),(6,8,'活跃 6-8次'),(9,20,'高频 9+次')]
print(f'  {"区间":<16s} {"笔数":>6s} {"连板率":>8s} {"均收益%":>9s} {"中位收益%":>9s}')
print(f'  {"-"*52}')
for lo, hi, label in bins:
    sub = merged[(merged['lu_60d'] >= lo) & (merged['lu_60d'] <= hi)]
    if len(sub) < 5: continue
    print(f'  {label:<16s} {len(sub):>6d} {sub["is_zt"].mean()*100:>7.1f}% {sub["净收益%"].mean():>+8.2f}% {sub["净收益%"].median():>+8.2f}%')

# ============================================================
# 4. GAIN VALIDATION
# ============================================================
print()
print('='*85)
print('  四、因子增益验证 — 逐层过滤')
print('='*85)
base_wr = merged['is_zt'].mean() * 100
base_ret = merged['净收益%'].mean()
print(f'  基准(721笔): 连板率{base_wr:.1f}% 均收益{base_ret:+.2f}%')
print()

filters = [
    ('mkt_sh_above_ma60 == 0', '大盘MA60下方'),
    ('is_250d_high == 1', '250日新高'),
    ('pre_price_compression > 1.5', '价格压缩比>1.5'),
    ('pre_sum_chg > 10', '10日累计涨幅>10%'),
    ('lu_60d <= 5', '60天涨停<=5'),
    ('max_consec_zt >= 3', '历史最高连板>=3'),
]
for cond, name in filters:
    sub = merged.query(cond)
    if len(sub) < 20: continue
    wr = sub['is_zt'].mean()*100
    ret = sub['净收益%'].mean()
    kept = len(sub)/len(merged)*100
    print(f'  {name:<18s}: {len(sub):>4d}笔({kept:>4.1f}%) | 连板{wr:.1f}% ({wr-base_wr:+.1f}) | 均{ret:+.2f}%')

# Multi-factor combos
print()
print('  --- 多因子组合 ---')
combos = [
    ('大盘MA60下 + 250新高', 'mkt_sh_above_ma60 == 0 and is_250d_high == 1'),
    ('+ 压缩比>1.5', 'mkt_sh_above_ma60 == 0 and is_250d_high == 1 and pre_price_compression > 1.5'),
    ('+ 60天涨停<=5', 'mkt_sh_above_ma60 == 0 and is_250d_high == 1 and pre_price_compression > 1.5 and lu_60d <= 5'),
    ('+ 10日涨幅>10%', 'mkt_sh_above_ma60 == 0 and is_250d_high == 1 and pre_price_compression > 1.5 and lu_60d <= 5 and pre_sum_chg > 10'),
]
for name, cond in combos:
    sub = merged.query(cond)
    if len(sub) < 5: continue
    wr = sub['is_zt'].mean()*100
    ret = sub['净收益%'].mean()
    print(f'  {name:<22s}: {len(sub):>4d}笔 | 连板率{wr:.1f}% | 均收益{ret:+.2f}%')

# The reverse filter - worst trades
print()
print('  --- 反向验证：最差组合 ---')
worst = merged.query('mkt_sh_above_ma60 == 1 and is_250d_high == 0')
if len(worst) > 5:
    print(f'  大盘MA60上方 + 非新高: {len(worst)}笔 | 连板率{worst["is_zt"].mean()*100:.1f}% | 均收益{worst["净收益%"].mean():+.2f}%')

# ============================================================
# 5. DUAL TARGET: 3-day return vs ZT
# ============================================================
print()
print('='*85)
print('  五、双目标分析：连板不是唯一的成功标准')
print('='*85)
ret_bins = [(-100,-5,'<-5%大亏'),(-5,0,'-5~0%小亏'),(0,5,'0~5%小赚'),
            (5,10,'5~10%中赚'),(10,20,'10~20%大赚'),(20,500,'>20%暴赚')]
for lo, hi, label in ret_bins:
    sub = merged[(merged['净收益%'] >= lo) & (merged['净收益%'] < hi)]
    zt_n = sub['is_zt'].sum()
    print(f'  {label:<14s}: {len(sub):>4d}笔 | 连板{int(zt_n)}笔({zt_n/len(sub)*100:.0f}%) | 均{ sub["净收益%"].mean():+.1f}%')

mid = merged[(merged['净收益%']>=5)&(merged['净收益%']<20)]
print(f'\n  -> 5~20%区间共{len(mid)}笔({len(mid)/len(merged)*100:.1f}%)，连板率仅{mid["is_zt"].mean()*100:.0f}%')
print(f'  -> 这部分\"不连板但赚钱\"的交易，被二分类标签浪费了')

# Compare: "predict 3d return > 5%" vs "predict ZT"
# How many trades have return > 5% but no ZT?
good_no_zt = merged[(merged['净收益%'] >= 5) & (merged['is_zt'] == 0)]
good_with_zt = merged[(merged['净收益%'] >= 5) & (merged['is_zt'] == 1)]
bad_no_zt = merged[(merged['净收益%'] < 0) & (merged['is_zt'] == 0)]
print(f'\n  赚>5%且连板: {len(good_with_zt)}笔')
print(f'  赚>5%但不连板: {len(good_no_zt)}笔 — 这{len(good_no_zt)}笔如果用\"连板\"标签会被判为失败')
print(f'  亏钱但不连板: {len(bad_no_zt)}笔')
print(f'  ZT精确率: {len(good_with_zt)/(len(good_with_zt)+len(bad_no_zt))*100:.1f}% (连板=赚钱)')
print(f'  ZT召回率: {len(good_with_zt)/(len(good_with_zt)+len(good_no_zt))*100:.1f}% (赚钱=连板)')
