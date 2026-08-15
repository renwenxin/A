"""第二阶段：LightGBM — vs Logistic对比 + Feature Importance + SHAP"""
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, brier_score_loss
import warnings
warnings.filterwarnings('ignore')

try:
    import lightgbm as lgb
    HAS_LGB = True
except:
    HAS_LGB = False
    print('LightGBM not installed. pip install lightgbm')

try:
    import shap
    HAS_SHAP = True
except:
    HAS_SHAP = False
    print('SHAP not installed. pip install shap')

# Load data
df = pd.read_csv('D:/cursor/project/ashare_review/data/feature_store.csv', encoding='utf-8-sig')
META = ['trade_id','代码','信号日','买入日','卖出日']
LABELS = ['label_3d_return','label_return_gt_5pct','label_return_gt_0pct',
          'label_is_zt','label_max_return','label_max_drawdown','label_day1_return']

feature_cols = [c for c in df.columns if c not in META + LABELS]
feature_cols = [c for c in feature_cols if df[c].dtype in (float, int)]
for col in feature_cols:
    df[col] = df[col].fillna(df[col].median())

X_all = df[feature_cols].values.astype(float)
n = len(df)
split = int(n * 0.7)

X_train, X_test = X_all[:split], X_all[split:]
df_train, df_test = df.iloc[:split], df.iloc[split:]

print(f'Train: {split} | Test: {n-split}')
print(f'Train period: {df.iloc[0]["信号日"]} ~ {df.iloc[split-1]["信号日"]}')
print(f'Test period:  {df.iloc[split]["信号日"]} ~ {df.iloc[n-1]["信号日"]}')

# Drop data-leak features
leak_features = ['is_zt', '持有天数']  # is_zt = label itself; 持有天数 = post-trade info
feature_cols = [c for c in feature_cols if c not in leak_features]
print(f'Train period: {df.iloc[0]["信号日"]} ~ {df.iloc[split-1]["信号日"]}')
print(f'Test period:  {df.iloc[split]["信号日"]} ~ {df.iloc[n-1]["信号日"]}')

# ============================================================
# LightGBM on 3 labels
# ============================================================
labels = [
    ('label_return_gt_5pct', '3日收益>5%'),
    ('label_return_gt_0pct', '3日收益>0%'),
    ('label_is_zt', '是否连板'),
]

target_label = 'label_return_gt_5pct'  # Primary target

print()
print('=' * 75)
print('  LightGBM vs Logistic 对比')
print('=' * 75)

for label_key, label_name in labels:
    y_train = df_train[label_key].values
    y_test = df_test[label_key].values

    # LightGBM
    if HAS_LGB:
        dtrain = lgb.Dataset(X_train, label=y_train)
        params = {
            'objective': 'binary', 'metric': 'auc',
            'boosting_type': 'gbdt', 'num_leaves': 31,
            'learning_rate': 0.05, 'feature_fraction': 0.8,
            'bagging_fraction': 0.8, 'bagging_freq': 5,
            'verbose': -1, 'min_data_in_leaf': 20,
        }
        lgbm = lgb.train(params, dtrain, num_boost_round=200)
        y_pred_lgb = lgbm.predict(X_test)
        auc_lgb = roc_auc_score(y_test, y_pred_lgb)
        brier_lgb = brier_score_loss(y_test, y_pred_lgb)

        # Feature importance
        imp = lgbm.feature_importance(importance_type='gain')
        imp_list = sorted(zip(feature_cols, imp), key=lambda x: x[1], reverse=True)[:15]
    else:
        auc_lgb = 0
        imp_list = []

    # Logistic
    from sklearn.linear_model import LogisticRegression
    lr = LogisticRegression(max_iter=5000, C=1e10)
    lr.fit(X_train, y_train)
    y_pred_lr = lr.predict_proba(X_test)[:, 1]
    auc_lr = roc_auc_score(y_test, y_pred_lr)
    brier_lr = brier_score_loss(y_test, y_pred_lr)

    print(f'\n  --- {label_name} (正样本 {y_train.mean()*100:.1f}%) ---')
    print(f'  {"模型":<15s} {"AUC":>8s} {"Brier":>8s}')
    print(f'  {"-"*33}')
    print(f'  {"Logistic":<15s} {auc_lr:>8.4f} {brier_lr:>8.4f}')
    if HAS_LGB:
        print(f'  {"LightGBM":<15s} {auc_lgb:>8.4f} {brier_lgb:>8.4f}')
        delta = (auc_lgb - auc_lr) / auc_lr * 100
        print(f'  {"提升":<15s} {delta:>+7.1f}%')

    if imp_list:
        print(f'\n  LightGBM Top 15 特征 (按 Gain):')
        print(f'  {"特征":<30s} {"Gain":>10s}')
        for name, gain in imp_list:
            print(f'  {name:<30s} {gain:>10.0f}')

