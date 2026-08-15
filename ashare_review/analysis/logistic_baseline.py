"""第一阶段：Logistic回归基线 — 三标签 OR + VIF + 校准 + 时间分割验证"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, precision_recall_curve, brier_score_loss
from sklearn.calibration import calibration_curve
from statsmodels.stats.outliers_influence import variance_inflation_factor

# Load Feature Store
df = pd.read_csv('D:/cursor/project/ashare_review/data/feature_store.csv', encoding='utf-8-sig')
print(f'Feature Store: {len(df)} rows x {len(df.columns)} cols')

# Identify feature columns (exclude meta + labels)
META = ['trade_id','代码','信号日','买入日','卖出日']
LABELS = ['label_3d_return','label_return_gt_5pct','label_return_gt_0pct',
          'label_is_zt','label_max_return','label_max_drawdown','label_day1_return']

feature_cols = [c for c in df.columns if c not in META + LABELS]
# Remove non-numeric
feature_cols = [c for c in feature_cols if df[c].dtype in (float, int)]
print(f'Features: {len(feature_cols)} | Labels: {len(LABELS)}')

# Fill NaN with median
for col in feature_cols:
    df[col] = df[col].fillna(df[col].median())

X_all = df[feature_cols].values.astype(float)
# Normalize
scaler = StandardScaler()
X_all_scaled = scaler.fit_transform(X_all)

print()
print('=' * 75)
print('  一、VIF 多重共线性分析 (删除 VIF > 10 的特征)')
print('=' * 75)

vif_data = []
for i, col in enumerate(feature_cols):
    try:
        vif = variance_inflation_factor(X_all_scaled, i)
        vif_data.append((col, vif))
    except:
        vif_data.append((col, 999))

vif_data.sort(key=lambda x: x[1], reverse=True)
print(f'  {"特征":<30s} {"VIF":>8s} {"状态":>10s}')
print(f'  {"-"*50}')
low_vif_features = []
for name, vif in vif_data:
    status = 'KEEP' if vif < 10 else '⚠ DROP'
    if vif < 10:
        low_vif_features.append(name)
    print(f'  {name:<30s} {vif:>8.1f} {status:>10s}')

print(f'\n  VIF<10 保留: {len(low_vif_features)} 个特征')

# Use low-VIF features
keep_idx = [feature_cols.index(c) for c in low_vif_features]
X = X_all[:, keep_idx]

# Time-split: first 70% train, last 30% test
n = len(df)
split = int(n * 0.7)
X_train, X_test = X[:split], X[split:]
df_train, df_test = df.iloc[:split], df.iloc[split:]
print(f'\n  时间分割: Train {split}笔 ({df.iloc[0]["信号日"]}~{df.iloc[split-1]["信号日"]}), Test {n-split}笔 ({df.iloc[split]["信号日"]}~{df.iloc[n-1]["信号日"]})')

# ============================================================
# Logistic Regression on 3 labels
# ============================================================
print()
print('=' * 75)
print('  二、Logistic 回归 — 三标签 OR 对比')
print('=' * 75)

labels = [
    ('label_return_gt_5pct', '3日收益>5%'),
    ('label_return_gt_0pct', '3日收益>0%'),
    ('label_is_zt', '是否连板'),
]

all_odds = {}
for label_key, label_name in labels:
    y_train = df_train[label_key].values
    y_test = df_test[label_key].values

    lr = LogisticRegression(max_iter=5000, C=1e10)
    lr.fit(X_train, y_train)

    y_pred = lr.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, y_pred)
    brier = brier_score_loss(y_test, y_pred)

    # OR
    odds = np.exp(lr.coef_[0])
    all_odds[label_key] = list(zip(low_vif_features, odds))

    pos_rate = y_train.mean() * 100
    print(f'\n  --- {label_name} (正样本 {pos_rate:.1f}%) ---')
    print(f'  AUC: {auc:.3f} | Brier: {brier:.4f}')
    print(f'  {"特征":<30s} {"OR":>8s} {"方向":>6s}')
    print(f'  {"-"*48}')
    for name, or_val in sorted(zip(low_vif_features, odds), key=lambda x: abs(np.log(x[1])), reverse=True)[:12]:
        direction = '⬆' if or_val > 1 else '⬇'
        print(f'  {name:<30s} {or_val:>8.4f} {direction:>6s}')

# ============================================================
# Compare: 赚钱 vs 连板 — same logic?
# ============================================================
print()
print('=' * 75)
print('  三、赚钱 vs 连板：是不是同一套逻辑？')
print('=' * 75)

odds_5pct = dict(all_odds['label_return_gt_5pct'])
odds_0pct = dict(all_odds['label_return_gt_0pct'])
odds_zt = dict(all_odds['label_is_zt'])

print(f'  {"特征":<28s} {"赚>5% OR":>9s} {"赚>0% OR":>9s} {"连板 OR":>9s} {"一致性":>8s}')
print(f'  {"-"*65}')
for name in low_vif_features[:15]:
    o5 = odds_5pct.get(name, 1)
    o0 = odds_0pct.get(name, 1)
    oz = odds_zt.get(name, 1)
    # Check if all point in same direction
    same = '✅' if (o5>1 and o0>1 and oz>1) or (o5<1 and o0<1 and oz<1) else '⚠️ 分歧'
    print(f'  {name:<28s} {o5:>9.4f} {o0:>9.4f} {oz:>9.4f} {same:>8s}')

# ============================================================
# Calibration
# ============================================================
print()
print('=' * 75)
print('  四、校准曲线 (Calibration)')
print('=' * 75)

for label_key, label_name in labels:
    y_train = df_train[label_key].values
    y_test = df_test[label_key].values

    lr = LogisticRegression(max_iter=5000, C=1e10)
    lr.fit(X_train, y_train)
    y_pred = lr.predict_proba(X_test)[:, 1]

    prob_true, prob_pred = calibration_curve(y_test, y_pred, n_bins=5, strategy='quantile')
    print(f'\n  {label_name}:')
    print(f'  {"预测概率":>12s} → {"实际比例":>12s} {"偏差":>8s}')
    for pt, pp in zip(prob_pred, prob_true):
        print(f'  {pt:>11.1%} → {pp:>11.1%} {(pp-pt)*100:>+7.1f}%')

# ============================================================
# Top-N performance
# ============================================================
print()
print('=' * 75)
print('  五、Top-N 排序能力 (模型选前N只的均收益)')
print('=' * 75)

for label_key, label_name in labels:
    y_train = df_train[label_key].values
    y_test = df_test[label_key].values

    lr = LogisticRegression(max_iter=5000, C=1e10)
    lr.fit(X_train, y_train)
    y_pred = lr.predict_proba(X_test)[:, 1]

    # Sort test set by predicted probability
    sorted_idx = np.argsort(y_pred)[::-1]
    test_returns = df_test['label_3d_return'].values

    print(f'\n  --- {label_name} ---')
    print(f'  {"Top N":<10s} {"笔数":>5s} {"均3日收益":>10s} {"胜率(>0%)":>9s} {"赚>5%率":>9s}')
    for top_n in [10, 20, 30, 50, 100, len(test_returns)]:
        idx = sorted_idx[:top_n]
        rets = test_returns[idx]
        print(f'  {"Top "+str(top_n):<10s} {len(rets):>5d} {np.mean(rets)*100:>+9.2f}% {np.mean(rets>0)*100:>8.1f}% {np.mean(rets>0.05)*100:>8.1f}%')

    # Baseline: random selection
    base_rets = test_returns
    print(f'  {"全部(基准)":<10s} {len(base_rets):>5d} {np.mean(base_rets)*100:>+9.2f}% {np.mean(base_rets>0)*100:>8.1f}% {np.mean(base_rets>0.05)*100:>8.1f}%')

print()
print('Done — Logistic基线建立完成')
