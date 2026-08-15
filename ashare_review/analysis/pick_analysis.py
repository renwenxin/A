"""Top 3 精选标的技术面分析 + 操作建议生成器 — 龙哥均线体系增强版

对策略筛选出的 Top 3 标的，基于 TDX 日线数据做：
1. 完整均线体系（5/10/20/60/89/250）位置 + 多头/空头判断
2. 日K承接分析（支撑压力位）— 龙哥承接战法
3. 量价关系 + 均线信号量能验证
4. 筹码分布成本分析
5. 可执行的操作建议（入场/止损/目标/仓位）

均线体系（龙哥口诀）：
- 5/10日线：短线生命线
- 20日线：中线导航标
- 60日线(季线)：波段入场基准
- 89/250日线：长线定盘星

承接分析核心（龙哥）：
- 涨停大阳收盘价 = 重要支撑位，连续不破 → 承接强
- 箱体下沿 = 支撑位，不破 → 承接没问题
- 断板次日三种走势：反包涨停/反包震荡/继续下跌
"""
from typing import Dict, List, Optional, Tuple
import numpy as np


def _native(val):
    """将 numpy 类型转为 Python 原生类型"""
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return float(val)
    if isinstance(val, np.bool_):
        return bool(val)
    if isinstance(val, np.ndarray):
        return val.tolist()
    return val


def analyze_pick(code: str, tdx, strategy: str, detail: dict,
                 use_agents: bool = False) -> dict:
    """对单个标的做技术分析

    Args:
        use_agents: 是否启用多Agent共识分析（需要LLM API Key）
    """
    support_period = {'auction': 5, 'one_two': 10, 'leader': 10,
                      'breakout': 20, 'institution': 20,
                      'sector_divergence': 10}.get(strategy, 10)
    tech = _tech_analysis(code, tdx, support_period=support_period)

    if use_agents:
        agent_result = analyze_with_agents(code, strategy, detail)
        suggestion = agent_result.get('suggestion', {})
    else:
        suggestion = _trading_suggestion(strategy, tech, detail)

    return {'tech': tech, 'suggestion': suggestion}


