"""Compare ZT vs non-ZT across all 3 dimensions + pre-breakout features"""
import json, numpy as np
from scipy import stats

with open('D:/cursor/project/ashare_review/data/three_dim_features.json', 'r', encoding='utf-8') as f:
    all_data = json.load(f)

zt = [d for d in all_data if d.get('is_zt') == 1]
no = [d for d in all_data if d.get('is_zt') == 0]
print(f'ZT: {len(zt)} | Non-ZT: {len(no)}')

# All numeric keys
numeric_keys = [k for k in all_data[0].keys()
                if k not in ('code', 'signal_date', 'is_zt')
                and isinstance(all_data[0].get(k), (int, float))]

# T-test
sig = []
for key in numeric_keys:
    z = [d.get(key, 0) for d in zt]
    n = [d.get(key, 0) for d in no]
    if np.std(z + n) < 1e-6: continue
    zm = np.mean(z); nm = np.mean(n)
    try:
        t, p = stats.ttest_ind(z, n)
    except: continue
    if p < 0.10:
        sig.append((key, zm, nm, zm - nm, p, t))

sig.sort(key=lambda x: x[4])

print()
print('=' * 85)
print('  全部显著特征 (p<0.1) — 按p值排序')
print('=' * 85)
print(f'  {"特征":<30s} {"连板组":>10s} {"非连板组":>10s} {"差异":>8s} {"p值":>8s} {"方向":>6s}')
print(f'  {"-"*75}')

cats = {
    'pre': ['pre_vol_slope','pre_price_slope','pre_vol_expansion','pre_price_compression','pre_sum_chg','pre_pos_days','pre_close_pos'],
    'sec': ['sec_zt_count','sec_avg_gain','sec_has_zhongjun','sec_is_top1','sec_rank','sec_pos_days_3d','sec_stock_count'],
    'mkt': ['mkt_7pct_count','mkt_zt_count','mkt_avg_7pct_gain','mkt_hot_sector_count','mkt_sh_above_ma60'],
    'stock': ['lu_250d','lu_60d','lu_20d','est_float_cap','price','max_consec_zt','is_250d_high','days_since_last_zt','avg_amount_20d_yi'],
}

for key, zm, nm, diff, p_val, t_stat in sig:
    direction = '更高' if diff > 0 else '更低'
    sig_mark = '***' if p_val < 0.01 else ('**' if p_val < 0.05 else '*')

    # Find category
    cat = 'other'
    for cname, ckeys in cats.items():
        if key in ckeys: cat = cname; break

    print(f'  {sig_mark} [{cat:5s}] {key:<30s} {zm:>10.2f} {nm:>10.2f} {diff:>+8.2f} {p_val:>8.4f} {direction:>6s}')

print(f'\n  共 {len(sig)} 个显著特征')

# Category summary
print()
print('=' * 85)
print('  按类别汇总')
print('=' * 85)
cat_names = {'pre': '突破前行为', 'sec': '板块热度', 'mkt': '市场情绪', 'stock': '股票股性'}
for cname, ckeys in cats.items():
    cat_sig = [(k, zm, nm, diff, p, t) for k, zm, nm, diff, p, t in sig if k in ckeys]
    if cat_sig:
        print(f'\n  --- {cat_names.get(cname, cname)} ({len(cat_sig)} 个显著特征) ---')
        for key, zm, nm, diff, p_val, t_stat in cat_sig:
            sig_mark = '***' if p_val < 0.01 else ('**' if p_val < 0.05 else '*')
            direction = '⬆' if diff > 0 else '⬇'
            print(f'    {sig_mark} {key:<32s} {zm:>10.4f} vs {nm:>10.4f}  {direction}  p={p_val:.4f}')
    else:
        print(f'\n  --- {cat_names.get(cname, cname)}: 无显著特征 ---')
