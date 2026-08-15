"""Alpha Research Framework: 第五类信息 — 相对排名 + 连续性 + 交互特征
不需要新数据，只改变特征表达方式
"""
import numpy as np, pandas as pd
from scipy.stats import spearmanr, rankdata
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler
import json, os
from datetime import datetime

df = pd.read_csv('D:/cursor/project/ashare_review/data/feature_store.csv', encoding='utf-8-sig')

LEAKS = ['is_zt', '持有天数', 'key']
META = ['trade_id', '代码', '信号日', '买入日', '卖出日']
LABELS = ['label_3d_return', 'label_return_gt_5pct', 'label_return_gt_0pct',
          'label_is_zt', 'label_max_return', 'label_max_drawdown', 'label_day1_return']

base_features = [c for c in df.columns if c not in META + LABELS + LEAKS]
base_features = [c for c in base_features if df[c].dtype in (float, int)]
for col in base_features:
    df[col] = df[col].fillna(df[col].median())

target = 'label_3d_return'
target_bin = 'label_return_gt_5pct'
print(f'Base features: {len(base_features)} | Trades: {len(df)}')

# ============================================================
# 1. RELATIVE FEATURES — 每天内部的横截面排名
# ============================================================
print('\n' + '='*65)
print('  相对排名特征 (Rank within day)')
print('='*65)

df['信号日'] = df['信号日'].astype(str)
dates = sorted(df['信号日'].unique())

# For each day, rank each stock among that day's signal pool
rank_features = {}
for feat in base_features:
    rank_features[f'rank_{feat}'] = np.zeros(len(df))

for ds in dates:
    mask = df['信号日'] == ds
    day_idx = df[mask].index
    if len(day_idx) < 3: continue
    for feat in base_features:
        vals = df.loc[day_idx, feat].values
        # Percentile rank (0-1), handle ties
        ranks = rankdata(vals, method='average') / len(vals)
        df.loc[day_idx, f'rank_{feat}'] = ranks

# Evaluate rank features vs original
eval_feats = ['pre_price_compression', 'lu_250d', 'lu_60d', '量比MAVOL180',
              '突破幅度%', 'pre_close_pos', '评分', 'price']

print(f'  {"Feature":<35s} {"原IC":>8s} {"RankIC":>8s} {"提升":>8s}')
print(f'  {"-"*62}')
for feat in eval_feats:
    orig_ic, _ = spearmanr(df[feat].values, df[target].values)
    rank_ic, _ = spearmanr(df[f'rank_{feat}'].values, df[target].values)
    delta = abs(rank_ic) - abs(orig_ic)
    mark = '⭐' if delta > 0.01 else ''
    print(f'  {feat:<35s} {orig_ic:>+8.4f} {rank_ic:>+8.4f} {delta:>+8.4f} {mark}')

# ============================================================
# 2. SEQUENCE FEATURES — 连续性模式
# ============================================================
print('\n' + '='*65)
print('  连续性特征 (Sequence patterns)')
print('='*65)

# Interaction terms from the pre-breakout metrics
# pre_price_slope × pre_price_compression (trend strength × volatility squeeze)
df['seq_vol_divergence'] = df['pre_vol_slope'] * (1 - df['pre_price_slope'].clip(-2, 2) / 2)
df['seq_price_squeeze'] = df['pre_price_compression'] * df['pre_pos_days'] / 10
df['seq_momentum_quality'] = df['pre_sum_chg'] * df['pre_pos_days'] / 10
df['seq_vol_acceleration'] = df['pre_vol_slope'] * df['pre_vol_expansion']

# 3-day vs 7-day volume pattern
df['seq_vol_surge'] = df['pre_vol_expansion'] - 1.0  # how much recent vol exceeds older

seq_feats = ['seq_vol_divergence', 'seq_price_squeeze', 'seq_momentum_quality',
             'seq_vol_acceleration', 'seq_vol_surge']

for feat in seq_feats:
    ic, p = spearmanr(df[feat].values, df[target].values)
    spread = np.mean(df[df[feat] > df[feat].quantile(0.8)][target]) - \
             np.mean(df[df[feat] < df[feat].quantile(0.2)][target])
    sig = '*' if p < 0.1 else ''
    print(f'  {feat:<30s} IC={ic:+.4f} (p={p:.3f}){sig}  Spread={spread*100:+.2f}%')

# ============================================================
# 3. MARKET POSITION — 全市场排名
# ============================================================
print('\n' + '='*65)
print('  市场位置特征 (Market position rank)')
print('='*65)

# Within each day, rank the signal stock's market position
for feat in ['avg_amount_20d_yi', 'lu_250d', 'price', '突破幅度%', '量比MAVOL180']:
    ic, p = spearmanr(df[f'rank_{feat}'].values, df[target].values)
    sig = '*' if p < 0.1 else ''
    print(f'  rank_{feat:<28s} IC={ic:+.4f} (p={p:.3f}){sig}')

