# ashare_review/strategy_lab/collectors/one2.py
"""复盘一进二精选 Top10（数据源: report.sentiment.picks）"""
import json

from ..snapshot import enrich_stock


def collect_one2(trade_date, tdx, ak, report_payload=None) -> list:
    if not report_payload or not (report_payload.get('sentiment') or {}).get('picks'):
        raise RuntimeError(
            f'{trade_date} 复盘报告未生成或今日无精选：请先到"复盘分析"页生成/刷新当日报告（sentiment.picks 为空）。')
    picks = report_payload['sentiment']['picks'][:10]
    items = []
    for i, p in enumerate(picks, start=1):
        snap = enrich_stock(p['code'], tdx) or {}
        items.append({
            'code': p['code'], 'name': p['name'], 'rank': i,
            'sector': p.get('sector_name', '') or snap.get('sector', ''),
            'regime': '',
            'amount_yi': snap.get('amount_yi'), 'vol_ratio': snap.get('vol_ratio'),
            'seal_ratio': p.get('seal_ratio') if p.get('seal_ratio') is not None
                          else snap.get('seal_ratio'),
            'open_px': snap.get('open_px'), 'high_px': snap.get('high_px'),
            'low_px': snap.get('low_px'),
            'close_px': snap.get('close_px') or p.get('price'),
            'gap_pct': None, 'next_return': None,
            'extra': json.dumps({
                'score': p.get('score'), 'reasons': p.get('reasons', []),
                'seal_ratio': p.get('seal_ratio'), 'seal_strength': p.get('seal_strength'),
                'price': p.get('price'), 'market_cap': p.get('market_cap'),
                'limit_up_time': p.get('limit_up_time'), 'theme_stage': p.get('theme_stage'),
                'theme_label': p.get('theme_label'),
                'sector_zt_count': p.get('sector_zt_count'),
                'board_type': p.get('board_type')},
                ensure_ascii=False),
        })
    return items
