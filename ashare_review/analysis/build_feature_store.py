"""Feature Store: 统一特征库 + 多标签
每行=一笔交易，所有特征+所有标签一次生成，后续ML研究只需读这一个CSV
"""
import json, os, sys, time, struct
import numpy as np
import pandas as pd

sys.path.insert(0, '.')
from ashare_review.data.tdx_reader import TdxReader, RECORD_SIZE

DATA_DIR = 'D:/cursor/project/ashare_review/data'
OUT_PATH = os.path.join(DATA_DIR, 'feature_store.csv')

# ===== 1. Load existing features =====
with open(os.path.join(DATA_DIR, 'three_dim_features.json'), 'r', encoding='utf-8') as f:
    feats_data = json.load(f)

df_feats = pd.DataFrame(feats_data)
print(f'Loaded features: {len(df_feats)} rows x {len(df_feats.columns)} cols')

# ===== 2. Load backtest trade details =====
tdf = pd.read_excel(os.path.join(DATA_DIR, 'vol180_breakout_backtest_250d.xlsx'),
                   sheet_name='交易明细', skiprows=1)
tdf.columns = ['序号','信号日','买入日','代码','评分','信号涨幅%','信号价','突破幅度%',
        '量比MAVOL180','压力位','MAVOL180','买入价','卖出日','卖出价',
        '持有天数','净收益%','结果','退出原因','是否连板','卖出日涨跌%','信号理由'][:len(tdf.columns)]
tdf = tdf.iloc[1:].copy()
tdf = tdf.dropna(subset=['代码'])
tdf['净收益%'] = pd.to_numeric(tdf['净收益%'], errors='coerce')
tdf['评分'] = pd.to_numeric(tdf['评分'], errors='coerce')
tdf['持有天数'] = pd.to_numeric(tdf['持有天数'], errors='coerce')
tdf['卖出日涨跌%'] = pd.to_numeric(tdf['卖出日涨跌%'].str.replace('%',''), errors='coerce')
tdf['突破幅度%'] = pd.to_numeric(tdf['突破幅度%'].str.replace('%','').str.replace('+',''), errors='coerce')
tdf['量比MAVOL180'] = pd.to_numeric(tdf['量比MAVOL180'].str.replace('x',''), errors='coerce')
tdf['信号价'] = pd.to_numeric(tdf['信号价'], errors='coerce')
tdf['买入价'] = pd.to_numeric(tdf['买入价'], errors='coerce')
tdf['卖出价'] = pd.to_numeric(tdf['卖出价'], errors='coerce')
tdf['代码'] = tdf['代码'].astype(str).str.zfill(6)
tdf['信号日'] = pd.to_datetime(tdf['信号日']).dt.strftime('%Y%m%d')
tdf['买入日'] = pd.to_datetime(tdf['买入日']).dt.strftime('%Y%m%d')
tdf['卖出日'] = pd.to_datetime(tdf['卖出日']).dt.strftime('%Y%m%d')
tdf['key'] = tdf['代码'] + '_' + tdf['信号日']

print(f'Loaded trades: {len(tdf)} rows')

# ===== 3. Merge features + trade details =====
# Add prefix to feature columns (skip code/signal_date/is_zt/key)
df_feats_renamed = df_feats.copy()
df_feats_renamed['feat_key'] = df_feats_renamed['code'] + '_' + df_feats_renamed['signal_date']
# Rename feature cols with feat_ prefix (except merge key)
for col in df_feats_renamed.columns:
    if col not in ('code','signal_date','feat_key'):
        df_feats_renamed = df_feats_renamed.rename(columns={col: f'feat_{col}'})
# Merge
df = tdf.merge(df_feats_renamed,
               left_on='key', right_on='feat_key', how='left')

# Fill missing numeric features
for col in df.columns:
    if col.startswith('feat_') and df[col].dtype in (float, int):
        df[col] = df[col].fillna(df[col].median() if df[col].notna().any() else 0)

