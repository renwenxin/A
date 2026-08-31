"""共振与启动信号 — 逻辑哥《共振与启动》(BV17mhG6mEDL) 三步法量化

两个信号：
  信号① 板块涨停家数「渐进递增 vs 单日爆发」—— 视频观点：涨停家数渐进递增
        （1+2→3+4→5+6）比单日爆发（10+20+40）可靠，后者多为消息流。
  信号② 冰点量化 —— 空间板高度压到 3-5 板、跌停家数从两位数收敛到个位数/零、
        老主线退潮 5-7 天 → 冰点转折窗口。

数据源：data/cache/ 与 data/cache/persist/ 下已有的 review_report_*.json
（每日复盘缓存，无网络调用；每天 generate() 后文件自然积累、序列自动变长）。
"""
import glob
import json
import os
from collections import defaultdict
from typing import Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
CACHE_DIR = os.path.join(DATA_DIR, 'cache')
PERSIST_DIR = os.path.join(CACHE_DIR, 'persist')

_MOMENTUM_LABEL = {
    'surge': '单日爆发',
    'progressive': '渐进递增',
    'sustained': '持续强势',
    'declining': '回落',
    'dormant': '低迷',
    'normal': '平稳',
    'insufficient': '数据不足',
}
_MOMENTUM_RANK = {
    'surge': 6, 'progressive': 5, 'sustained': 4, 'declining': 3,
    'normal': 2, 'dormant': 1, 'insufficient': 0,
}


# ----------------------------------------------------------------------
# 历史序列加载
# ----------------------------------------------------------------------
def load_review_history(days: int = 12) -> List[Dict]:
    """读取已有 review_report 缓存，返回按日期升序的最近 `days` 个交易日 dict 列表。

    每份缓存取 _payload，提取当日维度（空间板/涨停/跌停/上涨家数/情绪周期）和
    板块涨停聚合（sector_analysis.all_sectors）。文件不存在/损坏 → 跳过。
    """
    files = set()
    files.update(glob.glob(os.path.join(CACHE_DIR, 'review_report_*.json')))
    files.update(glob.glob(os.path.join(PERSIST_DIR, 'review_report_*.json')))
    by_date: Dict[str, Dict] = {}
    for path in files:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        payload = data.get('_payload')
        if not payload or payload.get('error'):
            continue
        date_str = str(payload.get('date') or '')
        if not date_str:
            continue
        mo = payload.get('market_overview') or {}
        sa = payload.get('sector_analysis') or {}
        sectors = [{
            'name': s.get('name'),
            'zt_count': int(s.get('zt_count') or 0),
            'max_consecutive': int(s.get('max_consecutive') or 0),
            'leader_name': s.get('leader_name') or '',
        } for s in (sa.get('all_sectors') or [])]
        by_date[date_str] = {
            'date': date_str,
            'max_consecutive': int(payload.get('max_consecutive') or 0),
            'limit_up': int(mo.get('limit_up_count') or 0),
            'limit_down': int(mo.get('limit_down_count') or 0),
            'up_count': int(mo.get('up_count') or 0),
            'down_count': int(mo.get('down_count') or 0),
            'cycle_stage': (payload.get('cycle') or {}).get('stage') or '',
            'cycle_class': (payload.get('cycle') or {}).get('stage_class') or '',
            'sectors': sectors,
        }
    if not by_date:
        return []
    ordered = sorted(by_date.values(), key=lambda d: d['date'])
    return ordered[-days:]


# ----------------------------------------------------------------------
# 信号① 板块涨停家数动量分类
# ----------------------------------------------------------------------
def _classify_seq(zs: List[int]) -> str:
    """按最近 2-3 日 zt 序列 + 峰值给单个板块分类。

    surge / declining 只需要 2 天就能判定；progressive / sustained 需要 3 天。
    """
    n = len(zs)
    if n < 2:
        return 'insufficient'
    today, prev = zs[-1], zs[-2]
    peak = max(zs)
    # 单日爆发：今日涨停潮级(≥5)且昨日很低(≤2)，且昨天没有先期启动
    # （昨天也在上升 → 属于渐进/加速而非无预警爆发）
    if today >= 5 and prev <= 2 and (n < 3 or prev <= zs[-3]):
        return 'surge'
    # 回落：历史峰值 ≥5 且最近一天开始下滑
    if peak >= 5 and prev > today:
        return 'declining'
    if n >= 3:
        prev2 = zs[-3]
        # 渐进递增：连续 2 天上升且末日 ≥3 —— 健康启动
        if prev2 < prev < today and today >= 3:
            return 'progressive'
        # 持续强势：最近 3 天均 ≥3 —— 真主线特征
        if prev2 >= 3 and prev >= 3 and today >= 3:
            return 'sustained'
    if peak <= 2:
        return 'dormant'
    return 'normal'


