"""Gold Dataset + 因子质量评估：分位数分析 + IC + 横截面排名 + 实验日志"""
import numpy as np
import pandas as pd
import json, os, pickle
from datetime import datetime
from scipy.stats import spearmanr
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.preprocessing import StandardScaler

# ============================================================
# 1. GOLD DATASET — 彻底清理
# ============================================================
df = pd.read_csv('D:/cursor/project/ashare_review/data/feature_store.csv', encoding='utf-8-sig')

# Feature audit
FEATURE_AUDIT = {
    'is_zt': 'LEAK=label', '持有天数': 'LEAK=post_trade', 'key': 'DROP=key',
    'trade_id': 'META', '代码': 'META', '信号日': 'META', '买入日': 'META', '卖出日': 'META',
    'label_3d_return': 'LABEL', 'label_return_gt_5pct': 'LABEL', 'label_return_gt_0pct': 'LABEL',
    'label_is_zt': 'LABEL', 'label_max_return': 'LABEL', 'label_max_drawdown': 'LABEL',
    'label_day1_return': 'LABEL',
}

CLEAN_FEATURES = []
for col in df.columns:
    if col in FEATURE_AUDIT:
        continue  # Meta, labels, or known leaks
    if col not in FEATURE_AUDIT and df[col].dtype in (float, int):
        CLEAN_FEATURES.append(col)

print(f'Gold Dataset: {len(df)} rows, {len(CLEAN_FEATURES)} clean features')
print(f'Removed: is_zt (label leak), 持有天数 (post-trade), key (key)')
print(f'Labels: 3d_return, return>5%, return>0%, is_zt, max_return, max_dd, day1_return')

# Fill NaN
for col in CLEAN_FEATURES:
    df[col] = df[col].fillna(df[col].median())

# ============================================================
# 2. DECILE ANALYSIS — 每个因子的分位数收益
# ============================================================
print()
print('=' * 85)
print('  分位数分析：每个因子 Top 10% vs Bottom 10% 的收益差')
print('=' * 85)

target = 'label_3d_return'
target_binary = 'label_return_gt_5pct'
y_full = df[target].values
y_bin_full = df[target_binary].values

decile_results = []
for feat in CLEAN_FEATURES:
    x = df[feat].values
    # Decile bins
    try:
        deciles = pd.qcut(x, q=10, labels=False, duplicates='drop')
    except:
        continue

    top_decile = y_full[deciles == deciles.max()]
    bot_decile = y_full[deciles == deciles.min()]
    top_bin = y_bin_full[deciles == deciles.max()]
    bot_bin = y_bin_full[deciles == deciles.min()]

    spread = np.mean(top_decile) - np.mean(bot_decile)
    # Monotonicity: Spearman between decile rank and mean return
    decile_means = [np.mean(y_full[deciles == d]) for d in range(10) if np.sum(deciles == d) > 0]
    if len(decile_means) >= 5:
        mono_r, _ = spearmanr(range(len(decile_means)), decile_means)
    else:
        mono_r = 0

    decile_results.append({
        'feature': feat,
        'spread': abs(spread) * 100,  # absolute spread in %
        'spread_raw': spread * 100,
        'monotonicity': mono_r,
        'top_mean': np.mean(top_decile) * 100,
        'bot_mean': np.mean(bot_decile) * 100,
        'top_winrate': np.mean(top_bin) * 100,
        'bot_winrate': np.mean(bot_bin) * 100,
    })

decile_results.sort(key=lambda x: x['spread'], reverse=True)

print(f'  {"特征":<30s} {"Spread%":>8s} {"单调性":>7s} {"Top10%收益":>9s} {"Bot10%收益":>9s} {"Top赚>5%":>8s} {"Bot赚>5%":>8s}')
print(f'  {"-"*85}')
for r in decile_results[:20]:
    direction = '✓' if abs(r['monotonicity']) > 0.5 else ''
    print(f'  {r["feature"]:<30s} {r["spread_raw"]:>+7.2f}% {r["monotonicity"]:>+6.2f}{direction:1s} {r["top_mean"]:>+8.2f}% {r["bot_mean"]:>+8.2f}% {r["top_winrate"]:>7.1f}% {r["bot_winrate"]:>7.1f}%')

