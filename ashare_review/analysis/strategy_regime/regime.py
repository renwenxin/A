"""行情分类 — 用上证指数 + 国证2000 + 市场广度给每个交易日打 regime 标签

三大维度（全部只用当日及之前数据，无未来函数）:
  1. 缠论趋势 (sh_trend): 上证日线笔 → 上涨/下跌/盘整
  2. 小盘风格 (rel_strength): 国证2000 vs 上证 20日动量差 → 小盘强/弱
  3. 情绪温度 (emotion): 涨停家数 + 上涨家数 → 极冰点/冰点/低迷/温和/活跃/火爆

Regime 标签 → 推荐战法:
  强势趋势  → 启动突破 V3
  题材轮动  → 1进2 接力
  冰点超跌  → 冰点抄底
  震荡观望  → 轻仓（等信号）
  弱市回调  → 轻仓（只做最强信号）
  退潮下跌  → 空仓（管住手）
"""
import numpy as np
import pandas as pd


def compute_regime(state_df: pd.DataFrame) -> pd.DataFrame:
    """给 market_state DataFrame 添加 regime 分类列。"""
    df = state_df.copy()

    # ── 小盘风格：国证2000 vs 上证 20日动量 ──
    df['sh_mom20'] = df['sh_close'].pct_change(20) * 100
    df['gz_mom20'] = df['gz_close'].pct_change(20) * 100
    df['rel_strength'] = df['gz_mom20'] - df['sh_mom20']

    # 上涨比例
    df['up_ratio'] = df['up_count'] / (df['up_count'] + df['down_count']).replace(0, 1)

    # ── 情绪温度 ──
    def emotion(r):
        up = r['up_count'] or 0
        lu = r['limit_up'] or 0
        if up <= 800 or lu <= 20:
            return '极冰点'
        if up <= 1200 or lu <= 30:
            return '冰点'
        if lu <= 40:
            return '低迷'
        if lu <= 60:
            return '温和'
        if lu <= 100:
            return '活跃'
        return '火爆'

    df['emotion'] = df.apply(emotion, axis=1)

    # ── regime 决策树 ──
    def regime(r):
        emo = r['emotion']
        # 优先用"当下笔"方向（更及时），回退到已确认笔
        trend = r.get('sh_trend_now') or r.get('sh_trend', '盘整')
        small_strong = r.get('rel_strength', 0) > 2.0
        small_weak = r.get('rel_strength', 0) < -2.0
        sh_ma60 = r.get('sh_ma60')
        sh_close = r.get('sh_close')
        above_ma60 = (sh_ma60 is not None and sh_close is not None
                      and not pd.isna(sh_ma60) and sh_close > sh_ma60)
        limit_down = r.get('limit_down', 0) or 0

        # 1) 冰点/极冰点 → 冰点超跌（冰点抄底战法的主场）
        if emo in ('冰点', '极冰点'):
            return '冰点超跌'

        # 2) 恐慌（跌停潮）→ 冰点超跌或退潮，看趋势
        if limit_down >= 50:
            return '冰点超跌' if trend == '下跌' else '退潮下跌'

        # 3) 强势趋势 → 启动突破 V3
        if trend == '上涨' and (emo in ('活跃', '火爆') or above_ma60):
            return '强势趋势'

        # 4) 题材轮动 → 1进2（小盘强 + 非下跌）
        if small_strong and trend in ('上涨', '盘整') and emo in ('低迷', '温和', '活跃'):
            return '题材轮动'

        # 5) 下跌环境
        if trend == '下跌':
            if emo == '低迷':
                return '弱市回调'
            return '退潮下跌'

        # 6) 权重防御（上证强于国证2000 + 非下跌）→ 大盘风格，偏震荡
        if small_weak and above_ma60:
            return '震荡观望'

        # 7) 默认：盘整观望
        return '震荡观望'

    df['regime'] = df.apply(regime, axis=1)

    # 推荐战法
    STRATEGY_MAP = {
        '强势趋势': '启动突破V3',
        '题材轮动': '1进2接力',
        '冰点超跌': '冰点抄底',
        '震荡观望': '轻仓观望',
        '弱市回调': '轻仓观望',
        '退潮下跌': '空仓',
    }
    df['recommend'] = df['regime'].map(STRATEGY_MAP)
    return df
