"""机构票筛选器

硬性条件（全满足才入选）：
- 机构持股家数 > 50（基金持有家数为主要构成）
- 近一年涨停次数 > 3
- 流通市值 > 200 亿（clist 在线时精确校验，回退路径放宽）
- 非 ST / *ST

筛选顺序按成本从低到高：ST过滤 → 机构家数 → 涨停次数 → 市值
"""
from typing import List, Dict
from .base import BaseScreener
from ..data.models import ScreeningResult


class InstitutionScreener(BaseScreener):
    """机构票筛选: 机构持股家数 + 涨停活跃度 + 大市值"""
    name = '机构票'

    def screen(self) -> List[ScreeningResult]:
        # 1) 获取机构持股数据（季度数据，缓存 30 天）
        holder_map: Dict[str, int] = {}
        try:
            holder_map = self.ak.get_institution_holder_count()
        except Exception:
            pass
        if not holder_map:
            return []

        # 2) 获取行情快照（取流通市值和名称）
        try:
            spot_df = self.ak.get_spot_df()
        except Exception:
            return []
        if spot_df.empty:
            return []

        # 判断市值数据是否来自 clist（有真实市值）还是回退路径（市值为0）
        has_real_mcap = (spot_df['流通市值'].sum() > 0)

        results = []
        for _, row in spot_df.iterrows():
            code = str(row.get('代码', '')).zfill(6)
            name = str(row.get('名称', ''))
            if not name or not code or len(code) != 6:
                continue

            # --- 非 ST（成本最低，最先过滤）---
            if name.startswith(('ST', '*ST', 'SST', 'S*ST', 'NST')):
                continue

            # --- 机构持股家数 > 50 ---
            holder_count = holder_map.get(code, 0)
            if holder_count <= 50:
                continue

            # --- 流通市值 > 200 亿 ---
            float_mcap = float(row.get('流通市值', 0)) / 1e8  # 元 → 亿
            mcap_estimated = False
            if not has_real_mcap or float_mcap <= 0:
                # 回退路径：市值数据缺失。机构家数>50 本身暗示一定规模，
                # 但仍用最新价做个粗过滤（排除 5 元以下大概率小盘股）
                latest_price = float(row.get('最新价', 0))
                if latest_price < 5:
                    continue
                float_mcap = 201  # 标记为通过，但注明估算
                mcap_estimated = True
            if float_mcap <= 200:
                continue

            # --- 近一年涨停 > 3 次（成本最高，放最后）---
            limit_up_count = self._count_limit_ups(code)
            if limit_up_count <= 3:
                continue

            # --- 评分排序 ---
            score = 0
            reasons = []

            # 机构家数
            holder_score = min(holder_count / 100 * 40, 40)
            score += holder_score
            reasons.append(f'机构{holder_count}家')

            # 涨停次数
            lu_score = min(limit_up_count / 10 * 35, 35)
            score += lu_score
            reasons.append(f'年涨停{limit_up_count}次')

            # 市值
            if not mcap_estimated:
                if 200 <= float_mcap <= 2000:
                    score += 25
                elif float_mcap <= 5000:
                    score += 15
                else:
                    score += 5
                reasons.append(f'流通市值{float_mcap:.0f}亿')
            else:
                score += 15  # 估算路径给固定分
                reasons.append(f'流通市值>200亿(估)')

            sector = self._get_sector(code)
            results.append(ScreeningResult(
                code=code, name=name, strategy=self.name,
                score=min(round(score), 100), reasons=reasons,
                detail={
                    'holder_count': holder_count,
                    'limit_up_count': limit_up_count,
                    'float_market_cap': float_mcap,
                    'board_type': sector,
                }
            ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:50]