# ------------------------------------------------------------------
# 技术面分析（龙哥均线体系增强）
# ------------------------------------------------------------------
def _tech_analysis(code: str, tdx, support_period: int = 10) -> Dict:
    """读取日线+指标，输出技术面速览"""
    try:
        from .indicators import enrich_all
        market = 'sh' if code.startswith('6') else 'sz'
        if code.startswith('8') or code.startswith('4'):
            market = 'bj'
        df = tdx.read_daily(code, market)
        if len(df) < 60:
            return _empty_tech()
        df = enrich_all(df)
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else latest

        close = float(latest['close'])

        # ============================================
        # 一、完整均线体系（5/10/20/60/89/250）
        # ============================================
        ma_status = []
        ma_periods = [5, 10, 20, 60, 89, 250]

        for p in ma_periods:
            ma_val = latest.get(f'ma{p}', 0)
            if ma_val and ma_val > 0 and not np.isnan(ma_val):
                ma_val_f = float(ma_val)
                above = close > ma_val_f
                pct_from = (close - ma_val_f) / ma_val_f * 100 if ma_val_f > 0 else 0
                ma_status.append({
                    'period': p,
                    'above': _native(above),
                    'ma_price': round(ma_val_f, 2),
                    'pct_from_ma': round(pct_from, 1),
                })

        # 站上均线数
        above_count = sum(1 for m in ma_status if m['above'])

        # 均线排列判断
        ma_arrangement = _judge_ma_arrangement(ma_status)
        ma_summary = _ma_summary_text(ma_status, above_count)

        # ============================================
        # 二、MACD（含柱状趋势）
        # ============================================
        dif = float(latest.get('macd_dif', 0))
        dea = float(latest.get('macd_dea', 0))
        bar = float(latest.get('macd_bar', 0))
        prev_bar = float(prev.get('macd_bar', 0))
        prev2_bar = float(df['macd_bar'].iloc[-3]) if len(df) >= 3 else prev_bar

        # MACD状态细化
        if dif > dea:
            if bar > prev_bar:
                macd_state = '金叉红柱·放大'
            elif bar > 0:
                macd_state = '金叉红柱·缩短'
            else:
                macd_state = '金叉缩柱'
        else:
            if bar < prev_bar and bar < 0:
                macd_state = '死叉绿柱·放大'
            elif bar < 0:
                macd_state = '死叉绿柱·缩短'
            else:
                macd_state = '死叉收柱'

        macd_above_zero = '零轴上' if dif > 0 else '零轴下'

        # 底背离/顶背离简查
        macd_divergence = ''
        if len(df) >= 30:
            recent_30 = df.iloc[-30:]
            # 价格新低但DIF不创新低 = 底背离
            price_low_30 = float(recent_30['close'].min())
            dif_low_30 = float(recent_30['macd_dif'].min())
            if close <= price_low_30 * 1.02 and dif > dif_low_30 * 1.1:
                macd_divergence = '底背离'

        # ============================================
        # 三、量价关系
        # ============================================
        try:
            vol_ratio = float(latest.get('volume_ratio', 1))
        except (ValueError, TypeError):
            vol_ratio = 1.0
        change_pct = float(latest.get('change_pct', 0))
        vol_state = _vol_price_label(change_pct, vol_ratio)

        # 5日均量 vs 20日均量
        vol_5 = float(df['volume'].iloc[-5:].mean())
        vol_20 = float(df['volume'].iloc[-20:].mean())
        vol_5_20_ratio = round(vol_5 / max(vol_20, 1), 2)

        # ============================================
        # 四、关键位（日K承接体系）
        # ============================================
        recent_60 = df.iloc[-60:]
        recent_sp = df.iloc[-support_period:]
        recent_3 = df.iloc[-3:]

        low_sp = float(recent_sp['low'].min())
        low_3 = float(recent_3['low'].min())
        low_10 = float(df.iloc[-10:]['low'].min())
        high_sp = float(recent_sp['high'].max())
        high_20 = float(df.iloc[-20:]['high'].max())
        high_60 = float(recent_60['high'].max())

        # === 支撑位计算（日K承接体系） ===
        # 1. 最近涨停大阳收盘价（龙哥：涨停价=重要支撑）
        big_yang_support = _find_recent_big_yang_support(df)

        # 2. MA支撑（周期对应）
        ma_key = f'ma{support_period}' if support_period >= 5 else 'ma5'
        ma_sp = float(latest.get(ma_key, latest.get('ma10', low_sp)))

        # 3. 箱体下沿（最近20日）
        box_low = float(df.iloc[-20:]['low'].min())

        # 综合支撑：取最近且合理的
        supports = []
        for s, label in [
            (big_yang_support, '涨停阳线支撑'),
            (ma_sp, f'MA{support_period}均线'),
            (low_3, '3日低点'),
            (box_low, '箱体下沿'),
        ]:
            if s > 0 and s < close:
                supports.append((s, label))

        # 选择最接近收盘价的支撑位作为主支撑
        if supports:
            supports.sort(key=lambda x: abs(x[0] - close))
            support = supports[0][0]
            support_label = supports[0][1]
        else:
            support = round(close * 0.95, 2)
            support_label = '5%止损线'

        # === 压力位计算 ===
        pressures = []
        for r, label in [
            (high_sp, f'{support_period}日高点'),
            (high_20, '20日高点'),
            (high_60, '60日高点'),
        ]:
            if r > close:
                pressures.append((r, label))
        # 均线反压
        for m in ma_status:
            if not m['above'] and m['ma_price'] > close:
                pressures.append((m['ma_price'], f'MA{m["period"]}反压'))

        if pressures:
            pressures.sort(key=lambda x: x[0])
            resistance = pressures[0][0]
            resistance_label = pressures[0][1]
        else:
            resistance = round(close * 1.08, 2)
            resistance_label = '8%上行目标'

        # ============================================
        # 五、筹码成本分析
        # ============================================
        chip_info = {}
        try:
            from .chip import calc_cost_analysis
            chip_info = calc_cost_analysis(df)
        except Exception:
            chip_info = {'avg_cost': 0, 'cost_support': 0, 'chip_pressure': 0,
                         'above_ratio': 0, 'concentration': 0}

        # ============================================
        # 六、日K承接判断
        # ============================================
        support_quality = _judge_daily_support(df, close, support, big_yang_support)

        return {
            'close': round(close, 2),
            'change_pct': round(change_pct, 1),
            'ma_status': ma_status,
            'above_ma_count': _native(above_count),
            'ma_arrangement': ma_arrangement,
            'ma_summary': ma_summary,
            'macd_state': macd_state,
            'macd_zone': macd_above_zero,
            'macd_divergence': macd_divergence,
            'vol_state': vol_state,
            'vol_ratio': round(vol_ratio, 1),
            'vol_5_20_ratio': vol_5_20_ratio,
            'support': round(support, 2),
            'support_label': support_label,
            'resistance': round(resistance, 2),
            'resistance_label': resistance_label,
            'atr_pct': round((resistance - support) / max(close, 0.01) * 100, 1),
            'support_quality': support_quality,
            'chip_avg_cost': chip_info.get('avg_cost', 0),
            'chip_concentration': chip_info.get('concentration', 0),
            'chip_above_ratio': chip_info.get('above_ratio', 0),
        }
    except Exception as e:
        print(f'[pick_analysis] {code} 技术分析失败: {e}')
        return _empty_tech()