# ============================================================
# 3. INFORMATION COEFFICIENT (IC) — 横截面相关性
# ============================================================
print()
print('=' * 85)
print('  Information Coefficient (IC): 每个因子与未来收益的 Spearman Rank 相关性')
print('=' * 85)

ic_results = []
for feat in CLEAN_FEATURES:
    x = df[feat].values
    ic_3d, p_3d = spearmanr(x, y_full)
    ic_bin, p_bin = spearmanr(x, y_bin_full)
    ic_results.append({
        'feature': feat,
        'IC_3d': ic_3d,
        'p_3d': p_3d,
        'IC_bin': ic_bin,
        'p_bin': p_bin,
    })

ic_results.sort(key=lambda x: abs(x['IC_3d']), reverse=True)

print(f'  {"特征":<30s} {"IC(3d收益)":>10s} {"p值":>8s} {"IC(>5%)":>8s} {"p值":>8s} {"IC显著?":>8s}')
print(f'  {"-"*75}')
sig_count = 0
for r in ic_results:
    sig_3d = '***' if r['p_3d'] < 0.01 else ('**' if r['p_3d'] < 0.05 else ('*' if r['p_3d'] < 0.1 else ''))
    sig_bin = '***' if r['p_bin'] < 0.01 else ('**' if r['p_bin'] < 0.05 else ('*' if r['p_bin'] < 0.1 else ''))
    if sig_3d or sig_bin: sig_count += 1
    print(f'  {r["feature"]:<30s} {r["IC_3d"]:>+10.4f} {r["p_3d"]:>8.4f} {r["IC_bin"]:>+8.4f} {r["p_bin"]:>8.4f} {sig_3d+"/"+sig_bin:>8s}')

print(f'\n  {sig_count}/{len(ic_results)} 个因子的 IC 在统计上显著 (p<0.1)')

# ============================================================
# 4. CROSS-SECTIONAL REGRESSION — 预测连续收益
# ============================================================
print()
print('=' * 85)
print('  横截面回归: 预测 3日收益率（回归） vs 预测 >5%（分类）')
print('=' * 85)

X = df[CLEAN_FEATURES].values.astype(float)
n = len(df)
split = int(n * 0.7)

scaler = StandardScaler()
X_train = scaler.fit_transform(X[:split])
X_test = scaler.transform(X[split:])

# Regression
reg = LinearRegression()
reg.fit(X_train, df[target].values[:split])
y_reg_pred = reg.predict(X_test)

# Classification
clf = LogisticRegression(max_iter=5000, C=1e10)
clf.fit(X_train, df[target_binary].values[:split])
y_clf_pred = clf.predict_proba(X_test)[:, 1]

test_returns = df[target].values[split:]
test_binary = df[target_binary].values[split:]

print(f'\n  样本外测试: {len(test_returns)}笔')
print(f'  {"方法":<12s} {"Top10均收益":>12s} {"Top20均收益":>12s} {"Top30均收益":>12s} {"Top50均收益":>12s}')
print(f'  {"-"*60}')

for name, preds in [('回归', y_reg_pred), ('分类', y_clf_pred)]:
    sorted_idx = np.argsort(preds)[::-1]
    results = []
    for top_n in [10, 20, 30, 50]:
        idx = sorted_idx[:top_n]
        results.append(f'{np.mean(test_returns[idx])*100:>+11.2f}%')
    print(f'  {name:<12s} {" ".join(results)}')

# Baseline
base_ret = np.mean(test_returns) * 100
print(f'  {"全部基准":<12s} {base_ret:>+11.2f}%')

# ============================================================
# 5. CROSS-SECTIONAL RANKING — 每天候选排序
# ============================================================
print()
print('=' * 85)
print('  横截面排名: 按信号日分组，每天内部排序')
print('=' * 85)

df['信号日'] = df['信号日'].astype(str)
dates = sorted(df['信号日'].unique())
daily_top_returns = []
daily_bot_returns = []
daily_baselines = []

