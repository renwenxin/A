"""日度复盘报告 — 龙哥复盘体系增强版

在原有涨停统计基础上新增：
- 情绪周期节点判断（启动/发酵/高潮/退潮/冰点）
- 板块涨停潮 + 分歧分析（龙哥题材复盘方法论）
- 竞价预期预测（基于当日走势预判次日竞价）
- 弱转强候选标的（烂板/分歧板次日弱转强潜力）
- 均线体系概况（大盘+主要标的）
- 筹码分布信号（连板龙头的持有/卖出判断）

复盘是收盘后做的事——所有数据在收盘后即可获取。
"""
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
from collections import Counter, defaultdict
from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher
from ..utils.calendar import TradingCalendar


def _is_yizi_board(lu) -> bool:
    """判断是否一字板：封板时间为09:25（集合竞价即封死）"""
    try:
        return str(lu.limit_up_time).replace(':', '')[:4] == '0925'
    except (ValueError, AttributeError, TypeError):
        return False


class DailyReport:
    def __init__(self, tdx: TdxReader = None, ak_fetcher: AkshareFetcher = None):
        self.tdx = tdx or TdxReader()
        self.ak = ak_fetcher or AkshareFetcher()
        self.calendar = TradingCalendar()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def generate(self, trade_date: Optional[str] = None) -> Dict:
        if trade_date is None:
            trade_date = self._resolve_trade_date()
        report_date = self._fmt_date(trade_date)

        print(f"[复盘] 正在获取 {report_date} 数据...")

        # --- 涨停池 ---
        limit_ups = self.ak.get_limit_up_pool(trade_date)
        print(f"[复盘] 涨停池: {len(limit_ups)} 只")

        # --- 龙虎榜 ---
        lhb = self.ak.get_lhb(trade_date)
        print(f"[复盘] 龙虎榜: {len(lhb)} 条")

        # --- 概念/行业板块 ---
        hot_concepts, concepts_fallback = self._get_hot_concepts(trade_date)
        print(f"[复盘] 概念板块: {len(hot_concepts)} 个")

        hot_industries, industries_fallback = self._get_hot_industries(trade_date)
        print(f"[复盘] 行业板块: {len(hot_industries)} 个")

        hot_merged = (concepts_fallback and industries_fallback)

        # --- 大盘表现 ---
        market_overview = self._get_market_overview(trade_date)
        print(f"[复盘] 大盘: 成交额{market_overview['total_volume']:.0f}亿 "
              f"涨{market_overview['up_count']} 跌{market_overview['down_count']}")

        # ==================== 统计汇总 ====================
        total_zt = len(limit_ups)
        sealed = sum(1 for lu in limit_ups if lu.is_seal)
        broken = sum(1 for lu in limit_ups if lu.is_broken)
        first_boards = [lu for lu in limit_ups if lu.is_first]
        multi_boards = [lu for lu in limit_ups if lu.consecutive >= 2]
        max_consecutive = max((lu.consecutive for lu in limit_ups), default=0)

        # --- 涨停时间分布 ---
        time_dist = {'早盘(<10:30)': 0, '上午(10:30-11:30)': 0, '下午': 0}
        for lu in limit_ups:
            try:
                t_str = str(lu.limit_up_time).replace(':', '')[:4]
                t = int(t_str) if t_str.isdigit() else 0
                if 0 < t <= 1030:
                    time_dist['早盘(<10:30)'] += 1
                elif t <= 1130:
                    time_dist['上午(10:30-11:30)'] += 1
                else:
                    time_dist['下午'] += 1
            except (ValueError, TypeError):
                time_dist['下午'] += 1

        # --- 龙虎榜排序 ---
        lhb_sorted = self._sort_lhb(lhb)

        # ==================== 新增：板块涨停潮分析 ====================
        sector_analysis = self._analyze_sectors(limit_ups)

        # ==================== 新增：情绪周期节点判断 ====================
        cycle = self._detect_cycle_stage(limit_ups, market_overview)

        # ==================== 新增：弱转强候选 ====================
        weak_to_strong = self._find_weak_to_strong_candidates(limit_ups)

        # ==================== 短线情绪（增强版） ====================
        sentiment = self._generate_sentiment(limit_ups, market_overview, cycle)

        # ==================== 竞价预期预测 ====================
        auction_forecast = self._forecast_next_auction(limit_ups, cycle)

        # ==================== 筹码分布信号 ====================
        chip_signals = self._analyze_chip_signals(multi_boards)

        return {
            'date': report_date,
            'is_trading_day': self.calendar.is_trading_day(
                datetime.strptime(trade_date, '%Y%m%d').date()),
            'total_limit_ups': total_zt,
            'sealed': sealed,
            'broken': broken,
            'seal_rate': f'{sealed/max(total_zt,1)*100:.1f}%',
            'first_boards': len(first_boards),
            'multi_boards': len(multi_boards),
            'max_consecutive': max_consecutive,
            'time_distribution': time_dist,
            'market_overview': market_overview,
            'hot_concepts': hot_concepts,
            'hot_industries': hot_industries,
            'hot_merged': hot_merged,
            # 连板梯队
            'multi_board_list': [{
                'code': lu.code, 'name': lu.name,
                'consecutive': lu.consecutive, 'board_type': lu.board_type,
                'limit_up_time': lu.limit_up_time,
                'seal_amount': round(lu.seal_amount, 0),
                'turnover_yi': round(lu.turnover / 10000, 1) if lu.turnover else 0,
            } for lu in sorted(multi_boards, key=lambda x: x.consecutive, reverse=True)],
            # 龙虎榜 Top 10
            'top_lhb': [{
                'code': l.code, 'name': l.name, 'reason': l.reason,
                'net_amount': l.net_amount,
                'buy_amount': l.buy_amount,
                'sell_amount': l.sell_amount,
            } for l in lhb_sorted],
            # 新增字段
            'sentiment': sentiment,
            'cycle': cycle,
            'sector_analysis': sector_analysis,
            'weak_to_strong': weak_to_strong,
            'auction_forecast': auction_forecast,
            'chip_signals': chip_signals,
        }

    # ==================================================================
    # LLM 综述
    # ==================================================================
    def build_summary_prompt(self, data: dict) -> str:
        """基于复盘数据构建 LLM 综述 prompt"""
        total = data.get('total_limit_ups', 0)
        sealed = data.get('sealed_count', data.get('sealed', 0))
        seal_rate = data.get('seal_rate', 0)
        max_consec = data.get('max_consecutive', 0)

        # 市场涨跌比 & 成交额
        market = data.get('market_overview', {})
        market_up = data.get('market_up', market.get('up_count', 0))
        market_down = data.get('market_down', market.get('down_count', 0))
        amount = data.get('total_amount_yi', market.get('total_volume', 0))

        # 情绪 & 竞价
        cycle = data.get('cycle', {})
        sentiment = data.get('sentiment_node', cycle.get('stage', ''))
        auction_data = data.get('auction_forecast', {})
        auction = data.get('auction_mood', auction_data.get('forecast', ''))

        # 热点板块
        sectors = data.get('hot_sectors', [])
        if not sectors:
            sector_analysis = data.get('sector_analysis', {})
            sectors = sector_analysis.get('hot_sectors', [])
        sector_text = ', '.join([f"{s.get('name', '')}({s.get('count', s.get('zt_count', 0))}只涨停)"
                                for s in sectors[:5]])

        # 连板梯队
        ladder = data.get('ladder', [])
        if not ladder:
            multi_list = data.get('multi_board_list', [])
            from collections import Counter
            cons_counter = Counter(m.get('consecutive', 0) for m in multi_list)
            ladder = [{'consecutive': k, 'count': v}
                      for k, v in sorted(cons_counter.items(), reverse=True)]
        ladder_text = ', '.join([f"{l.get('consecutive', 0)}板:{l.get('count', 0)}只"
                                for l in ladder[:5]])

        prompt = f"""请基于以下A股今日复盘数据，生成一份简洁的市场综述（Markdown格式，约300字）。

## 市场数据
- 涨停总数: {total}只，封板: {sealed}只，封板率: {seal_rate}%
- 最高连板: {max_consec}板
- 涨跌比: {market_up}:{market_down}
- 成交额: {amount:.0f}亿

## 热点板块
{sector_text}

## 连板梯队
{ladder_text}

## 情绪判断
- 情绪阶段: {sentiment}
- 竞价预判: {auction}

请按以下5个维度输出：

📊 **市场总览**: 今日整体定性（1-2句）

🔥 **热点板块**: 主线板块识别+持续性判断（1-2句）

📈 **情绪周期**: 当前阶段+次日大概率走向（1-2句）

⚡ **竞价预期**: 次日竞价氛围预判+关注方向（1-2句）

🎯 **操作建议**: 仓位建议+关注方向（1-2句）
"""
        return prompt

    def generate_llm_summary(self, trade_date=None) -> str:
        """生成 LLM 市场综述 — 需要 LLM Provider 可用"""
        data = self.generate(trade_date)
        prompt = self.build_summary_prompt(data)

        try:
            from ..agents.providers import create_provider
            provider = create_provider()
            result = provider.chat_sync([
                {'role': 'system', 'content': '你是A股资深复盘分析师，擅长提炼市场要点。'},
                {'role': 'user', 'content': prompt},
            ], temperature=0.3, max_tokens=1024)
            return result
        except Exception as e:
            return f'LLM综述生成失败: {e}\n\n请确认已设置 API Key（DEEPSEEK_API_KEY 或 ANTHROPIC_API_KEY）'

    # ------------------------------------------------------------------
    # 日期处理
    # ------------------------------------------------------------------
    def _resolve_trade_date(self) -> str:
        today = date.today()
        if self.calendar.is_trading_day(today):
            return today.strftime('%Y%m%d')
        d = today
        for _ in range(10):
            d = d - timedelta(days=1)
            if self.calendar.is_trading_day(d):
                return d.strftime('%Y%m%d')
        return today.strftime('%Y%m%d')

    @staticmethod
    def _fmt_date(ymd: str) -> str:
        try:
            d = datetime.strptime(ymd, '%Y%m%d')
            return d.strftime('%Y-%m-%d')
        except ValueError:
            return ymd

    # ==================================================================
    # 新增：板块分析（龙哥题材复盘方法论）
    # ==================================================================
    def _analyze_sectors(self, limit_ups: List) -> Dict:
        """按板块/题材聚合涨停股，识别涨停潮和分歧信号

        龙哥方法论：
        - 建立Excel文档，将当日涨停票从一板到最高板按题材分类
        - 重点关注一板中新题材（多股涨停的）
        - 板块内涨停≥5只 = 涨停潮
        - 找到涨停潮的板块龙头
        """
        # 按 sector (board_type) 聚合
        sector_map: Dict[str, List] = defaultdict(list)
        for lu in limit_ups:
            sec = lu.board_type or '未分类'
            sector_map[sec].append(lu)

        sectors = []
        for sec_name, stocks in sector_map.items():
            count = len(stocks)
            if count < 2:
                continue

            # 一板中是否有新题材
            first_in_sector = [s for s in stocks if s.is_first]
            multi_in_sector = [s for s in stocks if s.consecutive >= 2]
            max_cons = max((s.consecutive for s in stocks), default=1)

            # 找板块龙头（连板最高、封板最早）
            leader = max(stocks, key=lambda s: (s.consecutive, -int(
                str(s.limit_up_time).replace(':', '')[:4] or '1500')))

            # 板型分布
            board_types = Counter(s.board_type for s in stocks)

            # 判断强度
            if count >= 8:
                strength = '涨停潮🔥'
            elif count >= 5:
                strength = '强势'
            elif count >= 3:
                strength = '活跃'
            else:
                strength = '普通'

            sectors.append({
                'name': sec_name,
                'zt_count': count,
                'strength': strength,
                'first_count': len(first_in_sector),
                'multi_count': len(multi_in_sector),
                'max_consecutive': max_cons,
                'leader_code': leader.code,
                'leader_name': leader.name,
                'leader_cons': leader.consecutive,
                'board_types': dict(board_types),
                'is_new_theme': len(first_in_sector) >= 3 and max_cons <= 2,
            })

        # 按涨停数排序
        sectors.sort(key=lambda x: x['zt_count'], reverse=True)

        # 涨停潮板块（≥5只）
        hot_sectors = [s for s in sectors if s['zt_count'] >= 5]
        # 新题材（多只首板的新板块）
        new_themes = [s for s in sectors if s['is_new_theme']]

        return {
            'all_sectors': sectors[:15],
            'hot_sectors': hot_sectors,
            'new_themes': new_themes,
            'sector_count': len(sectors),
        }

    # ==================================================================
    # 新增：情绪周期节点判断（龙哥周期转换体系）
    # ==================================================================
    def _detect_cycle_stage(self, limit_ups: List, market: Dict) -> Dict:
        """判断当前处于周期的哪个阶段

        周期阶段:
        - 启动/发酵: 新题材出现、空间板出现、赚钱效应开始
        - 高潮/疯狂: 一字板加速、量能天量、后排掉队、低位补涨
        - 退潮/分歧: 龙头见顶、核按钮出现、高度压制、强势股补跌
        - 冰点/绝望: 连板极低(2-3板)、成交量萎缩、百股跌停

        信号体系:
        - 赚钱效应出现 → 启动期
        - 加速一致 + 后排掉队 → 高潮末期
        - 龙头见顶 + 核按钮 → 退潮期
        - 连板极低 + 交投清淡 → 冰点期
        """
        total = len(limit_ups)
        sealed = sum(1 for lu in limit_ups if lu.is_seal)
        broken_count = sum(1 for lu in limit_ups if lu.is_broken)
        seal_rate = sealed / max(total, 1) * 100
        max_cons = max((lu.consecutive for lu in limit_ups), default=0)
        first_count = sum(1 for lu in limit_ups if lu.is_first)

        # 一字板数量（加速信号）—— 用封板时间=09:25判断
        yizi_count = sum(1 for lu in limit_ups
                         if _is_yizi_board(lu))
        yizi_ratio = yizi_count / max(total, 1)

        # 判断
        if total >= 100 and seal_rate >= 80 and max_cons >= 6:
            if yizi_ratio >= 0.3:
                # 一字板占比过高 = 加速一致，可能高潮末期
                stage = '高潮末期⚠'
                stage_desc = (
                    '涨停家数充足但一字板占比过高({:.0f}%)，属于加速一致阶段。'
                    '后排跟风股开始冲高回落无法封板时即为高潮见顶信号。'
                    '此时应去弱留强，中位股必须清仓，仅持有总龙头或空仓等待分歧。'
                ).format(yizi_ratio * 100)
                action = '去弱留强，卖出中位跟风股，仅保留总龙头底仓'
                risk_level = '中高'
            else:
                stage = '高潮期'
                stage_desc = (
                    '赚钱效应爆棚，板块全面爆发。封板率{:.0f}%，最高{}连板。'
                    '警惕放量滞涨和低位补涨信号（周期末段特征）。'
                    '核心龙头仍可持有，但不要再重仓追高中位标的。'
                ).format(seal_rate, max_cons)
                action = '持股为主，关注龙头开板信号和后排掉队情况'
                risk_level = '中'
        elif total >= 50 and seal_rate >= 70 and max_cons >= 4:
            stage = '发酵期'
            stage_desc = (
                '赚钱效应明显增强，涨停家数充足，连板梯队逐渐成形。'
                '板块联动良好，是短线操作的最佳阶段。'
                '重点关注1进2和2进3接力机会。'
            )
            action = '积极操作，重点做1进2和2进3接力'
            risk_level = '低'
        elif total >= 30 and seal_rate >= 60 and max_cons >= 3:
            stage = '启动期'
            stage_desc = (
                '市场开始出现赚钱效应，新题材萌发。'
                '首板数量增多，关注新出现的一板题材（多股涨停的）。'
                '此时是试错阶段，轻仓参与前排标的。'
            )
            action = '轻仓试错，关注新题材首板和一进二机会'
            risk_level = '中低'
        elif total < 15 or seal_rate < 40:
            stage = '冰点期'
            stage_desc = (
                '市场情绪降至谷底，涨停家数稀少仅{}只，封板率仅{:.0f}%。'
                '连板高度被压至{}板，恐慌情绪浓厚。'
                '但冰点往往意味着否极泰来——如果出现新题材超预期竞价，可能是新周期萌芽。'
                '此时应空仓或极小仓位试错首板启动。'
            ).format(total, seal_rate, max_cons)
            action = '空仓观望或极小仓位试错新题材首板'
            risk_level = '极高'
        elif seal_rate < 55 or (broken_count > 0 and broken_count / max(total, 1) > 0.3):
            stage = '退潮期'
            stage_desc = (
                '赚钱效应减弱，炸板率上升至{:.0f}%。龙头出现分歧，'
                '跟风股开始补跌。该强不强的标的必须第一时间离场。'
                '核按钮出现时确认退潮。'
            ).format(broken_count / max(total, 1) * 100)
            action = '减仓防守，卖出中位股和跟风股，不开新仓'
            risk_level = '高'
        else:
            stage = '震荡期'
            stage_desc = (
                '市场情绪平稳，资金观望情绪较浓。涨停{}只，封板率{:.0f}%。'
                '适合控仓参与，去弱留强。'
            ).format(total, seal_rate)
            action = '控仓参与，聚焦前排核心标的'
            risk_level = '中'

        # 连板高度变化（与近期对比）
        height_analysis = self._analyze_height_trend(limit_ups, max_cons)

        return {
            'stage': stage,
            'stage_desc': stage_desc.strip(),
            'action': action,
            'risk_level': risk_level,
            'metrics': {
                'total_zt': total,
                'seal_rate': round(seal_rate, 1),
                'max_consecutive': max_cons,
                'yizi_ratio': round(yizi_ratio * 100, 1),
                'first_boards': first_count,
                'height_trend': height_analysis,
            }
        }

    def _analyze_height_trend(self, limit_ups: List, current_max: int) -> str:
        """分析连板高度趋势"""
        if current_max >= 7:
            return '高度空间打开，连板梯队完整，短线可操作性高'
        elif current_max >= 5:
            return '高度适中，关注是否有标的突破6板打开新空间'
        elif current_max >= 3:
            return '高度偏低，等待空间突破后再加大仓位'
        else:
            return '高度受压制，需等待破局者出现'

    # ==================================================================
    # 新增：弱转强候选
    # ==================================================================
    def _find_weak_to_strong_candidates(self, limit_ups: List) -> List[Dict]:
        """找弱转强候选标的

        弱转强条件（龙哥）：
        1. 前一日是烂板/分歧板/尾盘板 → 弱
        2. 次日竞价高开3%以上 + 竞价量>昨日5% → 转强
        3. 必须是核心题材的人气股，不能是跟风后排

        盘后预判：筛选出今日分歧但明天可能弱转强的标的
        """
        candidates = []
        for lu in limit_ups:
            score = 0
            reasons = []

            # 必须是封死的（炸板不回的排除）
            if not lu.is_seal:
                continue
            # 排除一字板（一字没有弱转强一说）
            if _is_yizi_board(lu):
                continue

            # 分歧信号的标的才纳入（烂板、炸板回封、尾盘板、分歧较大）
            is_weak = False

            if lu.is_broken:
                is_weak = True
                reasons.append('炸板回封')
                score += 2

            # 尾盘板（14:00以后封板）
            try:
                t_str = str(lu.limit_up_time).replace(':', '')[:4]
                t = int(t_str) if t_str.isdigit() else 1200
                if t >= 1400:
                    is_weak = True
                    reasons.append('尾盘封板')
                    score += 1
            except (ValueError, TypeError):
                pass

            # 封成比低（分歧大）
            if lu.turnover > 0 and lu.seal_amount / lu.turnover < 0.3:
                is_weak = True
                reasons.append(f'分歧板(封成比{lu.seal_amount/lu.turnover:.2f})')
                score += 1

            if not is_weak:
                continue

            # 判断是否有弱转强潜力
            # 流通市值适中
            if 10 <= lu.float_market_cap <= 100:
                score += 1
                reasons.append('市值适中')

            # 连板股更有辨识度
            if lu.consecutive >= 2:
                score += 2
                reasons.append(f'{lu.consecutive}连板·高辨识度')

            # 板块有支撑
            sector_stocks = [s for s in limit_ups
                             if s.board_type == lu.board_type]
            if len(sector_stocks) >= 3:
                score += 1
                reasons.append(f'{lu.board_type}板块有支撑')

            # 非ST
            if score >= 3:
                candidates.append({
                    'code': lu.code, 'name': lu.name,
                    'consecutive': lu.consecutive,
                    'board_type': lu.board_type,
                    'seal_amount': round(lu.seal_amount, 0),
                    'weak_signal': '；'.join(reasons[:3]),
                    'wts_score': score,
                    # 次日弱转强确认条件
                    'confirm_condition': (
                        f'次日竞价高开≥3% + 竞价量>昨日爆量5% + '
                        f'竞价额>1000万 = 弱转强确认买点'
                    ),
                })

        candidates.sort(key=lambda x: x['wts_score'], reverse=True)
        return candidates[:6]

    # ==================================================================
    # 新增：竞价预期预测
    # ==================================================================
    def _forecast_next_auction(self, limit_ups: List, cycle: Dict) -> Dict:
        """基于当日走势预判次日竞价整体氛围

        龙哥逻辑：
        - 前一日封板越硬 → 次日竞价溢价越高
        - 竞价本质是隔夜情绪的延续与修正
        - 早盘秒板>上午板>下午板>尾盘板，次日溢价递减
        - 封单巨大(封单/成交额>20%) → 次日大概率一字板
        - 巨量烂板 → 次日低开预期
        """
        # 按板型分组统计
        early_sealed = 0  # 10点前封板
        morning_sealed = 0  # 10-11:30封板
        afternoon_sealed = 0  # 下午封板
        yizi_count = 0
        broken_count = 0
        strong_multi = []  # 强势连板（次日可能一字）

        for lu in limit_ups:
            if _is_yizi_board(lu):
                yizi_count += 1
            if lu.is_broken:
                broken_count += 1

            # 封板时间
            try:
                t_str = str(lu.limit_up_time).replace(':', '')[:4]
                t = int(t_str) if t_str.isdigit() else 1400
                if t <= 1000:
                    early_sealed += 1
                elif t <= 1130:
                    morning_sealed += 1
                else:
                    afternoon_sealed += 1
            except (ValueError, TypeError):
                afternoon_sealed += 1

            # 强势连板（缩量加速板 + 封单巨大）
            if lu.consecutive >= 2 and lu.turnover > 0:
                seal_ratio = lu.seal_amount / lu.turnover
                if seal_ratio > 1.0:  # 封单大于成交额 = 极强
                    strong_multi.append({
                        'code': lu.code, 'name': lu.name,
                        'consecutive': lu.consecutive,
                        'seal_ratio': round(seal_ratio, 1),
                    })

        total_valid = max(early_sealed + morning_sealed + afternoon_sealed, 1)
        early_ratio = early_sealed / total_valid

        # 预测次日整体竞价氛围
        if yizi_count >= 10 and early_ratio >= 0.5:
            forecast = '火爆'
            forecast_desc = '今日一字板众多+早盘秒板占比高，次日竞价大概率延续强势。一字龙可能继续一字，换手龙预期高开5%+。注意9:20后不能撤单的量才是真实量。'
        elif early_ratio >= 0.4 and len(limit_ups) >= 50:
            forecast = '偏强'
            forecast_desc = '早盘封板占比较高，次日竞价整体偏强，多数涨停股预期高开。重点关注板块龙头的竞价表现来判断板块持续性。'
        elif early_ratio >= 0.2:
            forecast = '中性'
            forecast_desc = '上午下午板参半，次日竞价分化概率大。板块龙头可能高开，跟风股大概率平低开。需在竞价时仔细辨别。'
        elif broken_count > len(limit_ups) * 0.2:
            forecast = '偏弱'
            forecast_desc = '炸板率偏高，次日竞价可能承压。关注炸板回封的标的是否有修复（竞价高开=超预期）。'
        else:
            forecast = '观望'
            forecast_desc = '涨停力度不足，次日竞价大概率平淡。等竞价结束后看清方向再动手。'

        return {
            'forecast': forecast,
            'forecast_desc': forecast_desc,
            'early_sealed': early_sealed,
            'morning_sealed': morning_sealed,
            'afternoon_sealed': afternoon_sealed,
            'yizi_count': yizi_count,
            'strong_multi': strong_multi[:5],  # 次日可能一字或高开的连板股
        }

    # ==================================================================
    # 新增：筹码分布信号
    # ==================================================================
    def _analyze_chip_signals(self, multi_boards: List) -> List[Dict]:
        """对连板股做筹码分布分析"""
        signals = []
        # 只分析前10个连板股（筹码计算较慢）
        for lu in sorted(multi_boards, key=lambda x: x.consecutive, reverse=True)[:10]:
            try:
                market = 'sh' if lu.code.startswith('6') else 'sz'
                if lu.code.startswith('8') or lu.code.startswith('4'):
                    market = 'bj'
                df = self.tdx.read_daily(lu.code, market)
                if len(df) < 60:
                    continue
                from ..analysis.chip import detect_chip_patterns
                patterns = detect_chip_patterns(df)
                for p in patterns:
                    if p['signal'] in ('买入', '卖出', '警示'):
                        signals.append({
                            'code': lu.code,
                            'name': lu.name,
                            'consecutive': lu.consecutive,
                            'pattern': p['pattern'],
                            'signal': p['signal'],
                            'confidence': p['confidence'],
                            'description': p['description'],
                            'action': p['action'],
                        })
            except Exception:
                pass
        return signals[:10]

    # ------------------------------------------------------------------
    # 热点板块（保持原有逻辑）
    # ------------------------------------------------------------------
    def _get_hot_concepts(self, trade_date: str = None, top_n: int = 10) -> tuple:
        try:
            df = self.ak.get_concept_boards()
            if df is None or df.empty:
                return self._hot_from_zt_pool(trade_date, top_n), True
            result = self._parse_board_df(df, top_n)
            if result and all(abs(r.get('change_pct', 0)) < 0.001 for r in result):
                print("[复盘] 概念板块涨跌幅全为0，改用涨停池行业分布")
                return self._hot_from_zt_pool(trade_date, top_n), True
            return result, False
        except Exception as e:
            print(f"[复盘] 获取概念板块异常: {e}")
            return self._hot_from_zt_pool(trade_date, top_n), True

    def _get_hot_industries(self, trade_date: str = None, top_n: int = 10) -> tuple:
        try:
            df = self.ak.get_industry_boards()
            if df is None or df.empty:
                return self._hot_from_zt_pool(trade_date, top_n), True
            result = self._parse_board_df(df, top_n)
            if result and all(abs(r.get('change_pct', 0)) < 0.001 for r in result):
                print("[复盘] 行业板块涨跌幅全为0，改用涨停池行业分布")
                return self._hot_from_zt_pool(trade_date, top_n), True
            return result, False
        except Exception as e:
            print(f"[复盘] 获取行业板块异常: {e}")
            return self._hot_from_zt_pool(trade_date, top_n), True

    def _hot_from_zt_pool(self, trade_date: str = None, top_n: int = 10) -> List[Dict]:
        limit_ups = self.ak.get_limit_up_pool(trade_date)
        sector_count = Counter(lu.board_type for lu in limit_ups if lu.board_type)
        return [{
            'name': s,
            'change_pct': 0.0,
            'lead_stock': '',
            'zt_count': c,
        } for s, c in sector_count.most_common(top_n)]

    def _parse_board_df(self, df, top_n: int) -> List[Dict]:
        cols = list(df.columns)
        print(f"[复盘] 板块数据列名: {cols[:10]}...")

        name_col = next((c for c in cols if '名称' in c or 'name' in c.lower()), None)
        if name_col is None:
            name_col = next((c for c in cols if '板块' in c and '代码' not in c), None)
        if name_col is None:
            name_col = cols[0]
            print(f"[复盘] 警告: 未找到板块名称列，使用 '{name_col}' 作为名称")
        pct_col = next((c for c in cols if '涨跌幅' in c or '涨幅' in c or 'change' in c.lower() or 'pct' in c.lower()), None)
        lead_col = next((c for c in cols if '领涨' in c or '龙头' in c), None)

        results = []
        for _, row in df.iterrows():
            results.append({
                'name': str(row.get(name_col, '')),
                'change_pct': self._safe_float(row.get(pct_col, 0)) if pct_col else 0.0,
                'lead_stock': str(row.get(lead_col, '')) if lead_col else '',
            })

        results.sort(key=lambda x: abs(x['change_pct']), reverse=True)
        return results[:top_n]

    # ------------------------------------------------------------------
    # 龙虎榜
    # ------------------------------------------------------------------
    def _sort_lhb(self, lhb: List) -> List:
        if lhb:
            valid = [l for l in lhb if l.net_amount is not None]
            return sorted(valid, key=lambda x: abs(x.net_amount), reverse=True)[:10]
        fallback = self.ak.get_lhb_fallback()
        print(f"[复盘] 龙虎榜备用接口: {len(fallback)} 条")
        valid = [l for l in fallback if l.net_amount is not None]
        return sorted(valid, key=lambda x: abs(x.net_amount), reverse=True)[:10]

    # ------------------------------------------------------------------
    # 大盘表现
    # ------------------------------------------------------------------
    def _get_market_overview(self, trade_date: str = None) -> Dict:
        overview = {
            'total_volume': 0,
            'up_count': 0, 'down_count': 0, 'flat_count': 0,
            'limit_up_count': 0, 'limit_down_count': 0,
            'avg_change_pct': 0.0,
        }
        td_date = None
        if trade_date:
            try:
                td_date = datetime.strptime(trade_date, '%Y%m%d').date()
            except ValueError:
                pass

        is_historical = td_date is not None and td_date != date.today()

        if not is_historical:
            try:
                df = self.ak.get_spot_df()
                if df is not None and not df.empty:
                    cols = list(df.columns)
                    amt_col = next((c for c in cols if '成交额' in c), None)
                    if amt_col:
                        overview['total_volume'] = round(df[amt_col].sum() / 1e8, 0)
                    pct_col = next((c for c in cols if '涨跌幅' in c), None)
                    if pct_col:
                        pct = df[pct_col]
                        overview['up_count'] = int((pct > 0).sum())
                        overview['down_count'] = int((pct < 0).sum())
                        overview['flat_count'] = int((pct == 0).sum())
                        overview['avg_change_pct'] = round(float(pct.mean()), 2)
                    if overview['total_volume'] > 0 and overview['up_count'] + overview['down_count'] > 0:
                        return overview
            except Exception as e:
                print(f"[复盘] 大盘概况(spot)异常: {e}")

        if not is_historical:
            print("[复盘] 实时行情数据不完整，补充使用通达信本地数据...")

        print("[复盘] 使用通达信本地数据...")
        try:
            idx = self.tdx.get_index_turnover(trade_date=td_date)
            overview['total_volume'] = idx['total_amount']
            breadth = self.tdx.get_market_breadth(trade_date=td_date)
            overview['up_count'] = breadth['up_count']
            overview['down_count'] = breadth['down_count']
            overview['flat_count'] = breadth['flat_count']
            overview['limit_up_count'] = breadth['limit_up_count']
            overview['limit_down_count'] = breadth['limit_down_count']
            if breadth['scanned'] > 0:
                net = breadth['up_count'] - breadth['down_count']
                overview['avg_change_pct'] = round(net / breadth['scanned'] * 100, 2)
            print(f"[复盘] 通达信扫描{breadth['scanned']}只: "
                  f"涨{overview['up_count']} 跌{overview['down_count']} "
                  f"成交额{overview['total_volume']:.0f}亿")
        except Exception as e:
            print(f"[复盘] 通达信大盘数据获取失败: {e}")

        return overview

    # ------------------------------------------------------------------
    # 短线情绪（增强版）
    # ------------------------------------------------------------------
    def _generate_sentiment(self, limit_ups: List, market_overview: Dict,
                             cycle: Dict) -> Dict:
        total = len(limit_ups)
        sealed = sum(1 for lu in limit_ups if lu.is_seal)
        broken_count = sum(1 for lu in limit_ups if lu.is_broken)
        seal_rate = sealed / max(total, 1) * 100
        max_cons = max((lu.consecutive for lu in limit_ups), default=0)

        # 情绪等级（结合周期判断）
        if cycle['stage'].startswith('高潮'):
            mood = '强'
            mood_desc = cycle['stage_desc']
        elif cycle['stage'] == '发酵期':
            mood = '偏强'
            mood_desc = cycle['stage_desc']
        elif cycle['stage'] in ('启动期', '震荡期'):
            mood = '中性'
            mood_desc = cycle['stage_desc']
        elif cycle['stage'].startswith('退潮'):
            mood = '偏弱'
            mood_desc = cycle['stage_desc']
        elif cycle['stage'] == '冰点期':
            mood = '冰点'
            mood_desc = cycle['stage_desc']
        else:
            mood = '中性'
            mood_desc = cycle['stage_desc']

        broken_rate = broken_count / max(total, 1) * 100
        picks = self._select_top_picks(limit_ups)

        return {
            'mood': mood,
            'mood_desc': mood_desc,
            'seal_rate': f'{seal_rate:.1f}%',
            'broken_rate': f'{broken_rate:.1f}%',
            'total_zt': total,
            'max_consecutive': max_cons,
            'picks': picks,
            'summary': (
                f"涨停{total}只，封板率{seal_rate:.1f}%，"
                f"炸板{broken_count}只（{broken_rate:.1f}%），"
                f"最高连板{max_cons}板。"
                f"周期阶段：{cycle['stage']}。"
                f"{cycle['action']}"
            ),
        }

    def _select_top_picks(self, limit_ups: List) -> List[Dict]:
        """精选备选标的：优先1进2和2进3，排除4板以上高位标的"""
        picks = []
        for lu in limit_ups:
            if _is_yizi_board(lu):
                continue

            score = 0
            reasons = []

            if lu.consecutive == 1:
                score += 5
                reasons.append('首板启动·1进2候选')
            elif lu.consecutive == 2:
                score += 4
                reasons.append('2连板·2进3候选')
            elif lu.consecutive == 3:
                score += 2
                reasons.append('3连板·关注换手')
            else:
                continue

            if lu.is_seal:
                score += 2
                reasons.append('封死涨停')
            if lu.seal_amount > 5000:
                score += 2
                reasons.append(f'封单{lu.seal_amount/10000:.1f}亿')
            elif lu.seal_amount > 1000:
                score += 1
                reasons.append('封单充足')

            try:
                t_str = str(lu.limit_up_time).replace(':', '')[:4]
                t = int(t_str) if t_str.isdigit() else 1400
                if t <= 1000:
                    score += 2
                    reasons.append('早盘秒板')
                elif t <= 1030:
                    score += 1
                    reasons.append('上午封板')
            except (ValueError, TypeError):
                pass

            if 10 <= lu.float_market_cap <= 100:
                score += 1
                reasons.append(f'流通市值{lu.float_market_cap:.0f}亿')

            if score >= 5:
                picks.append({
                    'code': lu.code, 'name': lu.name,
                    'consecutive': lu.consecutive,
                    'score': score, 'reasons': reasons,
                })

        picks.sort(key=lambda x: x['score'], reverse=True)
        return picks[:5]

    @staticmethod
    def _safe_float(val, default=0.0) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return default
