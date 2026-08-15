"""部署：清理泄漏 → 训练干净Logistic → 保存模型 → 更新V2页面"""
import json, pickle, numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

# ===== 1. Load & Clean =====
df = pd.read_csv('D:/cursor/project/ashare_review/data/feature_store.csv', encoding='utf-8-sig')

# Remove leak columns
LEAKS = ['is_zt', '持有天数', 'key']
META = ['trade_id', '代码', '信号日', '买入日', '卖出日']
LABELS = ['label_3d_return', 'label_return_gt_5pct', 'label_return_gt_0pct',
          'label_is_zt', 'label_max_return', 'label_max_drawdown', 'label_day1_return']

features = [c for c in df.columns if c not in META + LABELS + LEAKS]
features = [c for c in features if df[c].dtype in (float, int)]
print(f'Clean features: {len(features)}')

for col in features:
    df[col] = df[col].fillna(df[col].median())

X = df[features].values.astype(float)
target = 'label_return_gt_5pct'
y = df[target].values

# Time split: 70% train, 30% test
n = len(df)
split = int(n * 0.7)

scaler = StandardScaler()
X_train = scaler.fit_transform(X[:split])
X_test = scaler.transform(X[split:])
y_train = y[:split]
y_test = y[split:]

# ===== 2. Train =====
lr = LogisticRegression(max_iter=5000, C=1e10)
lr.fit(X_train, y_train)

y_pred = lr.predict_proba(X_test)[:, 1]
auc = roc_auc_score(y_test, y_pred)
print(f'\nAUC: {auc:.4f} (样本外 {len(y_test)}笔)')
print(f'Train: {split}笔 ({df.iloc[0]["信号日"]}~{df.iloc[split-1]["信号日"]})')
print(f'Test:  {n-split}笔 ({df.iloc[split]["信号日"]}~{df.iloc[n-1]["信号日"]})')

# Top-N on test set
test_returns = df['label_3d_return'].values[split:]
sorted_idx = np.argsort(y_pred)[::-1]

print(f'\n{"Top N":<10s} {"均3日收益":>10s} {"赚>5%率":>9s} {"赚>0%率":>9s}')
print(f'{"-"*40}')
for top_n in [5, 10, 20, 30, 50]:
    idx = sorted_idx[:top_n]
    rets = test_returns[idx]
    print(f'{"Top "+str(top_n):<10s} {np.mean(rets)*100:>+9.2f}% {np.mean(rets>0.05)*100:>8.0f}% {np.mean(rets>0)*100:>8.0f}%')
print(f'{"全部基准":<10s} {np.mean(test_returns)*100:>+9.2f}% {np.mean(test_returns>0.05)*100:>8.0f}% {np.mean(test_returns>0)*100:>8.0f}%')

# ===== 3. OR Table =====
odds = np.exp(lr.coef_[0])
print(f'\n{"特征":<30s} {"OR":>8s} {"方向":>6s}')
print(f'{"-"*46}')
for name, or_val in sorted(zip(features, odds), key=lambda x: abs(np.log(x[1])), reverse=True)[:15]:
    direction = '⬆' if or_val > 1 else '⬇'
    print(f'  {name:<30s} {or_val:>8.4f} {direction:>6s}')

# ===== 4. Save model =====
model_data = {
    'scaler': scaler,
    'model': lr,
    'features': features,
    'auc': auc,
    'target': target,
    'train_dates': (df.iloc[0]['信号日'], df.iloc[split-1]['信号日']),
    'test_dates': (df.iloc[split]['信号日'], df.iloc[n-1]['信号日']),
}
model_path = 'D:/cursor/project/ashare_review/data/logistic_model.pkl'
with open(model_path, 'wb') as f:
    pickle.dump(model_data, f)
print(f'\nModel saved: {model_path}')

# ===== 5. Test prediction on a sample =====
print('\n--- Sample Prediction ---')
sample_idx = np.argsort(y_pred)[-5:][::-1]
for i, idx in enumerate(sample_idx):
    code = df.iloc[split + idx]['代码']
    prob = y_pred[idx]
    actual_ret = test_returns[idx] * 100
    print(f'  #{i+1} {code}: P(>5%)={prob*100:.1f}% | 实际收益 {actual_ret:+.2f}%')