for ds in dates:
    day_mask = df['信号日'] == ds
    day_idx = df[day_mask].index
    if len(day_idx) < 3: continue

    day_X = X[day_idx]
    day_y = df.iloc[day_idx][target].values

    # Rank by a simple composite score
    # Use the top 5 features from decile analysis
    try:
        scores = np.zeros(len(day_X))
        for feat_name, weight in [('pre_price_compression', 1), ('pre_close_pos', 1),
                                    ('is_250d_high', 1), ('max_consec_zt', 0.5), ('lu_250d', 0.5)]:
            if feat_name in CLEAN_FEATURES:
                col_idx = CLEAN_FEATURES.index(feat_name)
                scores += day_X[:, col_idx] * weight
    except:
        continue

    sorted_idx = np.argsort(scores)[::-1]
    top_n = max(1, len(sorted_idx) // 5)  # top 20%
    bot_n = max(1, len(sorted_idx) // 5)

    daily_top_returns.append(np.mean(day_y[sorted_idx[:top_n]]))
    daily_bot_returns.append(np.mean(day_y[sorted_idx[-bot_n:]]))
    daily_baselines.append(np.mean(day_y))

print(f'  交易日数: {len(daily_baselines)} (日均候选 ≥3只)')
print(f'  每日Top20%均收益: {np.mean(daily_top_returns)*100:+.2f}%')
print(f'  每日Bot20%均收益: {np.mean(daily_bot_returns)*100:+.2f}%')
print(f'  每日全量均收益: {np.mean(daily_baselines)*100:+.2f}%')
long_short = np.mean([t - b for t, b in zip(daily_top_returns, daily_bot_returns)]) * 100
print(f'  多空Spread: {long_short:+.2f}%')

# ============================================================
# 6. EXPERIMENT LOG
# ============================================================
experiment = {
    'timestamp': datetime.now().isoformat(),
    'dataset': 'feature_store.csv',
    'version': 'Gold V1',
    'clean_features': len(CLEAN_FEATURES),
    'removed_leaks': ['is_zt (label leak)', '持有天数 (post-trade)'],
    'n_total': len(df),
    'train_split': f'{split} / {n-split} (70/30 time-split)',
    'targets': ['label_3d_return', 'label_return_gt_5pct'],
    'results': {
        'regression_top10_ret': round(np.mean(test_returns[np.argsort(y_reg_pred)[::-1][:10]]) * 100, 2),
        'regression_top20_ret': round(np.mean(test_returns[np.argsort(y_reg_pred)[::-1][:20]]) * 100, 2),
        'classification_top10_ret': round(np.mean(test_returns[np.argsort(y_clf_pred)[::-1][:10]]) * 100, 2),
        'classification_top20_ret': round(np.mean(test_returns[np.argsort(y_clf_pred)[::-1][:20]]) * 100, 2),
        'baseline_ret': round(base_ret, 2),
        'cross_sectional_spread': round(long_short, 2),
        'significant_factors': sig_count,
        'total_factors': len(ic_results),
    },
    'top_factors_by_ic': [(r['feature'], round(r['IC_3d'], 4)) for r in ic_results[:5]],
    'top_factors_by_spread': [(r['feature'], round(r['spread_raw'], 2)) for r in decile_results[:5]],
}

log_path = 'D:/cursor/project/ashare_review/data/experiment_log.json'
existing_logs = []
if os.path.exists(log_path):
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            existing_logs = json.load(f)
    except:
        pass
existing_logs.append(experiment)
with open(log_path, 'w', encoding='utf-8') as f:
    json.dump(existing_logs, f, ensure_ascii=False, indent=2)

print()
print('=' * 85)
print('  实验日志')
print('=' * 85)
print(f'  已保存: {log_path} (共 {len(existing_logs)} 条实验记录)')
print(f'  本次关键指标:')
print(f'    回归 Top10: {experiment["results"]["regression_top10_ret"]:+.2f}%')
print(f'    分类 Top10: {experiment["results"]["classification_top10_ret"]:+.2f}%')
print(f'    横截面Spread: {experiment["results"]["cross_sectional_spread"]:+.2f}%')
print(f'    显著因子: {sig_count}/{len(ic_results)}')

print()
print('Done — Gold Dataset + 因子质量评估完成')