# ============================================================
# Top-N comparison: Logistic vs LightGBM
# ============================================================
print()
print('=' * 75)
print('  Top-N 排序能力对比: Logistic vs LightGBM')
print('=' * 75)

y_train = df_train[target_label].values
y_test = df_test[target_label].values
test_returns = df_test['label_3d_return'].values

# Logistic predictions
lr = LogisticRegression(max_iter=5000, C=1e10)
lr.fit(X_train, y_train)
lr_pred = lr.predict_proba(X_test)[:, 1]

# LightGBM predictions
if HAS_LGB:
    dtrain = lgb.Dataset(X_train, label=y_train)
    lgbm = lgb.train(
        {'objective': 'binary', 'metric': 'auc', 'boosting_type': 'gbdt',
         'num_leaves': 31, 'learning_rate': 0.05, 'feature_fraction': 0.8,
         'verbose': -1, 'min_data_in_leaf': 20},
        dtrain, num_boost_round=200)
    lgb_pred = lgbm.predict(X_test)
else:
    lgb_pred = lr_pred

print(f'\n  目标: {target_label} (预测\"3日收益>5%\")')
print(f'  {"Top N":<10s} {"Log 均收益":>11s} {"Log 赚>5%":>10s} {"LGB 均收益":>11s} {"LGB 赚>5%":>10s}')
print(f'  {"-"*55}')

for top_n in [10, 20, 30, 50, 100]:
    lr_idx = np.argsort(lr_pred)[::-1][:top_n]
    lgb_idx = np.argsort(lgb_pred)[::-1][:top_n]

    lr_ret = np.mean(test_returns[lr_idx]) * 100
    lr_hit = np.mean(test_returns[lr_idx] > 0.05) * 100
    lgb_ret = np.mean(test_returns[lgb_idx]) * 100
    lgb_hit = np.mean(test_returns[lgb_idx] > 0.05) * 100

    best_ret = '⭐' if lgb_ret > lr_ret else ''
    print(f'  {"Top "+str(top_n):<10s} {lr_ret:>+10.2f}% {lr_hit:>9.1f}% {lgb_ret:>+10.2f}% {lgb_hit:>9.1f}% {best_ret}')

base_ret = np.mean(test_returns) * 100
base_hit = np.mean(test_returns > 0.05) * 100
print(f'  {"全部基准":<10s} {base_ret:>+10.2f}% {base_hit:>9.1f}%')

# ============================================================
# SHAP (if available, on a small sample)
# ============================================================
if HAS_LGB and HAS_SHAP:
    print()
    print('=' * 75)
    print('  SHAP 分析 (Top 10 特征)')
    print('=' * 75)
    # Use a subset for SHAP performance
    shap_sample = min(200, len(X_test))
    explainer = shap.TreeExplainer(lgbm)
    shap_values = explainer.shap_values(X_test[:shap_sample])

    # Mean |SHAP| importance
    shap_imp = np.abs(shap_values).mean(axis=0)
    shap_list = sorted(zip(feature_cols, shap_imp), key=lambda x: x[1], reverse=True)[:10]

    print(f'  {"特征":<30s} {"Mean|SHAP|":>12s}')
    print(f'  {"-"*44}')
    for name, val in shap_list:
        print(f'  {name:<30s} {val:>12.6f}')

    # Direction: mean SHAP value
    shap_mean = shap_values.mean(axis=0)
    print(f'\n  {"特征":<30s} {"Mean SHAP":>10s} {"方向":>6s}')
    for name, val in sorted(zip(feature_cols, shap_mean), key=lambda x: abs(x[1]), reverse=True)[:10]:
        direction = '+' if val > 0 else '-'
        print(f'  {name:<30s} {val:>+10.4f} {direction:>6s}')

print()
print('Done — LightGBM分析完成')