def _empty_tech() -> Dict:
    return {
        'close': 0, 'change_pct': 0,
        'ma_status': [], 'above_ma_count': 0,
        'ma_arrangement': 'N/A', 'ma_summary': '',
        'macd_state': 'N/A', 'macd_zone': '',
        'macd_divergence': '',
        'vol_state': 'N/A', 'vol_ratio': 0, 'vol_5_20_ratio': 0,
        'support': 0, 'support_label': '',
        'resistance': 0, 'resistance_label': '',
        'atr_pct': 0, 'support_quality': '',
        'chip_avg_cost': 0, 'chip_concentration': 0, 'chip_above_ratio': 0,
    }


# ------------------------------------------------------------------
# 均线判断
# ------------------------------------------------------------------
def _judge_ma_arrangement(ma_status: List[Dict]) -> str:
    """判断均线排列：多头/空头/缠绕"""
    above_list = [(m['period'], m['above'], m['ma_price'])
                  for m in sorted(ma_status, key=lambda x: x['period'])]
    if len(above_list) < 3:
        return '数据不足'

    # 检查是否多头排列（短均线在长均线上方，且价格站上所有均线）
    prices = [a[2] for a in above_list if a[2] > 0]
    if len(prices) >= 3:
        is_bull = all(prices[i] > prices[i+1] for i in range(len(prices)-1))
        if is_bull and all(a[1] for a in above_list):
            return '多头排列'

    # 空头排列
    is_bear = all(prices[i] < prices[i+1] for i in range(len(prices)-1))
    if is_bear:
        return '空头排列'

    # 部分站上
    above_count = sum(1 for a in above_list if a[1])
    if above_count >= len(above_list) * 0.6:
        return '偏多整理'
    elif above_count <= len(above_list) * 0.3:
        return '偏空整理'
    return '均线缠绕'


def _ma_summary_text(ma_status: List[Dict], above_count: int) -> str:
    """生成均线体系简述（龙哥口诀风格）"""
    if not ma_status:
        return ''

    parts = []
    # 5/10日线状态
    ma5 = next((m for m in ma_status if m['period'] == 5), None)
    ma10 = next((m for m in ma_status if m['period'] == 10), None)

    if ma5 and ma10:
        if ma5['above'] and ma10['above']:
            parts.append('5/10日线支撑有效')
        elif not ma5['above']:
            parts.append('5日线破位·短线风险')
        elif ma5['above'] and not ma10['above']:
            parts.append('站5日线·10日线反压')

    # 20日线
    ma20 = next((m for m in ma_status if m['period'] == 20), None)
    if ma20:
        if ma20['above']:
            parts.append('20日线向上·中线健康')
        else:
            parts.append('20日线反压·中线承压')

    # 总结站上几条均线
    if above_count >= 5:
        parts.append(f'站上{above_count}线·强势')
    elif above_count >= 3:
        parts.append(f'站上{above_count}线·偏强')

    return '；'.join(parts) if parts else '均线信号中性'


