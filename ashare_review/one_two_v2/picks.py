# ashare_review/one_two_v2/picks.py
"""今日1进2 — 盘后 8 维打分 + 战法分类（视频方法论）"""
from typing import Dict, List


def filter_candidates(pool: List) -> List[Dict]:
    """候选池：首板、非一字板、非ST、非北交所。返回 [{lu, code, name}]"""
    cands = []
    for lu in pool:
        code = str(lu.code)
        if lu.consecutive != 1:
            continue
        if code.startswith(('8', '4', '92', '30', '68')):
            continue   # 主板优先（排除北交/创业板/科创板）
        try:
            if str(lu.name).startswith(('ST', '*ST', 'SST', 'S*ST')):
                continue
        except Exception:
            pass
        try:
            t = str(lu.limit_up_time).replace(':', '')[:4]
            if t == '0925':   # 一字板
                continue
        except Exception:
            pass
        cands.append({'lu': lu, 'code': code, 'name': lu.name})
    return cands


def _parse_time(lu) -> int:
    try:
        return int(str(lu.limit_up_time).replace(':', '')[:4])
    except (ValueError, TypeError):
        return 1400


def score_dimension(dim: str, lu, ctx: dict, weights: dict) -> Dict:
    """单维打分，返回 {'score': float, 'reason': str}。ctx 为上下文 dict。"""
    w = weights['dimensions'].get(dim, 0)
    if dim == 'quality':
        t = _parse_time(lu)
        s = 0
        if t <= 1030:
            s += 10
        elif t <= 1130:
            s += 6
        elif t <= 1400:
            s += 2
        else:
            s -= 4
        if lu.turnover and lu.seal_amount:
            sr = lu.seal_amount / lu.turnover
            if sr > 0.5:
                s += 10
            elif sr > 0.3:
                s += 4
        if lu.float_market_cap and lu.seal_amount:
            ss = lu.seal_amount / (lu.float_market_cap * 10000)
            if ss > 0.015:
                s += 6
        if lu.is_seal and not lu.is_broken:
            s += 4
        return {'score': round(s * w / 30, 1) if w else 0, 'reason': f'质量{s}'}
    if dim == 'theme_stage':
        sector = ctx.get('sector') or {}
        zt = sector.get('zt_count', 0)
        cons = sector.get('max_consecutive', 1)
        if zt >= 8 and cons >= 4:
            return {'score': -w, 'reason': '兑现期·追高危险'}
        if zt >= 5 or (zt >= 3 and cons >= 2):
            return {'score': w, 'reason': '试水期·爆发力最强'}
        if zt <= 2:
            return {'score': -w * 0.5, 'reason': '朦胧期·一日游风险'}
        return {'score': 0, 'reason': '题材中性'}
    if dim == 'emotion':
        trend = ctx.get('zt_trend', '')
        if trend == 'double_ice':
            return {'score': w, 'reason': '二连冰·情绪转暖前夜'}
        if trend == 'double_climax':
            return {'score': -w, 'reason': '连高两日·高潮次日回避'}
        return {'score': 0, 'reason': '情绪中性'}
    if dim == 'energy_ladder':
        if ctx.get('ladder_at_2'):
            return {'score': w, 'reason': '高能量梯队在2板'}
        if ctx.get('ladder_at_3'):
            return {'score': -w * 0.5, 'reason': '资金主攻3板·1进2弱化'}
        return {'score': 0, 'reason': '梯队中性'}
    if dim == 'volume_health':
        tv = ctx.get('today_vol')
        pv = ctx.get('prev_high_vol')
        pct = weights['thresholds'].get('volume_health_pct', 80.0)
        if tv and pv and pv > 0:
            ratio = tv / pv
            if 0.7 <= ratio <= 1.0:
                return {'score': w, 'reason': f'量能健康({ratio:.0%}前高量)'}
            if ratio < 0.4 or ratio > 1.5:
                return {'score': -w * 0.5, 'reason': f'量能极端({ratio:.0%})'}
            return {'score': w * 0.5, 'reason': f'量能尚可({ratio:.0%})'}
        return {'score': 0, 'reason': '量能数据缺失'}
    if dim == 'theme_overlay':
        n = ctx.get('concept_count', 0)
        coverage = ctx.get('concept_coverage', 100)   # 概念库覆盖大小
        if n >= 3:
            return {'score': w, 'reason': f'题材叠加({n}概念)·万金油'}
        if n >= 1:
            return {'score': w * 0.5, 'reason': f'{n}概念'}
        if coverage < 20:
            return {'score': 0, 'reason': '概念库未覆盖(小库)'}   # 小库时未知≠孤立，不惩罚
        return {'score': -w * 0.25, 'reason': '题材孤立'}
    if dim == 'cap_price':
        cap = lu.float_market_cap or 0
        price = lu.close_price or 0
        cap_max = weights['thresholds'].get('cap_max', 100.0)
        price_max = weights['thresholds'].get('price_max', 15.0)
        if cap <= 50 and price <= price_max:
            return {'score': w, 'reason': f'市值{cap:.0f}亿·价{price:.0f}·轿子轻'}
        if cap <= cap_max:
            return {'score': w * 0.5, 'reason': '市值适中'}
        return {'score': -w * 0.5, 'reason': f'市值{cap:.0f}亿偏大'}
    if dim == 'status':
        if ctx.get('upper_same_theme'):
            return {'score': -w, 'reason': '上方有同题材高位·后排套利'}
        return {'score': w, 'reason': '无同题材高位·补涨龙机会'}
    return {'score': 0, 'reason': ''}


def classify_tactic(lu) -> str:
    """战法分类：weak_strong(烂板/尾盘) / graph(图形突破候选) / auction(竞价爆量)"""
    if _parse_time(lu) >= 1400 or (lu.is_broken and lu.is_seal):
        return 'weak_strong'
    return 'auction'


def compute_score(lu, ctx: dict, weights: dict) -> Dict:
    """8 维加权总分。返回 {score, dimensions: {dim: {score, reason}}, tactic}"""
    dims = {}
    total = 0.0
    for dim in ('quality', 'theme_stage', 'emotion', 'energy_ladder',
                'volume_health', 'theme_overlay', 'cap_price', 'status'):
        r = score_dimension(dim, lu, ctx, weights)
        dims[dim] = r
        total += r['score']
    return {'score': round(total, 1), 'dimensions': dims,
            'tactic': classify_tactic(lu)}