print(f'Merged: {len(df)} rows')

# ===== 4. 多任务标签 =====
df['label_3d_return'] = df['净收益%'] / 100.0          # 回归标签
df['label_return_gt_5pct'] = (df['净收益%'] > 5).astype(int)  # 分类标签1
df['label_return_gt_0pct'] = (df['净收益%'] > 0).astype(int)  # 分类标签2
df['label_is_zt'] = (df['是否连板'] == '是').astype(int)       # 分类标签3
df['label_win_or_loss'] = (df['净收益%'] > 0).astype(int)

# ===== 5. 未来最大浮盈 / 最大回撤 (从TDX读取买入→卖出期间的日线) =====
print('\nExtracting max_return & max_drawdown during holding...')
tdx = TdxReader()

max_returns = []
max_drawdowns = []
day1_returns = []

t0 = time.time()
for idx, (_, row) in enumerate(df.iterrows()):
    if (idx+1) % 100 == 0:
        print(f'  {idx+1}/{len(df)} ({time.time()-t0:.0f}s)...', flush=True)

    code = row['代码']
    buy_date = str(row['买入日'])
    sell_date = str(row['卖出日'])
    buy_price = row['买入价']

    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith(('8','4')): market = 'bj'

    max_ret = 0.0
    max_dd = 0.0
    d1_ret = 0.0

    try:
        df_stock = tdx.read_daily(code, market)
        if df_stock is not None and not df_stock.empty:
            from datetime import datetime
            try:
                buy_dt = datetime.strptime(buy_date, '%Y%m%d').date()
                sell_dt = datetime.strptime(sell_date, '%Y%m%d').date()
            except:
                max_returns.append(0); max_drawdowns.append(0); day1_returns.append(0)
                continue

            # Filter to trading days between buy and sell
            mask = df_stock['trade_date'].apply(
                lambda x: (x.date() if hasattr(x, 'date') else x) > buy_dt
            ) & df_stock['trade_date'].apply(
                lambda x: (x.date() if hasattr(x, 'date') else x) <= sell_dt
            )
            period = df_stock[mask]

            if not period.empty:
                highs = period['high'].values.astype(float)
                lows = period['low'].values.astype(float)
                opens = period['open'].values.astype(float)

                # Max return: highest (high - buy_price) / buy_price
                max_ret = float(np.max((highs - buy_price) / buy_price))
                # Max drawdown: lowest (low - buy_price) / buy_price
                max_dd = float(np.min((lows - buy_price) / buy_price))
                # Day 1 return: first day's close vs buy_price
                if 'close' in period.columns:
                    d1_ret = float((period['close'].iloc[0] - buy_price) / buy_price)
    except:
        pass

    max_returns.append(max_ret)
    max_drawdowns.append(max_dd)
    day1_returns.append(d1_ret)

df['label_max_return'] = max_returns
df['label_max_drawdown'] = max_drawdowns
df['label_day1_return'] = day1_returns

# ===== 6. 构建最终 Feature Store =====
# Select columns: trade_id, metadata, features, labels
# Collect all available feature columns (those with feat_ prefix)
available_features = [c for c in df.columns if c.startswith('feat_')]
print(f'Available features: {len(available_features)}')
FEATURE_COLS = available_features

LABEL_COLS = [
    'label_3d_return',       # 3日净收益率 (回归)
    'label_return_gt_5pct',  # 收益>5% (分类)
    'label_return_gt_0pct',  # 收益>0% (分类)
    'label_is_zt',           # 是否连板 (分类)
    'label_max_return',      # 持有期最大浮盈 (回归)
    'label_max_drawdown',    # 持有期最大回撤 (回归)
    'label_day1_return',     # 买入次日收益
]

META_COLS = ['代码', '信号日', '买入日', '卖出日']

