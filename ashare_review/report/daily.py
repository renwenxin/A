"""日度复盘报告 — 龙哥复盘体系增强版

在原有涨停统计基础上新增：
- 情绪周期节点判断（启动/发酵/高潮/退潮/冰点）
- 板块涨停潮 + 分歧分析（龙哥题材复盘方法论）
- 竞价预期预测（基于当日走势预判次日竞价）
- 弱转强候选标的（烂板/分歧板次日弱转强潜力）
- 均线体系概况（大盘+主要标的）
- 筹码分布信号（连板龙头的持有/卖出判断）
- 涨停复制候选标的（近20日涨停回调企稳+四类信号识别）

复盘是收盘后做的事——所有数据在收盘后即可获取。
"""
import json
import os
import struct
import numpy as np
from datetime import date, datetime, timedelta, time
from typing import Dict, List, Optional
from collections import Counter, defaultdict
from ..data.tdx_reader import TdxReader, RECORD_SIZE
from ..data.akshare_fetcher import AkshareFetcher
from ..utils.calendar import TradingCalendar
from .events import get_events_for_period, get_event_summary_text
from ..utils.log import get_logger

logger = get_logger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
LIMIT_UP_POOL_FILE = os.path.join(DATA_DIR, 'limit_up_pool.json')
PICKS_HISTORY_FILE = os.path.join(DATA_DIR, 'picks_history.json')
CONCEPT_MAP_FILE = os.path.join(DATA_DIR, 'concept_map.json')