# ------------------------------------------------------------------
# 量价标签
# ------------------------------------------------------------------
def _vol_price_label(change_pct: float, vol_ratio: float) -> str:
    if change_pct > 3 and vol_ratio > 2.0:
        return '放量暴涨'
    elif change_pct > 2 and vol_ratio > 1.5:
        return '放量拉升'
    elif change_pct > 0 and vol_ratio > 1.0:
        return '温和放量'
    elif change_pct > 0 and vol_ratio < 0.7:
        return '缩量上涨(背离)'
    elif change_pct < -3 and vol_ratio > 2.0:
        return '放量暴跌'
    elif change_pct < -2 and vol_ratio > 1.5:
        return '放量下杀'
    elif change_pct < 0 and vol_ratio < 0.7:
        return '缩量回调'
    else:
        return '量价平稳'


# ------------------------------------------------------------------
# 日K承接判断
# ------------------------------------------------------------------
def _find_recent_big_yang_support(df, lookback: int = 20) -> float:
    """找最近的大阳线/涨停板收盘价作为支撑位"""
    if len(df) < lookback:
        return 0
    seg = df.iloc[-lookback:]
    for i in range(len(seg) - 1, -1, -1):
        row = seg.iloc[i]
        try:
            change = float(row.get('change_pct', 0))
            if abs(change) >= 5:  # 大阳或涨停
                return float(row['close'])
        except (ValueError, TypeError):
            continue
    return 0