# Clean feature names (remove 'feat_' prefix)
store = df[META_COLS].copy()
for col in FEATURE_COLS:
    # Also add raw trade features
    clean_name = col.replace('feat_', '')
    store[clean_name] = df[col].values if col in df.columns else 0

# Add raw trade metrics as additional features
for raw_col in ['评分', '信号涨幅%', '突破幅度%', '量比MAVOL180', '持有天数']:
    if raw_col in df.columns:
        store[raw_col] = df[raw_col].values

for col in LABEL_COLS:
    store[col] = df[col].values

store.insert(0, 'trade_id', range(1, len(store)+1))

# Save
store.to_csv(OUT_PATH, index=False, encoding='utf-8-sig')
print(f'\nFeature Store saved: {OUT_PATH}')
print(f'  {len(store)} rows x {len(store.columns)} columns')
print(f'  Features: {len(FEATURE_COLS)} | Labels: {len(LABEL_COLS)}')

# ===== 7. Quick stats =====
print()
print('='*65)
print('  Feature Store 统计')
print('='*65)
print(f'  label_3d_return: mean={store["label_3d_return"].mean()*100:+.2f}% median={store["label_3d_return"].median()*100:+.2f}%')
print(f'  label_return_gt_5pct: {store["label_return_gt_5pct"].mean()*100:.1f}% positive')
print(f'  label_return_gt_0pct: {store["label_return_gt_0pct"].mean()*100:.1f}% positive')
print(f'  label_is_zt: {store["label_is_zt"].mean()*100:.1f}% positive')
print(f'  label_max_return: mean={store["label_max_return"].mean()*100:+.2f}% max={store["label_max_return"].max()*100:+.2f}%')
print(f'  label_max_drawdown: mean={store["label_max_drawdown"].mean()*100:+.2f}% min={store["label_max_drawdown"].min()*100:+.2f}%')
print(f'  label_day1_return: mean={store["label_day1_return"].mean()*100:+.2f}%')

# Cross-label analysis
zt = store[store['label_is_zt']==1]
no_zt = store[store['label_is_zt']==0]
print(f'\n  --- 连板组 ---')
print(f'  3日收益: {zt["label_3d_return"].mean()*100:+.2f}% | 最大浮盈: {zt["label_max_return"].mean()*100:+.2f}% | 最大回撤: {zt["label_max_drawdown"].mean()*100:+.2f}%')
print(f'  赚>5%: {zt["label_return_gt_5pct"].mean()*100:.1f}% | 赚>0%: {zt["label_return_gt_0pct"].mean()*100:.1f}%')
print(f'\n  --- 非连板组 ---')
print(f'  3日收益: {no_zt["label_3d_return"].mean()*100:+.2f}% | 最大浮盈: {no_zt["label_max_return"].mean()*100:+.2f}% | 最大回撤: {no_zt["label_max_drawdown"].mean()*100:+.2f}%')
print(f'  赚>5%: {no_zt["label_return_gt_5pct"].mean()*100:.1f}% | 赚>0%: {no_zt["label_return_gt_0pct"].mean()*100:.1f}%')

# "连板 but 亏钱" analysis
zt_lose = store[(store['label_is_zt']==1) & (store['label_3d_return'] < 0)]
print(f'\n  --- 连板但亏钱 ---')
print(f'  数量: {len(zt_lose)}笔 | 均亏损: {zt_lose["label_3d_return"].mean()*100:+.2f}%')
print(f'  最大浮盈曾达: {zt_lose["label_max_return"].mean()*100:+.2f}% ← 存在止盈机会')

# No ZT but >5%
no_zt_win = store[(store['label_is_zt']==0) & (store['label_return_gt_5pct']==1)]
print(f'\n  --- 不连板但赚>5% ---')
print(f'  数量: {len(no_zt_win)}笔 | 均收益: {no_zt_win["label_3d_return"].mean()*100:+.2f}%')
print(f'  最大浮盈: {no_zt_win["label_max_return"].mean()*100:+.2f}%')