# ---- 预测台账：情绪周期 stage → 次日方向 映射（待验证假设，台账数据可反过来校准） ----
_NEXT_BIAS_BY_STAGE = {
    '启动期': 'up', '发酵期': 'up',
    '高潮末期': 'down', '退潮期': 'down',
    '高潮期': 'flat', '震荡期': 'flat', '冰点期': 'flat',
}
# ---- 预测台账：竞价 forecast → 方向 映射 ----
_AUCTION_DIRECTION = {
    '火爆': 'high', '偏强': 'high', '中性': 'flat',
    '偏弱': 'low', '观望': 'low',
}


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

        logger.info(f"正在获取 {report_date} 数据...")

        # --- 涨停池 ---
        limit_ups = self.ak.get_limit_up_pool(trade_date)
        logger.info(f"涨停池: {len(limit_ups)} 只")

        # --- 龙虎榜 ---
        lhb = self.ak.get_lhb(trade_date)
        logger.info(f"龙虎榜: {len(lhb)} 条")

        # --- 概念/行业板块 ---
        hot_concepts, concepts_fallback = self._get_hot_concepts(trade_date)
        logger.info(f"概念板块: {len(hot_concepts)} 个")

        hot_industries, industries_fallback = self._get_hot_industries(trade_date)
        logger.info(f"行业板块: {len(hot_industries)} 个")

        hot_merged = (concepts_fallback and industries_fallback)

        # --- 大盘表现 ---
        market_overview = self._get_market_overview(trade_date)
        logger.info(f"大盘: 成交额{market_overview['total_volume']:.0f}亿 "
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

        # ==================== 新增：细分概念分析（概念映射表×涨停池） ====================
        concept_analysis = self._analyze_concepts(limit_ups)

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

        # ==================== 事件日历 ====================
        trade_dt = datetime.strptime(trade_date, '%Y%m%d').date()
        event_data = get_events_for_period(trade_dt)

        # ==================== 涨停复制候选标的 ====================
        zt_replica_picks = self._get_zt_replica_candidates(limit_ups, trade_date)

        # ==================== V2 新增：昨日标的验证 ====================
        yesterday_picks = self._get_yesterday_picks_validation(trade_date)
        pick_stats = self._get_historical_pick_stats()

        # ==================== V2 新增：保存今日标的供明日验证 ====================
        self._save_today_picks(trade_date, sentiment['picks'])

        return {
            'date': report_date,
            'limit_up_codes': [lu.code for lu in limit_ups],
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
            'concept_analysis': concept_analysis,
            'weak_to_strong': weak_to_strong,
            'auction_forecast': auction_forecast,
            'chip_signals': chip_signals,
            # 事件日历
            'recent_events': event_data.get('recent_events', []),
            'upcoming_events': event_data.get('upcoming_events', []),
            'ongoing_themes': event_data.get('ongoing_themes', []),
            'has_event_data': event_data.get('has_data', False),
            # 涨停复制候选标的
            'zt_replica_picks': zt_replica_picks,
            # V2 新增
            'yesterday_picks': yesterday_picks,
            'pick_stats': pick_stats,
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

    def generate_llm_summary(self, trade_date=None, data=None) -> str:
        """生成 LLM 市场综述 — 需要 LLM Provider 可用。

        data: 已生成的报告 dict。传入则直接基于该数据构建 prompt，
        避免在已有缓存时为了 LLM 综述再整份重新爬取。
        """
        if data is None:
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
        """解析"最新已收盘交易日"作为复盘/回测的基准日。

        交易日 15:30 之前当日数据未收盘落定（涨停池/龙虎榜/行情均不完整），
        使用上一交易日数据；15:30 之后使用当天数据。非交易日取最近交易日。
        截止时间可用环境变量 REPORT_CUTOFF_HM 覆盖（如 REPORT_CUTOFF_HM=14:30）。
        """
        cutoff = time(15, 30)
        env_hm = os.environ.get('REPORT_CUTOFF_HM')
        if env_hm and ':' in env_hm:
            try:
                h, m = env_hm.split(':')
                cutoff = time(int(h), int(m))
            except ValueError:
                pass
        today = date.today()
        if self.calendar.is_trading_day(today):
            now = datetime.now()
            if now.time() < cutoff:
                d = today
                for _ in range(10):
                    d = d - timedelta(days=1)
                    if self.calendar.is_trading_day(d):
                        return d.strftime('%Y%m%d')
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
    # 新增：细分概念聚合（概念映射表 × 涨停池）
    # ==================================================================
    def _load_concept_map(self) -> dict:
        """加载概念映射表 concept_map.json -> {概念名: {'members': {code...}, 'partial': bool}}"""
        if not os.path.exists(CONCEPT_MAP_FILE):
            return {}
        try:
            with open(CONCEPT_MAP_FILE, encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"读取概念映射表失败: {e}")
            return {}
        out = {}
        for name, meta in data.get('concepts', {}).items():
            if isinstance(meta, dict):
                members = meta.get('members', [])
                partial = bool(meta.get('partial'))
            else:
                members, partial = meta, False
            codes = {str(m).zfill(6) for m in members if str(m).zfill(6).isdigit()}
            if codes:
                out[str(name)] = {'members': codes, 'partial': partial}
        return out

    def _analyze_concepts(self, limit_ups: List) -> Dict:
        """按细分概念聚合涨停股（MLCC概念 / 算力租赁 / 医药电商…）。

        涨停池只有"所属行业"列（粗行业），概念归属来自本地 concept_map.json。
        每个涨停股可能属于多个概念 → 每个概念统计涨停家数 + 龙头。

        partial 概念（成分不完整，如只抓了同花顺第一页）在报告中保留，
        但"没匹配到"不判死刑——由文章层自行控制措辞。
        """
        concept_map = self._load_concept_map()
        if not concept_map:
            return {'concept_sectors': [], 'has_data': False, 'concept_count': 0}

        agg: Dict[str, List] = defaultdict(list)
        for lu in limit_ups:
            code = str(lu.code).zfill(6)
            for name, meta in concept_map.items():
                if code in meta['members']:
                    agg[name].append(lu)

        sectors = []
        for name, stocks in agg.items():
            max_cons = max((s.consecutive for s in stocks), default=1)
            leader = max(stocks, key=lambda s: (s.consecutive, -int(
                str(s.limit_up_time).replace(':', '')[:4] or '1500')))
            sectors.append({
                'name': name,
                'zt_count': len(stocks),
                'max_consecutive': max_cons,
                'leader_code': leader.code,
                'leader_name': leader.name,
                'leader_cons': leader.consecutive,
                'member_codes': [s.code for s in stocks],
                'partial': concept_map[name]['partial'],
            })
        # 涨停家数优先，其次连板高度
        sectors.sort(key=lambda x: (x['zt_count'], x['max_consecutive']), reverse=True)

        return {
            'concept_sectors': sectors[:8],
            'has_data': bool(sectors),
            'concept_count': len(sectors),
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
                stage = '高潮末期'
                stage_class = 'gaochao-moqi'
                stage_emoji = '⚠'
                stage_desc = (
                    '涨停家数充足但一字板占比过高({:.0f}%)，属于加速一致阶段。'
                    '后排跟风股开始冲高回落无法封板时即为高潮见顶信号。'
                    '此时应去弱留强，中位股必须清仓，仅持有总龙头或空仓等待分歧。'
                ).format(yizi_ratio * 100)
                action = '去弱留强，卖出中位跟风股，仅保留总龙头底仓'
                risk_level = '中高'
            else:
                stage = '高潮期'
                stage_class = 'gaochao'
                stage_emoji = '🔥'
                stage_desc = (
                    '赚钱效应爆棚，板块全面爆发。封板率{:.0f}%，最高{}连板。'
                    '警惕放量滞涨和低位补涨信号（周期末段特征）。'
                    '核心龙头仍可持有，但不要再重仓追高中位标的。'
                ).format(seal_rate, max_cons)
                action = '持股为主，关注龙头开板信号和后排掉队情况'
                risk_level = '中'
        elif total >= 50 and seal_rate >= 70 and max_cons >= 4:
            stage = '发酵期'
            stage_class = 'fajiao'
            stage_emoji = '📈'
            stage_desc = (
                '赚钱效应明显增强，涨停家数充足，连板梯队逐渐成形。'
                '板块联动良好，是短线操作的最佳阶段。'
                '重点关注1进2和2进3接力机会。'
            )
            action = '积极操作，重点做1进2和2进3接力'
            risk_level = '低'
        elif total >= 30 and seal_rate >= 60 and max_cons >= 3:
            stage = '启动期'
            stage_class = 'qidong'
            stage_emoji = '🌱'
            stage_desc = (
                '市场开始出现赚钱效应，新题材萌发。'
                '首板数量增多，关注新出现的一板题材（多股涨停的）。'
                '此时是试错阶段，轻仓参与前排标的。'
            )
            action = '轻仓试错，关注新题材首板和一进二机会'
            risk_level = '中低'
        elif total < 15 or seal_rate < 40:
            stage = '冰点期'
            stage_class = 'bingdian'
            stage_emoji = '❄'
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
            stage_class = 'tuichao'
            stage_emoji = '🌊'
            stage_desc = (
                '赚钱效应减弱，炸板率上升至{:.0f}%。龙头出现分歧，'
                '跟风股开始补跌。该强不强的标的必须第一时间离场。'
                '核按钮出现时确认退潮。'
            ).format(broken_count / max(total, 1) * 100)
            action = '减仓防守，卖出中位股和跟风股，不开新仓'
            risk_level = '高'
        else:
            stage = '震荡期'
            stage_class = 'zhendang'
            stage_emoji = '↔'
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
            'stage_class': stage_class,
            'stage_emoji': stage_emoji,
            'stage_desc': stage_desc.strip(),
            'action': action,
            'risk_level': risk_level,
            'next_bias': _NEXT_BIAS_BY_STAGE.get(stage, 'flat'),
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
            'direction': _AUCTION_DIRECTION.get(forecast, 'flat'),
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
            logger.warning(f"获取概念板块异常: {e}")
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
            logger.warning(f"获取行业板块异常: {e}")
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
        logger.info(f"板块数据列名: {cols[:10]}...")

        name_col = next((c for c in cols if '名称' in c or 'name' in c.lower()), None)
        if name_col is None:
            name_col = next((c for c in cols if '板块' in c and '代码' not in c), None)
        if name_col is None:
            name_col = cols[0]
            logger.warning(f"警告: 未找到板块名称列，使用 '{name_col}' 作为名称")
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
        logger.info(f"龙虎榜备用接口: {len(fallback)} 条")
        valid = [l for l in fallback if l.net_amount is not None]
        return sorted(valid, key=lambda x: abs(x.net_amount), reverse=True)[:10]

    # ------------------------------------------------------------------
    # 大盘表现
    # ------------------------------------------------------------------
    @staticmethod
    def _plausible_breadth(o: Dict) -> bool:
        """防御性校验实时快照的涨跌家数是否在 A 股可能范围内。

        A 股约 5600 只，涨跌平合计不可能超过 7000（超过说明把指数/基金/转债
        等非股票也算进去了，比如旧代码把 9000+ 个 .day 文件全扫进去）；
        全市场单日平均涨跌幅也不可能超过 ±30%（北交所单日 ±30% 极限）。
        不满足就当作坏快照丢弃，走通达信本地数据兜底，避免污染复盘。
        """
        total = (o.get('up_count', 0) + o.get('down_count', 0)
                 + o.get('flat_count', 0))
        if total <= 0 or total > 7000:
            return False
        if abs(o.get('avg_change_pct', 0.0)) > 30:
            return False
        return True

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
                    if (overview['total_volume'] > 0
                            and overview['up_count'] + overview['down_count'] > 0
                            and self._plausible_breadth(overview)):
                        return overview
            except Exception as e:
                logger.warning(f"大盘概况(spot)异常: {e}")

        if not is_historical:
            logger.info("实时行情数据不完整，补充使用通达信本地数据...")

        logger.info("使用通达信本地数据...")
        try:
            idx = self.tdx.get_index_turnover(trade_date=td_date)
            overview['total_volume'] = idx['total_amount']
            breadth = self.tdx.get_market_breadth(trade_date=td_date)
            if breadth['scanned'] == 0 and td_date is not None:
                # 目标日无日线数据（当日尚未收盘 / 未来日期）→ 回退最新可用交易日
                logger.warning(f"{td_date} 无日线数据（可能当日尚未收盘），回退到最新可用交易日")
                td_date = None
                idx = self.tdx.get_index_turnover(trade_date=None)
                overview['total_volume'] = idx['total_amount']
                breadth = self.tdx.get_market_breadth(trade_date=None)
            overview['up_count'] = breadth['up_count']
            overview['down_count'] = breadth['down_count']
            overview['flat_count'] = breadth['flat_count']
            overview['limit_up_count'] = breadth['limit_up_count']
            overview['limit_down_count'] = breadth['limit_down_count']
            overview['avg_change_pct'] = breadth.get('avg_change_pct', 0.0)
            logger.info(f"通达信扫描{breadth['scanned']}只: "
                  f"涨{overview['up_count']} 跌{overview['down_count']} "
                  f"成交额{overview['total_volume']:.0f}亿")
        except Exception as e:
            logger.error(f"通达信大盘数据获取失败: {e}")

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
        """精选备选标的 — 龙哥1进2接力选股体系

        复盘页"1进2接力"表格的数据源。严格只收首板（连板数==1），
        多板标的走龙头/连板体系，不混入此列表（否则7板票会出现在1进2里）。

        盘后选股规则（收盘后22:00筛选）：
        1) 仅首板（连板数==1），排除一字板
        2) 主板优先（60xxxx, 00xxxx），非ST
        3) 股价3-15元区间（低价=群众基础广）
        4) 流通市值10-100亿（小盘=拉升难度小）
        5) 封成比>0.5（封单额/成交额，标准龙哥体系）
        6) 封单强度>0.015（封单额/流通市值）
        7) 早盘封板优先（≤10:30）
        8) 股性活跃（年涨停次数多）
        9) 优先N字结构+箱体突破+60/89日线粘合
        10) 热点板块（不在退潮板块）
        """
        picks = []
        for lu in limit_ups:
            # 1进2接力只收首板（多板标的走龙头/连板体系，不在此列表混入）
            if lu.consecutive != 1:
                continue
            # 排除一字板
            if _is_yizi_board(lu):
                continue

            score = 0
            reasons = []

            # ---- 一、1进2战法核心评分 ----

            # 1) 主板优先
            is_main_board = lu.code.startswith('60') or lu.code.startswith('00')
            if is_main_board:
                score += 3
            else:
                # 创业板/科创板 — 降低优先级（20cm不适合1进2接力）
                score -= 2

            # 2) 股价区间（3-15元最佳，低价=群众基础广）
            price = lu.close_price if lu.close_price > 0 else 0
            if 3 <= price <= 15:
                score += 10
                reasons.append(f'低价{price:.1f}元·散户参与度高')
            elif 15 < price <= 25:
                score += 5
                reasons.append(f'中价{price:.1f}元')
            elif price > 100:
                score -= 3  # 高价股不适合散户接力

            # 3) 流通市值（10-100亿最佳）
            mcap = lu.float_market_cap
            if 10 <= mcap <= 60:
                score += 10
                reasons.append(f'小盘{mcap:.0f}亿·拉升难度小')
            elif 60 < mcap <= 100:
                score += 8
                reasons.append(f'适中{mcap:.0f}亿')
            elif 100 < mcap <= 150:
                score += 3
                reasons.append(f'偏大{mcap:.0f}亿')
            elif mcap > 300:
                score -= 3  # 大盘股不适合接力

            # 4) 封成比（封单额/成交额 > 0.5）
            turnover = lu.turnover if lu.turnover > 0 else 0
            seal_ratio = lu.seal_amount / turnover if turnover > 0 else 0
            if seal_ratio > 1.0:
                score += 15
                reasons.append(f'封成比{seal_ratio:.2f}·极强封单')
            elif seal_ratio > 0.5:
                score += 10
                reasons.append(f'封成比{seal_ratio:.2f}·封单充足')
            elif seal_ratio > 0.3:
                score += 4
                reasons.append(f'封成比{seal_ratio:.2f}')
            else:
                score -= 4
                reasons.append(f'封成比{seal_ratio:.2f}·封单偏弱')

            # 5) 封单强度（封单额/流通市值 > 0.015）
            seal_strength = lu.seal_amount / (mcap * 10000) if mcap > 0 else 0
            if seal_strength > 0.025:
                score += 12
                reasons.append(f'封单强度{seal_strength:.3f}·主力坚决')
            elif seal_strength > 0.015:
                score += 8
                reasons.append(f'封单强度达标({seal_strength:.3f})')
            elif seal_strength > 0.01:
                score += 3
            else:
                score -= 2

            # 6) 涨停时间（越早越好）
            try:
                t_str = str(lu.limit_up_time).replace(':', '')[:4]
                t = int(t_str) if t_str.isdigit() else 1400
                if t <= 1000:
                    score += 12
                    reasons.append('早盘秒板≤10:00·次日预期高开5-9%')
                elif t <= 1030:
                    score += 8
                    reasons.append('上午封板≤10:30·次日预期高开3-6%')
                elif t <= 1130:
                    score += 4
                    reasons.append('午前封板')
                elif t <= 1400:
                    score += 1
                    reasons.append('下午板·次日预期平开或小幅高开')
                else:
                    score -= 3
                    reasons.append('尾盘板·关注弱转强机会')
            except (ValueError, TypeError):
                pass

            # 7) 封板质量（封死未炸 > 炸板回封 > 烂板）
            if lu.is_seal and not lu.is_broken:
                score += 6
                reasons.append('封死未炸板')
            elif lu.is_seal and lu.is_broken:
                score += 2
                reasons.append('炸板回封·分歧转一致·弱转强候补')
            elif lu.is_broken:
                score -= 5
                reasons.append('炸板未封·谨慎')

            # ---- 二、首板加分（此处已过滤为仅首板，即1进2候选） ----
            cons = lu.consecutive
            score += 8
            reasons.append('首板·1进2候选（核心模式）')

            # ---- 三、股性评分（历史涨停次数） ----
            limit_up_count = getattr(lu, 'limit_up_count', 0)
            if limit_up_count >= 15:
                score += 8
                reasons.append(f'股性极活({limit_up_count}次/年)·妖股基因')
            elif limit_up_count >= 10:
                score += 5
                reasons.append(f'股性活跃({limit_up_count}次/年)·龙头候补')
            elif limit_up_count >= 5:
                score += 2
                reasons.append(f'股性尚可({limit_up_count}次/年)')
            elif limit_up_count <= 1:
                score -= 2
                reasons.append('股性冷门·首次涨停')

            # ---- 四、板块/板型标记 ----
            board_type = getattr(lu, 'board_type', '') or '-'
            if board_type and board_type not in ('-', 'N/A', '未知', ''):
                if '一字' in str(board_type):
                    score -= 3
                    reasons.append(f'一字板·不参与1进2接力')
                else:
                    reasons.append(f'板型:{board_type}')

            # ---- 精选门槛：至少8分 ----
            if score >= 8:
                # 读取通达信分钟线获取当日最高单分钟量（爆量）
                max_vol = 0  # 手
                market = 'sh' if lu.code.startswith('6') else 'sz'
                if lu.code.startswith(('8', '4')):
                    market = 'bj'
                try:
                    max_vol = self.tdx.read_minute_max_volume(lu.code, market)
                except Exception:
                    pass

                picks.append({
                    'code': lu.code,
                    'name': lu.name,
                    'consecutive': cons,
                    'score': score,
                    'reasons': reasons,
                    'seal_ratio': round(seal_ratio, 2),
                    'seal_strength': round(seal_strength, 4),
                    'price': round(price, 2) if price else 0,
                    'market_cap': round(mcap, 0),
                    'limit_up_time': str(lu.limit_up_time),
                    'max_volume': max_vol,
                    'max_volume_50': max_vol // 2,
                })

        picks.sort(key=lambda x: x['score'], reverse=True)
        return picks[:8]  # 返回Top 8备选标的

    # ==================================================================
    # 涨停复制候选标的（龙哥涨停双响炮+N字反包体系）
    # ==================================================================
    def _get_zt_replica_candidates(self, limit_ups: List, trade_date: str = None) -> List[Dict]:
        """涨停复制选股：从涨停池+历史涨停候选池中找回调企稳的标的。

        逻辑与 zt_replica_portfolio.py 一致：
        1. 从涨停池中找近20日有涨停的标的
        2. 检查是否处于缩量回调企稳状态
        3. 识别四类信号（双响炮/N字反包/缩量回踩/蓄势待发）
        4. 附加均线支撑、板块联动、回调窗口评分
        """
        # 加载行业映射
        industry_map = {}
        industry_file = os.path.join(DATA_DIR, 'industry_map.json')
        if os.path.exists(industry_file):
            try:
                with open(industry_file, 'r', encoding='utf-8') as f:
                    industry_map = json.load(f)
            except Exception:
                pass

        # 加载股票名称映射
        name_map = {}
        name_file = os.path.join(DATA_DIR, 'stock_name_map.json')
        if os.path.exists(name_file):
            try:
                with open(name_file, 'r', encoding='utf-8') as f:
                    name_map = json.load(f)
            except Exception:
                pass

        def _get_name(code: str) -> str:
            return name_map.get(str(code).zfill(6), '')

        def _get_sector(code: str) -> str:
            return industry_map.get(str(code).zfill(6), '')

        @staticmethod
        def _limit_threshold(code: str) -> float:
            code = str(code).zfill(6)
            if code.startswith(('300', '301', '688')):
                return 0.199
            if code.startswith(('8', '4')):
                return 0.299
            return 0.095

        @staticmethod
        def _is_main_board(code: str) -> bool:
            code = str(code).zfill(6)
            return code.startswith(('60', '00', '001', '002'))

        # 收集需要检查的标的：今日涨停池 + 历史候选池中近20日涨停的
        codes_to_check = set()

        # 1) 今日涨停池（已有数据，直接加入）
        today_zt_codes = {lu.code for lu in limit_ups if _is_main_board(lu.code)}
        codes_to_check.update(today_zt_codes)

        # 2) 历史候选池中近20日有涨停回调的（从limit_up_pool.json加载）
        if os.path.exists(LIMIT_UP_POOL_FILE):
            try:
                with open(LIMIT_UP_POOL_FILE, 'r', encoding='utf-8') as f:
                    pool_data = json.load(f)
                pool = pool_data.get('pool', [])
                # 取年涨停次数最多的前100只（高频涨停股更容易出现复制机会）
                pool_sorted = sorted(pool, key=lambda x: x.get('limit_count', 0), reverse=True)
                for stock in pool_sorted[:100]:
                    code = str(stock['code']).zfill(6)
                    if _is_main_board(code) and code not in codes_to_check:
                        codes_to_check.add(code)
            except Exception:
                pass

        print(f'[复盘·涨停复制] 检查 {len(codes_to_check)} 只标的...')

        candidates = []
        checked = 0
        for code in codes_to_check:
            checked += 1
            if checked % 50 == 0:
                print(f'  涨停复制检查 {checked}/{len(codes_to_check)}...')

            name = _get_name(code)
            if not name or 'ST' in str(name):
                continue

            # 读取日线数据
            market = 'sh' if str(code).startswith('6') else 'sz'
            if str(code).startswith(('8', '4')):
                market = 'bj'
            try:
                df = self.tdx.read_daily(code, market)
                if df is None or df.empty or len(df) < 60:
                    continue
            except Exception:
                continue

            closes = df['close'].values
            opens = df['open'].values
            highs = df['high'].values
            lows = df['low'].values
            volumes = df['volume'].values
            idx = len(closes) - 1

            # 计算MAVOL180
            if len(volumes) >= 180:
                mavol180 = float(np.mean(volumes[-180:]))
            else:
                mavol180 = float(np.mean(volumes))

            if mavol180 <= 0:
                continue

            # 在近20日内找最近一次涨停
            limit_pct = _limit_threshold(code)
            zt_idx = None
            zt_close = zt_vol = zt_low = zt_high = 0
            zt_timing = '换手板'
            lookback = min(20, idx)

            for j in range(idx - 1, max(idx - lookback - 1, 0), -1):
                if j < 1:
                    continue
                prev_c = closes[j - 1]
                if prev_c <= 0:
                    continue
                chg = (closes[j] - prev_c) / prev_c
                if chg >= limit_pct:
                    # 排除一字板
                    if abs(opens[j] - closes[j]) / max(closes[j], 0.01) < 0.005:
                        continue
                    zt_idx = j
                    zt_close = closes[j]; zt_vol = volumes[j]
                    zt_low = lows[j]; zt_high = highs[j]
                    # 涨停时段
                    open_chg = (opens[j] - prev_c) / prev_c
                    if open_chg >= 0.03:
                        zt_timing = '早盘强势板'
                    elif open_chg >= 0:
                        zt_timing = '换手板'
                    else:
                        zt_timing = '低开拉板'
                    break

            if zt_idx is None:
                continue

            days_since = idx - zt_idx
            if days_since < 1 or days_since > 10:
                continue

            # 回调分析
            pb_start = zt_idx + 1
            pb_end = idx
            if pb_end < pb_start:
                continue
            pb_days = pb_end - pb_start + 1

            pb_vols = [volumes[i] for i in range(pb_start, pb_end + 1)]
            pb_vol_max = max(pb_vols) if pb_vols else 0
            pb_high = max(highs[i] for i in range(pb_start, pb_end + 1))
            pb_low = min(lows[i] for i in range(pb_start, pb_end + 1))

            # 缩量判断
            is_shrinking = pb_vol_max < zt_vol * 0.6
            is_moderate = pb_vol_max < zt_vol * 0.8

            # 不破涨停最低价
            is_above_zt_low = pb_low >= zt_low * 0.98

            close = closes[idx]; vol = volumes[idx]
            vol_ratio = vol / mavol180

            # 均线
            ma5 = float(np.mean(closes[-5:])) if len(closes) >= 5 else 0
            ma10 = float(np.mean(closes[-10:])) if len(closes) >= 10 else 0
            ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else 0

            ma_support = ''
            ma_score = 0
            if ma5 > 0:
                dist_to_ma5 = (close - ma5) / ma5 * 100
                if -1 <= dist_to_ma5 <= 3:
                    ma_support = '回踩MA5'; ma_score = 10
            if not ma_support and ma10 > 0:
                dist_to_ma10 = (close - ma10) / ma10 * 100
                if -1 <= dist_to_ma10 <= 4:
                    ma_support = '回踩MA10'; ma_score = 6
            if not ma_support and ma20 > 0 and close > ma20:
                ma_support = '站稳MA20'; ma_score = 3

            # 今日涨跌幅
            today_chg = 0
            if idx >= 1 and closes[idx-1] > 0:
                today_chg = (close - closes[idx-1]) / closes[idx-1]

            # 今日是否涨停
            is_zt_today = today_chg >= limit_pct
            is_yizi = abs(opens[idx] - close) / max(close, 0.01) < 0.005

            # 四类信号判断
            break_pb = close > pb_high and vol_ratio >= 1.2
            sig_a = is_shrinking and break_pb  # N字反包
            sig_b = is_zt_today and not is_yizi and (is_shrinking or is_moderate)  # 双响炮
            sig_c = ((is_shrinking or is_moderate) and is_above_zt_low
                     and vol_ratio >= 1.2 and close > zt_close * 0.98 and not break_pb)  # 缩量回踩
            sig_d = (not is_zt_today and 0.05 <= today_chg < 0.095
                     and vol_ratio >= 1.5 and close > ma5 and ma_support != ''  # 蓄势待发
                     and (is_shrinking or is_moderate))

            if not (sig_a or sig_b or sig_c or sig_d):
                continue
            if vol_ratio >= 5.0:
                continue  # 过度放量

            # 评分
            if sig_b:
                sig_type = '🔥涨停双响炮'; score = 70
            elif sig_a:
                sig_type = '📈N字反包'; score = 63
            elif sig_d:
                sig_type = '📊蓄势待发'; score = 52
            else:
                sig_type = '🔍缩量回踩企稳'; score = 55

            if is_shrinking: score += 12
            elif is_moderate: score += 6
            if vol_ratio >= 2.0: score += 10
            elif vol_ratio >= 1.5: score += 5
            if 2 <= pb_days <= 4: score += 8
            elif pb_days <= 6: score += 4
            if zt_timing == '早盘强势板': score += 3
            score += ma_score

            # 前日抗跌
            if idx >= 2 and closes[idx-1] > 0 and closes[idx-2] > 0:
                prev_stock_chg = (closes[idx-1] - closes[idx-2]) / closes[idx-2]
                if prev_stock_chg > 0:
                    score += 10

            # 板块联动
            sector = _get_sector(code)

            candidates.append({
                'code': str(code).zfill(6),
                'name': name,
                'sig_type': sig_type,
                'score': min(100, score),
                'close': round(float(close), 2),
                'vol_ratio': round(vol_ratio, 1),
                'zt_days_ago': days_since,
                'pb_days': pb_days,
                'is_shrinking': is_shrinking,
                'break_pct': round((close - pb_high) / pb_high * 100, 1) if pb_high > 0 else 0,
                'today_chg': round(today_chg * 100, 1),
                'ma_support': ma_support,
                'zt_timing': zt_timing,
                'sector': sector,
                'limit_count': 0,  # 会从候选池补充
            })

        # 板块联动加分
        sector_signals = defaultdict(int)
        for c in candidates:
            if c['sector'] and c['sig_type'] != '🔍缩量回踩企稳':
                sector_signals[c['sector']] += 1

        for c in candidates:
            if c['sector'] and sector_signals.get(c['sector'], 0) >= 2:
                c['score'] = min(100, c['score'] + 10)
                c['sector_linkage'] = sector_signals[c['sector']]

        # 填入历史涨停次数
        if os.path.exists(LIMIT_UP_POOL_FILE):
            try:
                with open(LIMIT_UP_POOL_FILE, 'r', encoding='utf-8') as f:
                    pool_data = json.load(f)
                pool_map = {str(p['code']).zfill(6): p.get('limit_count', 0)
                           for p in pool_data.get('pool', [])}
                for c in candidates:
                    c['limit_count'] = pool_map.get(c['code'], 0)
            except Exception:
                pass

        candidates.sort(key=lambda x: x['score'], reverse=True)
        # 用 ascii 兼容字符避免 Windows GBK 终端崩溃
        try:
            top_info = [(c["code"][-6:], c["sig_type"].encode('ascii', 'replace').decode('ascii'), c["score"])
                       for c in candidates[:5]]
            print(f'[复盘·涨停复制] 找到 {len(candidates)} 只候选, Top 5: {top_info}')
        except Exception:
            print(f'[复盘·涨停复制] 找到 {len(candidates)} 只候选')

        return candidates[:12]  # 返回Top 12

    @staticmethod
    def _safe_float(val, default=0.0) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    # ==================================================================
    # 昨日标的验证（V2 新增）
    # ==================================================================
    def _save_today_picks(self, trade_date: str, picks: List[Dict]) -> None:
        """保存今日精选标的到历史文件，供次日验证"""
        # 未来日期的涨停池会回退为"最近可用"（其实是今天的数据），
        # 若把今天的精选落进明天的键，次日"昨日标的验证"就会读到今日精选。直接跳过。
        try:
            d = datetime.strptime(trade_date, '%Y%m%d').date()
            if d > date.today():
                logger.warning(f"跳过未来日期精选保存: {trade_date}（涨停池可能回退为今日数据）")
                return
        except ValueError:
            return
        history = {}
        if os.path.exists(PICKS_HISTORY_FILE):
            try:
                with open(PICKS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except Exception:
                pass
        history[trade_date] = [{
            'code': p['code'], 'name': p['name'],
            'score': p['score'], 'price': p.get('price', 0),
            'reasons': p.get('reasons', []),
        } for p in picks]
        # 只保留最近30天
        keys = sorted(history.keys(), reverse=True)[:30]
        history = {k: history[k] for k in keys}
        try:
            with open(PICKS_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _get_yesterday_picks_validation(self, trade_date: str) -> List[Dict]:
        """加载昨日精选标的，并与今日实际表现对比验证

        返回每个标的的今日表现：是否涨停、涨跌幅、是否2连板成功
        """
        try:
            dt = datetime.strptime(trade_date, '%Y%m%d').date()
            prev_date = dt - timedelta(days=1)
            # 跳过周末
            for _ in range(5):
                if self.calendar.is_trading_day(prev_date):
                    break
                prev_date = prev_date - timedelta(days=1)
            prev_ymd = prev_date.strftime('%Y%m%d')
        except ValueError:
            return []

        if not os.path.exists(PICKS_HISTORY_FILE):
            return []

        try:
            with open(PICKS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except Exception:
            return []

        yesterday_picks = history.get(prev_ymd, [])
        if not yesterday_picks:
            return []

        # 获取今日涨停池（用于判断是否2连板）
        today_limit_ups = self.ak.get_limit_up_pool(trade_date)
        today_zt_codes = {lu.code: lu for lu in today_limit_ups}

        results = []
        for pick in yesterday_picks:
            code = pick['code']
            # 读取今日行情
            market = 'sh' if str(code).startswith('6') else 'sz'
            if str(code).startswith(('8', '4')):
                market = 'bj'
            try:
                df = self.tdx.read_daily(code, market)
                if df is None or df.empty or len(df) < 2:
                    continue
                today_bar = df.iloc[-1]
                yesterday_bar = df.iloc[-2]
                today_chg = (today_bar['close'] - yesterday_bar['close']) / yesterday_bar['close'] * 100
            except Exception:
                today_chg = 0

            # 判断今日是否涨停/连板
            is_zt_today = code in today_zt_codes
            today_zt = today_zt_codes.get(code)
            is_2board = is_zt_today and today_zt and today_zt.consecutive >= 2

            results.append({
                'code': code,
                'name': pick['name'],
                'yesterday_score': pick['score'],
                'yesterday_price': pick.get('price', 0),
                'today_chg': round(today_chg, 2),
                'is_zt_today': is_zt_today,
                'is_2board': is_2board,
                'result': '✅ 2连板成功' if is_2board else (
                    '🔥 涨停' if is_zt_today else (
                        '📈 收涨' if today_chg > 0 else (
                            '📉 收跌' if today_chg < -3 else '➖ 震荡'
                        )
                    )
                ),
                'result_class': 'success' if is_2board else (
                    'good' if is_zt_today else (
                        'neutral' if today_chg > 0 else 'bad'
                    )
                ),
            })

        return results

    def _get_historical_pick_stats(self) -> Dict:
        """获取历史精选标的的整体胜率统计"""
        if not os.path.exists(PICKS_HISTORY_FILE):
            return {'total_picks': 0, 'win_rate': 0, 'avg_score': 0}

        try:
            with open(PICKS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except Exception:
            return {'total_picks': 0, 'win_rate': 0, 'avg_score': 0}

        all_picks = []
        for date_str, picks in history.items():
            all_picks.extend(picks)

        if not all_picks:
            return {'total_picks': 0, 'win_rate': 0, 'avg_score': 0}

        avg_score = sum(p.get('score', 0) for p in all_picks) / len(all_picks)
        return {
            'total_days': len(history),
            'total_picks': len(all_picks),
            'avg_score': round(avg_score, 1),
        }
