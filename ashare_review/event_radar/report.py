"""分析结果组装 + Markdown 导出。"""
import json, os
from datetime import date as _date

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'event_radar')
RESULTS_DIR = os.path.join(DATA_DIR, 'results')


def build_result(trade_date: str, events: list) -> dict:
    return {'date': trade_date, 'generated_at': _date.today().strftime('%Y-%m-%d %H:%M'),
            'events': events}


def _table(headers: list, rows: list) -> str:
    if not rows:
        return '_（无）_'
    line = '| ' + ' | '.join(headers) + ' |'
    sep = '|' + '---|' * len(headers)
    body = ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join([line, sep] + body)


def to_markdown(result: dict) -> str:
    out = [f'# 📡 事件雷达 · {result["date"]}', '']
    for ev in result.get('events', []):
        out += [f'## {ev["theme"]} — {ev.get("description", "")}', '']
        for ch in ev.get('chains', []):
            out.append(f'**{ch["node"]}**（成分 {len(ch.get("codes", []))} 只 · 来源 {ch.get("source", "-")}）')
        out.append('')
        out.append('### 🚀 龙头股')
        out.append(_table(['代码', '名称', '涨幅%', '量比', '涨停'],
                          [[s['code'], s.get('name', ''), s['pct'], s['vol_ratio'], '✓' if s['is_zt'] else '']
                           for s in ev.get('leaders', [])]))
        out.append('')
        out.append('### ⚡ 潜力股（未大涨但资金启动）')
        out.append(_table(['代码', '名称', '涨幅%', '量比'],
                          [[s['code'], s.get('name', ''), s['pct'], s['vol_ratio']]
                           for s in ev.get('potentials', [])]))
        if ev.get('lhb'):
            out.append('')
            out.append('### 💰 龙虎榜')
            out.append(_table(['代码', '名称', '净买(万)', '类型'],
                              [[x['code'], x['name'], x['net_buy'], x['type']] for x in ev['lhb']]))
        if ev.get('next_day_notes'):
            out += ['', '### 🎯 明日关注', ev['next_day_notes']]
        out.append('')
    out.append('> ⚠️ 本报告由系统自动生成，不构成投资建议；参与度以公司公告为准。')
    return '\n'.join(out)


def save_result(result: dict, trade_date: str) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    p = os.path.join(RESULTS_DIR, f'{trade_date}.json')
    json.dump(result, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    return p


def load_result(trade_date: str) -> dict:
    p = os.path.join(RESULTS_DIR, f'{trade_date}.json')
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding='utf-8'))