# ============================================================
# 4. INTERACTION FEATURES — 因子组合
# ============================================================
print('\n' + '='*65)
print('  交互特征 (Interactions)')
print('='*65)

interactions = []
# Best factor pairs from earlier analysis
pairs = [
    ('pre_price_compression', 'is_250d_high'),
    ('pre_price_compression', 'lu_250d'),
    ('pre_close_pos', 'is_250d_high'),
    ('lu_60d', 'pre_price_compression'),
    ('量比MAVOL180', '突破幅度%'),
    ('pre_price_compression', 'mkt_sh_above_ma60'),
    ('lu_250d', 'pre_close_pos'),
]

for f1, f2 in pairs:
    if f1 in df.columns and f2 in df.columns:
        name = f'{f1}×{f2}'
        df[name] = df[f1] * df[f2]
        interactions.append(name)
        ic, p = spearmanr(df[name].values, df[target].values)
        sig = '*' if p < 0.1 else ''
        print(f'  {name:<45s} IC={ic:+.4f} (p={p:.3f}){sig}')

# ============================================================
# 5. FULL EVALUATION — 新旧特征对比
# ============================================================
print('\n' + '='*65)
print('  新旧特征对比 (样本外)')
print('='*65)

all_rank_feats = [f'rank_{f}' for f in base_features]
all_new_feats = seq_feats + interactions + all_rank_feats

# Clean new features
for col in all_new_feats:
    if col in df.columns:
        df[col] = df[col].fillna(df[col].median())

valid_new = [c for c in all_new_feats if c in df.columns and df[c].std() > 1e-8]
print(f'New features: {len(valid_new)} (rank={len(all_rank_feats)}, seq={len(seq_feats)}, interactions={len(interactions)})')

# Prepare data
X_old = df[base_features].values.astype(float)
X_new = df[base_features + valid_new].values.astype(float)
y_reg = df[target].values

n = len(df)
split = int(n * 0.7)

scaler_old = StandardScaler()
scaler_new = StandardScaler()

X_old_train = scaler_old.fit_transform(X_old[:split])
X_old_test = scaler_old.transform(X_old[split:])
X_new_train = scaler_new.fit_transform(X_new[:split])
X_new_test = scaler_new.transform(X_new[split:])

y_train = y_reg[:split]
y_test = y_reg[split:]

# Old model
lr_old = LinearRegression().fit(X_old_train, y_train)
pred_old = lr_old.predict(X_old_test)

# New model
lr_new = LinearRegression().fit(X_new_train, y_train)
pred_new = lr_new.predict(X_new_test)

print(f'\n  {"Model":<15s} {"Top5":>8s} {"Top10":>8s} {"Top20":>8s} {"Top30":>8s} {"IC":>8s}')
print(f'  {"-"*52}')
for name, preds in [('31 原特征', pred_old), (f'+{len(valid_new)} 新特征', pred_new)]:
    sorted_idx = np.argsort(preds)[::-1]
    results = []
    for top_n in [5, 10, 20, 30]:
        ret = np.mean(y_test[sorted_idx[:top_n]]) * 100
        results.append(f'{ret:>+7.2f}%')
    ic, _ = spearmanr(preds, y_test)
    print(f'  {name:<15s} {" ".join(results)} {ic:>+8.4f}')

base_ret = np.mean(y_test) * 100
print(f'  {"全部基准":<15s} {" ":>8s} {" ":>8s} {" ":>8s} {" ":>8s} (base={base_ret:+.2f}%)')

# ============================================================
# 6. Log experiment
# ============================================================
top10_new = np.mean(y_test[np.argsort(pred_new)[::-1][:10]]) * 100
top10_old = np.mean(y_test[np.argsort(pred_old)[::-1][:10]]) * 100

experiment = {
    'timestamp': datetime.now().isoformat(),
    'name': 'Rank + Sequence + Interaction features',
    'data': 'feature_store.csv',
    'old_features': len(base_features),
    'new_features_added': len(valid_new),
    'new_categories': ['rank_within_day', 'sequence_patterns', 'interactions'],
    'n_total': len(df),
    'split': f'train {split} / test {n-split}',
    'results': {
        'old_top10_ret': round(top10_old, 2),
        'new_top10_ret': round(top10_new, 2),
        'delta_top10': round(top10_new - top10_old, 2),
        'baseline_ret': round(base_ret, 2),
    }
}

log_path = 'D:/cursor/project/ashare_review/data/experiment_log.json'
logs = []
if os.path.exists(log_path):
    with open(log_path, 'r', encoding='utf-8') as f:
        logs = json.load(f)
logs.append(experiment)
with open(log_path, 'w', encoding='utf-8') as f:
    json.dump(logs, f, ensure_ascii=False, indent=2)

print(f'\n  实验 #{len(logs)} 已保存: {log_path}')
print(f'  旧 Top10: {top10_old:+.2f}% → 新 Top10: {top10_new:+.2f}% (Δ={top10_new-top10_old:+.2f}%)')