def classify_sector_momentum(history: List[Dict]) -> List[Dict]:
    """汇总每板块最近 N 日涨停家数序列并分类。

    只保留末日仍有涨停记录的板块（对"动量"有意义）。按 surge/progressive 优先、
    再按今日涨停数降序排列。
    """
    if not history:
        return []
    seq_map: Dict[str, List] = defaultdict(list)
    for day in history:
        date_str = day['date']
        for s in day['sectors']:
            seq_map[s['name']].append((date_str, s['zt_count'], s['leader_name'] or ''))
    last_day_names = {s['name'] for s in history[-1]['sectors']}

    out = []
    for name, seq in seq_map.items():
        if name not in last_day_names:
            continue
        zs = [z for _, z, _ in seq]
        label = _classify_seq(zs)
        today_zt = zs[-1] if zs else 0
        peak_zt = max(zs) if zs else 0
        strength = ('涨停潮🔥' if today_zt >= 8 else
                    ('强势' if today_zt >= 5 else
                     ('活跃' if today_zt >= 3 else '普通')))
        out.append({
            'name': name,
            'label': label,
            'label_cn': _MOMENTUM_LABEL.get(label, label),
            'zt_seq': zs,
            'leader': seq[-1][2] if seq else '',
            'today_zt': today_zt,
            'peak_zt': peak_zt,
            'strength': strength,
        })
    out.sort(key=lambda x: (_MOMENTUM_RANK.get(x['label'], 0), x['today_zt']), reverse=True)
    return out


# ----------------------------------------------------------------------
# 信号② 冰点量化
# ----------------------------------------------------------------------
def _decline_days(history: List[Dict]) -> int:
    """退潮天数：从序列尾部往前数，涨停家数或空间板高度连续走低的交易日数。"""
    zts = [d.get('limit_up') or 0 for d in history]
    spaces = [d.get('max_consecutive') or 0 for d in history]
    n = len(history)
    days = 0
    for i in range(n - 1, 0, -1):
        if (zts[i] <= zts[i - 1]) or (spaces[i] <= spaces[i - 1]):
            days += 1
        else:
            break
    return days


def quantify_ice_point(history: List[Dict]) -> Optional[Dict]:
    """冰点量化：空间板高度 / 涨停家数 / 跌停家数 / 退潮天数 多维度打分。

    阈值参考视频口径 + 现有 strategy_regime.regime 的 ICE_ZT=30 / ICE_UP=1200。
    涨停家数用 TDX 全市场 ≥9.9% 口径（market_overview.limit_up_count），与
    market_state.csv 的 limit_up 同源，打分口径一致。
    返回 None 表示无历史数据。
    """
    if not history:
        return None
    today = history[-1]
    space = today.get('max_consecutive') or 0
    limit_up = today.get('limit_up') or 0
    limit_down = today.get('limit_down') or 0
    up_count = today.get('up_count') or 0
    decline_days = _decline_days(history)

    score = 0
    detail: List[Dict] = []

    if space <= 3:
        score += 2
        detail.append({'dim': '空间板高度', 'score': 2, 'reason': f'仅{space}板，高度压缩至极'})
    elif space <= 5:
        score += 1
        detail.append({'dim': '空间板高度', 'score': 1, 'reason': f'{space}板，高度受压制'})
    else:
        detail.append({'dim': '空间板高度', 'score': 0, 'reason': f'{space}板，高度健康'})

    if limit_up <= 30:
        score += 2
        detail.append({'dim': '涨停家数', 'score': 2, 'reason': f'{limit_up}家，极度萎缩'})
    elif limit_up <= 50:
        score += 1
        detail.append({'dim': '涨停家数', 'score': 1, 'reason': f'{limit_up}家，明显偏少'})
    else:
        detail.append({'dim': '涨停家数', 'score': 0, 'reason': f'{limit_up}家，正常'})

    if limit_down <= 10:
        score += 1
        detail.append({'dim': '跌停家数', 'score': 1, 'reason': f'{limit_down}家，恐慌已收敛（见底信号）'})
    elif limit_down >= 50:
        score -= 1
        detail.append({'dim': '跌停家数', 'score': -1, 'reason': f'{limit_down}家，恐慌加剧'})
    else:
        detail.append({'dim': '跌停家数', 'score': 0, 'reason': f'{limit_down}家，中性'})

    if decline_days >= 5:
        score += 2
        detail.append({'dim': '退潮天数', 'score': 2, 'reason': f'{decline_days}天，退潮充分'})
    elif decline_days >= 3:
        score += 1
        detail.append({'dim': '退潮天数', 'score': 1, 'reason': f'{decline_days}天，退潮进行中'})
    else:
        detail.append({'dim': '退潮天数', 'score': 0, 'reason': f'{decline_days}天'})

    if up_count <= 1200:
        score += 1
        detail.append({'dim': '上涨家数', 'score': 1, 'reason': f'{up_count}家，极度低迷'})
    else:
        detail.append({'dim': '上涨家数', 'score': 0, 'reason': f'{up_count}家'})

    if score >= 6:
        verdict, verdict_cn = 'ice_strong', '强冰点'
    elif score >= 4:
        verdict, verdict_cn = 'ice', '冰点'
    elif score >= 2:
        verdict, verdict_cn = 'near_ice', '临近冰点'
    else:
        verdict, verdict_cn = 'normal', '正常'

    return {
        'today': {
            'date': today['date'],
            'max_consecutive': space,
            'limit_up': limit_up,
            'limit_down': limit_down,
            'up_count': up_count,
            'cycle_stage': today.get('cycle_stage') or '',
            'cycle_class': today.get('cycle_class') or '',
        },
        'trend': [{
            'date': d['date'],
            'space': d.get('max_consecutive') or 0,
            'limit_up': d.get('limit_up') or 0,
            'limit_down': d.get('limit_down') or 0,
            'up': d.get('up_count') or 0,
            'stage': d.get('cycle_stage') or '',
            'stage_class': d.get('cycle_class') or '',
        } for d in history],
        'decline_days': decline_days,
        'score': score,
        'verdict': verdict,
        'verdict_cn': verdict_cn,
        'detail': detail,
    }