def _judge_daily_support(df, close: float, support: float,
                          yang_support: float) -> str:
    """判断日K承接质量（龙哥承接战法）

    关键判断：
    - 连续多日不破涨停价/支撑位 = 承接强
    - 跌破后形成新箱体 = 看新箱体下沿
    - 承接好：总跌不下去、跌下去能拉回来、总跌不到你想买的价
    """
    if len(df) < 5:
        return '数据不足'

    recent_5 = df.iloc[-5:]
    lows = [float(x) for x in recent_5['low'].values]

    # 连续不破支撑
    breaks_support = any(low < support * 0.98 for low in lows)

    if not breaks_support:
        # 低点是否逐步抬升（承接好的标志）
        if len(lows) >= 3:
            low_halves = [min(lows[:len(lows)//2]), min(lows[len(lows)//2:])]
            if low_halves[-1] > low_halves[0]:
                return '承接强·低点抬升'
        return '承接良好·支撑有效'

    # 有跌破但收回来了
    if yang_support > 0 and close > yang_support:
        # 盘中跌破但收盘站回
        return '盘中探底回升·承接尚可'

    return '支撑告破·等待企稳'


# ------------------------------------------------------------------
# 操作建议（龙哥实战体系）
# ------------------------------------------------------------------
def _trading_suggestion(strategy: str, tech: Dict, detail: Dict) -> Dict:
    """根据策略类型和盘面状态给出操作建议"""
    close = tech.get('close', 0)
    support = tech.get('support', 0)
    resistance = tech.get('resistance', 0)
    above_ma = tech.get('above_ma_count', 0)
    macd_state = tech.get('macd_state', '')
    vol_ratio = tech.get('vol_ratio', 1)
    chip_concentration = tech.get('chip_concentration', 0)
    chip_above_ratio = tech.get('chip_above_ratio', 0)
    ma_arrangement = tech.get('ma_arrangement', '')
    vol_5_20 = tech.get('vol_5_20_ratio', 1)

    if close <= 0:
        return _empty_suggestion()

    # --- 入场区间（次日开盘价附近 ±2%）---
    entry_low = round(close * 0.99, 2)
    entry_high = round(close * 1.02, 2)

    # --- 止损位 ---
    # 实战止损体系：优先用最近的均线支撑，均线太远就用固定比例
    max_stop_pct = 0.06  # 默认最多亏6%
    min_stop_pct = 0.04  # 最少要有4%空间（太紧会被噪音扫掉）

    stop_loss = 0
    # 1. 优先：找最近的站上均线（MA5/MA10），如果距现价≤6%就用
    for p in [5, 10]:
        ma = next((m for m in tech.get('ma_status', []) if m['period'] == p), None)
        if ma and ma['above'] and ma['ma_price'] > 0:
            dist_pct = (close - ma['ma_price']) / close
            if dist_pct <= max_stop_pct + 0.01:
                # 均线在合理范围内，用它
                stop_loss = round(ma['ma_price'] * 0.995, 2)
                break

    # 2. 回退：用固定比例止损
    if stop_loss <= 0 or stop_loss >= close:
        stop_loss = round(close * (1 - min_stop_pct - 0.01), 2)  # -5% default

    # 3. 兜底：确保在合理范围
    if stop_loss < close * (1 - max_stop_pct - 0.02):
        stop_loss = round(close * (1 - max_stop_pct), 2)
    if stop_loss >= close:
        stop_loss = round(close * 0.95, 2)

    # --- 目标位 ---
    target = round(resistance, 2) if resistance > close else round(close * 1.08, 2)
    # 如果筹码集中度高，目标可以看高一线
    if chip_concentration > 0.65 and chip_above_ratio > 50:
        target = round(max(target, close * 1.12), 2)

    # --- 风险收益比 ---
    risk = round((close - stop_loss) / close * 100, 1)
    reward = round((target - close) / close * 100, 1)
    rr_ratio = round(reward / max(risk, 0.01), 1)

    # --- 仓位建议 ---
    position = _position_advice(above_ma, macd_state, rr_ratio,
                                 ma_arrangement, vol_5_20)

    # --- 策略定制 ---
    strategy_tips = {
        'auction': (
            '竞价抢筹股波动大，开盘观察是否守住竞价价格。'
            '若回落超2%则放弃；若守住且放量上攻可加仓。'
            '分时均线是主力成本线，回踩不破是买点。'
        ),
        'one_two': (
            '1进2核心是次日竞价确认。弱于预期=直接放弃；'
            '符合预期=看前15分钟量能和分时承接；'
            '超预期(烂板高开)=弱转强买点。'
            '开盘后换手充分(前15分钟达昨日30%)且价格维持在均线上方可持有。'
        ),
        'leader': (
            '龙头股关注换手是否健康。连续缩量一字板需警惕开板即顶点。'
            '换手板上位的龙头更有持续性。'
            '总龙头可给弱转强修复预期；中位股(3-4板)弱于预期必须第一时间走。'
        ),
        'breakout': (
            '突破形态需次日确认站稳突破位。假突破(次日跌回箱体)必须止损。'
            '均线信号需量能验证：均线向好但缩量=大概率假突破。'
            '突破后回踩均线不破是最佳加仓点。'
        ),
        'institution': (
            '机构票波动较稳，可分批建仓。短线目标3-5%，持有周期3-5天。'
            '放量滞涨+底部筹码松动=主力出货信号，应止盈。'
        ),
        'sector_divergence': (
            '板块分歧介入需等待回调到支撑位再动手，追高容易被套。'
            '分歧日低吸核心龙头，不要碰跟风后排。'
            '分时均线下方偏离太多时可适当买入(均线原理：价格向均线回归)。'
        ),
    }

    # --- 风险提示 ---
    # 结合均线+MACD+筹码综合判断
    if '金叉' in macd_state and above_ma >= 4 and ma_arrangement == '多头排列':
        risk_note = '趋势全面向好，但需注意前高压力位附近的获利抛压。'
    elif '金叉' in macd_state and above_ma >= 3:
        risk_note = '趋势向好，可积极操作。严格按止损执行。'
    elif '死叉' in macd_state and above_ma <= 1:
        risk_note = '趋势偏弱，仅适合短线快进快出，隔日不强即走。'
    elif chip_above_ratio >= 90:
        risk_note = '⚠获利盘>90%，警惕主力随时派发。设宽幅止损。'
    elif '底背离' in tech.get('macd_divergence', ''):
        risk_note = 'MACD底背离信号，可能有反弹机会，轻仓试错。'
    elif vol_ratio < 0.7 and close > 0:
        risk_note = '缩量环境下信号可靠性降低，等待放量确认后再动手。'
    else:
        risk_note = '信号中性，严格按止损执行，不破位可持有。'

    return {
        'entry_zone': f'{_native(entry_low)}-{_native(entry_high)}',
        'stop_loss': _native(stop_loss),
        'target': _native(target),
        'risk_pct': _native(risk),
        'reward_pct': _native(reward),
        'rr_ratio': _native(rr_ratio),
        'position': position,
        'strategy_tip': strategy_tips.get(strategy, '严格止损，破位即走。'),
        'risk_note': risk_note,
    }


def _empty_suggestion() -> Dict:
    return {
        'entry_zone': '', 'stop_loss': 0, 'target': 0,
        'risk_pct': 0, 'reward_pct': 0, 'rr_ratio': 0,
        'position': '', 'strategy_tip': '', 'risk_note': '数据不足，无法生成建议',
    }


def _position_advice(above_ma: int, macd_state: str, rr_ratio: float,
                      ma_arrangement: str = '', vol_5_20: float = 1.0) -> str:
    """仓位建议（龙哥体系：综合多因子）"""
    score = 0

    # 均线因子
    if above_ma >= 5:
        score += 3
    elif above_ma >= 3:
        score += 2
    elif above_ma >= 1:
        score += 1
    else:
        score -= 1

    # 均线排列
    if ma_arrangement == '多头排列':
        score += 2
    elif ma_arrangement == '空头排列':
        score -= 2

    # MACD因子
    if '金叉' in macd_state:
        score += 2
    elif '死叉' in macd_state:
        score -= 1

    # 量能因子（均线信号需量能验证）
    if vol_5_20 >= 1.5:
        score += 2
    elif vol_5_20 >= 1.2:
        score += 1
    elif vol_5_20 < 0.7:
        score -= 1

    # 盈亏比因子
    if rr_ratio >= 3:
        score += 2
    elif rr_ratio >= 2:
        score += 1
    elif rr_ratio < 1.0:
        score -= 2
    elif rr_ratio < 1.5:
        score -= 1

    if score >= 7:
        return '标准仓(7-8成)'
    elif score >= 5:
        return '半仓(5成)'
    elif score >= 3:
        return '轻仓(3成)'
    elif score >= 1:
        return '观察仓(1-2成)'
    else:
        return '空仓观望'


# ------------------------------------------------------------------
# 多Agent共识分析（需要 LLM API Key）
# ------------------------------------------------------------------
def analyze_with_agents(code: str, strategy: str = 'leader',
                        detail: dict = None) -> dict:
    """使用多Agent共识替代纯规则分析 — 需要 LLM API Key

    返回结构兼容现有的 {tech, suggestion} 格式，
    同时在 suggestion 中注入 agent_opinions。
    """
    # 先做基础技术面分析（不需要LLM，始终可用）
    from ..data.tdx_reader import TdxReader
    tdx = TdxReader()
    tech = _tech_analysis(code, tdx, support_period=10)

    # 尝试 Agent 分析
    try:
        from ..agents.orchestrator import SwarmOrchestrator
        import asyncio, json

        name = code
        # 从名称映射获取股票名（不直接依赖 web.app）
        try:
            from ..screening.base import BaseScreener
            screener = BaseScreener.__new__(BaseScreener)
            screener.__init__()
            n = screener._get_name(code)
            if n:
                name = n
        except Exception:
            pass

        orch = SwarmOrchestrator()
        context = json.dumps(detail or {}, ensure_ascii=False)
        loop = asyncio.new_event_loop()
        plan = loop.run_until_complete(orch.analyze_stock(code, name, context))
        loop.close()

        suggestion = {
            'action': plan.action,
            'entry_zone': [plan.entry_zone_low, plan.entry_zone_high],
            'stop_loss': plan.stop_loss,
            'targets': plan.targets,
            'position_pct': plan.position_pct,
            'risk_level': plan.risk_level,
            'rationale': plan.rationale,
            'agent_opinions': [
                {'agent': o.agent, 'direction': o.direction,
                 'confidence': o.confidence, 'key_points': o.key_points,
                 'risks': o.risks, 'score': o.score}
                for o in plan.agent_opinions
            ],
            'source': 'agent_consensus',
        }
        return {'tech': tech, 'suggestion': suggestion}
    except Exception as e:
        return {'tech': tech, 'suggestion': {
            'action': 'watch',
            'rationale': f'Agent分析不可用: {e}。基础技术面已完成，请查看tech字段。',
            'source': 'rule_based_fallback',
        }}
