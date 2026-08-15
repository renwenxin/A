"""
2024 年数据缓存构建器

运行方式（终端执行）：
  cd D:/cursor/project
  python ashare_review/analysis/build_2024_cache.py

注意：
  需要先关闭 Flask 服务（Ctrl+C），因为这个脚本会占用 TDX 读取资源。
  运行时间：约 2~4 小时（取决于 CPU 速度）。
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from collections import defaultdict

from ashare_review.data.tdx_reader import TdxReader
from ashare_review.analysis.indicators import enrich_all


def build_2024_cache():
    """扫描全市场股票 2024 年数据，构建 gainers 和 sector_stats 缓存。"""
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')

    # 生成 2024-01 ~ 2024-12 的全部交易日
    start = date(2024, 1, 2)
    end = date(2024, 12, 31)
    all_dates = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            all_dates.append(d)
        d += __import__('datetime').timedelta(days=1)

    ds_set = {d.strftime('%Y%m%d') for d in all_dates}
    print(f'2024 交易日: {len(all_dates)} 天')

    # 扫描全市场股票
    tdx = TdxReader()
    stocks = tdx.list_stocks()
    print(f'全市场股票: {len(stocks)} 只')

    result = {ds: [] for ds in ds_set}
    sector_stats = {ds: {} for ds in ds_set}

    has_imap = False
    # 尝试加载行业映射
    imap_path = os.path.join(DATA_DIR, 'industry_map.json')
    industry_map = {}
    if os.path.exists(imap_path):
        with open(imap_path, 'r', encoding='utf-8') as f:
            industry_map = json.load(f)
        if len(industry_map) > 500:
            has_imap = True
            print(f'Loaded industry map: {len(industry_map)} stocks')

    total = len(stocks)
    t0 = __import__('time').time()
    gainers_count = 0

    for si, (code, market) in enumerate(stocks):
        if (si + 1) % 500 == 0:
            elapsed = __import__('time').time() - t0
            print(f'[{si+1}/{total}] {elapsed:.0f}s, gainers={gainers_count}')

        df = tdx.read_daily(code, market)
        if df is None or df.empty:
            continue
        # 只处理有日期索引的数据
        try:
            dates_list = [str(d)[:10] for d in df.index]
        except Exception:
            dates_list = [str(d)[:10] for d in range(len(df))]

        # 找 Date 或 date 列
        date_col = None
        for col in ['Date', 'date', 'DATE', 'datetime', 'Datetime']:
            if col in df.columns:
                date_col = col
                break

        if date_col:
            dates_list = [str(d)[:10] for d in df[date_col]]

        # 计算涨跌幅
        if 'close' in df.columns:
            pct = df['close'].pct_change() * 100
        else:
            continue

        industry = industry_map.get(code, '') if has_imap else ''

        for row_i in range(len(df)):
            if date_col:
                ds = str(df[date_col].iloc[row_i])[:10].replace('-', '')
            else:
                ds = str(df.index[row_i])[:10].replace('-', '')
            if ds not in ds_set:
                continue

            chg = float(pct.iloc[row_i]) if row_i > 0 and not pd.isna(pct.iloc[row_i]) else 0

            # 7%+ 涨幅
            if chg >= 7.0:
                close_val = float(df['close'].iloc[row_i])
                vol_val = float(df['volume'].iloc[row_i]) if 'volume' in df.columns else 0
                amt_val = float(df['amount'].iloc[row_i]) if 'amount' in df.columns else vol_val * close_val

                result[ds].append({
                    'code': code, 'market': market, 'change_pct': chg,
                    'close': close_val, 'volume': vol_val, 'amount': amt_val,
                    'industry': industry,
                })

                # 行业统计
                if industry:
                    sec = sector_stats[ds].setdefault(industry, {
                        'sum_gain': 0, 'count': 0, 'zt_count': 0, 'zhongjun_amt': 0
                    })
                    sec['sum_gain'] += chg
                    sec['count'] += 1
                    if chg >= 9.5:
                        sec['zt_count'] += 1
                    if amt_val > sec['zhongjun_amt']:
                        sec['zhongjun_amt'] = amt_val

                gainers_count += 1

    elapsed = __import__('time').time() - t0
    print(f'\n完成: {elapsed:.0f}s, 共 {gainers_count} 条 7%+ 记录')

    # 保存 gainers 缓存
    gainers_out = os.path.join(DATA_DIR, 'gainers_7pct_2024.json')
    with open(gainers_out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False)
    print(f'Saved: {gainers_out}')

    # 只保存有数据的日期
    non_empty = {k: v for k, v in result.items() if v}
    print(f'有交易数据的日期: {len(non_empty)} / {len(all_dates)}')

    # 保存 sector_stats 缓存
    sector_out = os.path.join(DATA_DIR, 'sector_daily_stats_2024.json')
    cleaned = {}
    for ds, secs in sector_stats.items():
        cleaned[ds] = {ind: {
            'sum_gain': round(s['sum_gain'], 2),
            'count': s['count'],
            'zt_count': s['zt_count'],
            'zhongjun_amt': s['zhongjun_amt'],
        } for ind, s in secs.items() if s['count'] > 0}
    with open(sector_out, 'w', encoding='utf-8') as f:
        json.dump(cleaned, f, ensure_ascii=False)
    print(f'Saved: {sector_out}')


if __name__ == '__main__':
    build_2024_cache()
