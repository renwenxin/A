"""竞价分析模块 — 9:25竞价结束后全市场四维分析

四维分析框架（龙哥体系）：
1. 开盘竞价：全市场量价分布 · 抢筹/抛压异动 · 弱转强候补
2. 大盘环境：指数涨跌 · 涨跌家数 · 市场情绪温度计 · 成交量
3. 板块热点：板块涨幅TOP · 涨停板块分布 · 板块梯队
4. 连板梯队：最高板高度 · 各梯队数量 · 龙头竞价表现 · 炸板预警

用法：
    from ashare_review.analysis.auction_analysis import AuctionAnalyzer
    analyzer = AuctionAnalyzer()
    result = analyzer.analyze()  # → dict ready for JSON serialization
"""
import sys, os, json, struct
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from collections import Counter, defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ashare_review.data.tdx_reader import TdxReader, RECORD_SIZE
from ashare_review.data.akshare_fetcher import AkshareFetcher
from ashare_review.data.models import AuctionInfo, LimitUpInfo
from ashare_review.utils.calendar import TradingCalendar


class AuctionAnalyzer:
    """竞价分析器 — 9:25后一键分析全市场状态"""

    def __init__(self):
        self.tdx = TdxReader()
        self.ak = AkshareFetcher()
        self.cal = TradingCalendar()
        self._name_map: Dict[str, str] = {}
        self._load_name_map()

    def _load_name_map(self):
        name_cache = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                  'data', 'stock_name_map.json')
        if os.path.exists(name_cache):
            try:
                with open(name_cache, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and len(data) > 1000:
                    self._name_map = data
            except Exception:
                pass

    def _get_name(self, code: str) -> str:
        return self._name_map.get(str(code).zfill(6), code)

    # ═══════════════════════════════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════════════════════════════

    def analyze(self) -> dict:
        """执行全市场竞价分析，返回结构化结果。"""
        print('[竞价分析] 开始采集数据...')
        t0 = __import__('time').time()

        # 并行采集数据
        auctions = self.ak.get_auction_data()
        limit_ups = self.ak.get_limit_up_pool()
        spot_df = self.ak.get_spot_df()

        print(f'[竞价分析] 竞价数据: {len(auctions)}只, 涨停: {len(limit_ups)}只, '
              f'行情: {len(spot_df)}只 ({__import__("time").time() - t0:.1f}s)')

        result = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'auction_opening': self._analyze_opening(auctions, limit_ups),
            'market_env': self._analyze_market(spot_df),
            'sector_heat': self._analyze_sectors(limit_ups, auctions),
            'limit_up_ladder': self._analyze_ladder(limit_ups, auctions),
        }

        print(f'[竞价分析] 完成 (总耗时 {__import__("time").time() - t0:.1f}s)')
        return result

    # ═══════════════════════════════════════════════════════════════════════
    # 一维：开盘竞价分析
    # ═══════════════════════════════════════════════════════════════════════

    def _analyze_opening(self, auctions: List, limit_ups: List) -> dict:
        """开盘竞价全貌分析。"""
        if not auctions:
            return {'error': '无竞价数据'}

        total = len(auctions)

        # ── 高开/低开分布 ──
        high_open_7 = []    # 超高开 ≥7%
        high_open_5 = []    # 强势高开 5-7%
        high_open_3 = []    # 高开 3-5%
        high_open_1 = []    # 小高开 1-3%
        flat_open = []      # 平开 -1% ~ 1%
        low_open_3 = []     # 低开 -3% ~ -1%
        low_open_deep = []  # 深低开 < -3%

        for a in auctions:
            pct = a.open_change_pct
            # 排除无效数据
            if a.auction_volume == 0 and a.auction_amount == 0 and abs(pct) < 0.01:
                continue
            if pct >= 7:
                high_open_7.append(a)
            elif pct >= 5:
                high_open_5.append(a)
            elif pct >= 3:
                high_open_3.append(a)
            elif pct >= 1:
                high_open_1.append(a)
            elif pct >= -1:
                flat_open.append(a)
            elif pct >= -3:
                low_open_3.append(a)
            else:
                low_open_deep.append(a)

        # ── 竞价爆量TOP20 ──
        auction_by_amount = sorted(
            [a for a in auctions if a.auction_amount > 0],
            key=lambda x: -x.auction_amount
        )[:20]
        top_amount = [{
            'code': a.code, 'name': a.name or self._get_name(a.code),
            'amount': round(a.auction_amount, 0),
            'open_pct': round(a.open_change_pct, 1),
            'volume': a.auction_volume,
        } for a in auction_by_amount]

        # ── 竞价抢筹异动（量价齐升） ──
        rush_buy = []
        for a in auctions:
            if a.open_change_pct >= 2 and a.auction_amount >= 1000:
                # 读取昨日爆量算量比
                yesterday_max = self._read_trailing_max_volume(a.code)
                if yesterday_max > 0 and a.auction_volume > yesterday_max * 0.3:
                    rush_buy.append({
                        'code': a.code,
                        'name': a.name or self._get_name(a.code),
                        'open_pct': round(a.open_change_pct, 1),
                        'amount': round(a.auction_amount, 0),
                        'vol_ratio': round(a.auction_volume / yesterday_max, 2),
                    })
        rush_buy.sort(key=lambda x: -(x['amount'] or 0))
        rush_buy = rush_buy[:20]

        # ── 弱转强候补（昨日涨停今日竞价表现） ──
        weak_to_strong = self._find_weak_to_strong(limit_ups, auctions)

        # ── 竞价抛压预警（高开低量=诱多） ──
        dump_warning = []
        for a in auctions:
            if a.open_change_pct >= 5:
                yesterday_max = self._read_trailing_max_volume(a.code)
                if yesterday_max > 0 and a.auction_volume < yesterday_max * 0.1:
                    dump_warning.append({
                        'code': a.code,
                        'name': a.name or self._get_name(a.code),
                        'open_pct': round(a.open_change_pct, 1),
                        'vol_ratio': round(a.auction_volume / max(yesterday_max, 1), 3),
                    })
        dump_warning.sort(key=lambda x: -x['open_pct'])
        dump_warning = dump_warning[:15]

        # ── 统计 ──
        valid_count = len(high_open_7) + len(high_open_5) + len(high_open_3) + \
                      len(high_open_1) + len(flat_open) + len(low_open_3) + len(low_open_deep)

        mood = '🔥火爆' if len(high_open_7) + len(high_open_5) > total * 0.05 else \
               '😊偏暖' if len(high_open_3) + len(high_open_5) > total * 0.08 else \
               '😐中性' if len(flat_open) > valid_count * 0.5 else \
               '🥶偏冷' if len(low_open_deep) > total * 0.03 else \
               '😐震荡'

        return {
            'total_stocks': total,
            'valid_auction_count': valid_count,
            'opening_mood': mood,
            'distribution': {
                '超高开≥7%': len(high_open_7),
                '强势高开5-7%': len(high_open_5),
                '高开3-5%': len(high_open_3),
                '小高开1-3%': len(high_open_1),
                '平开±1%': len(flat_open),
                '低开1-3%': len(low_open_3),
                '深低开<3%': len(low_open_deep),
            },
            'top_auction_amount': top_amount,
            'rush_buy_signals': rush_buy,
            'weak_to_strong_candidates': weak_to_strong,
            'dump_warnings': dump_warning,
        }

    # ── 弱转强检测 ──
    def _find_weak_to_strong(self, limit_ups: List, auctions: List) -> List[dict]:
        """找弱转强候补：昨日烂板/尾盘板 → 今日高开3%+"""
        auction_map = {a.code: a for a in auctions}

        candidates = []
        for lu in limit_ups:
            # 昨日首板 + 非一字板
            if not lu.is_first:
                continue
            if lu.board_type == '一字板':
                continue

            a = auction_map.get(lu.code)
            if a is None:
                continue

            # 判断是否为"昨日弱势板"
            time_str = str(lu.limit_up_time).replace(':', '')[:4]
            is_weak_board = False
            weak_reason = ''
            try:
                t = int(time_str)
                if t >= 1400:
                    is_weak_board = True
                    weak_reason = '昨日尾盘板'
                elif lu.is_broken:
                    is_weak_board = True
                    weak_reason = '昨日炸板回封'
            except (ValueError, TypeError):
                pass

            if not is_weak_board and lu.turnover > 0 and lu.seal_amount > 0:
                seal_ratio = lu.seal_amount / lu.turnover
                if seal_ratio < 0.3:
                    is_weak_board = True
                    weak_reason = '昨日分歧烂板'

            if not is_weak_board:
                continue

            # 今日竞价表现
            if a.open_change_pct >= 3:
                yesterday_max = self._read_trailing_max_volume(a.code)
                vol_ratio = round(a.auction_volume / max(yesterday_max, 1), 2) if yesterday_max > 0 else 0

                signal_strength = '⭐⭐⭐超预期·弱转强' if a.open_change_pct >= 5 and vol_ratio >= 0.5 else \
                                  '⭐⭐符合预期·弱转强' if a.open_change_pct >= 3 and vol_ratio >= 0.3 else \
                                  '⭐可观察'

                candidates.append({
                    'code': lu.code,
                    'name': a.name or lu.name,
                    'weak_reason': weak_reason,
                    'open_pct': round(a.open_change_pct, 1),
                    'auction_amount': round(a.auction_amount, 0),
                    'vol_ratio': vol_ratio,
                    'signal': signal_strength,
                })

        candidates.sort(key=lambda x: -(x['open_pct'] or 0))
        return candidates[:15]

    # ═══════════════════════════════════════════════════════════════════════
    # 二维：大盘环境
    # ═══════════════════════════════════════════════════════════════════════

    def _analyze_market(self, spot_df) -> dict:
        """大盘环境分析：指数 + 涨跌家数 + 情绪。"""
        if spot_df is None or spot_df.empty:
            return {'error': '无行情数据'}

        # ── 指数涨跌 ──
        indices = self._get_major_indices(spot_df)

        # ── 涨跌家数 ──
        changes = spot_df['涨跌幅'].dropna()
        total_analyzed = len(changes)
        up_count = int((changes > 0).sum())
        down_count = int((changes < 0).sum())
        flat_count = int((changes == 0).sum())

        # 涨停/跌停家数（近似：涨跌幅≥9.5%或≤-9.5%）
        zt_like = int((changes >= 9.5).sum())
        dt_like = int((changes <= -9.5).sum())

        # 涨幅>5% / 跌幅>5%
        up5 = int((changes >= 5).sum())
        down5 = int((changes <= -5).sum())

        # ── 市场情绪温度计 ──
        # 0-100: 极寒→极热
        up_ratio = up_count / max(total_analyzed, 1)
        if up_ratio >= 0.8:
            temperature = min(100, 60 + int(up_ratio * 50))
            temp_label = '🔥过热'
        elif up_ratio >= 0.6:
            temperature = 50 + int((up_ratio - 0.6) * 50)
            temp_label = '😊偏暖'
        elif up_ratio >= 0.4:
            temperature = 40 + int((up_ratio - 0.4) * 50)
            temp_label = '😐中性'
        elif up_ratio >= 0.2:
            temperature = 20 + int((up_ratio - 0.2) * 100)
            temp_label = '🥶偏冷'
        else:
            temperature = max(0, int(up_ratio * 100))
            temp_label = '❄️极寒'

        # ── 成交额统计 ──
        if '成交额' in spot_df.columns:
            total_amount = spot_df['成交额'].sum() / 1e8  # 亿
        else:
            total_amount = 0

        # ── 昨日前日对比 ──
        prev_info = self._get_prev_market_day()

        return {
            'indices': indices,
            'breadth': {
                'total': total_analyzed,
                'up': up_count,
                'down': down_count,
                'flat': flat_count,
                'up_ratio': round(up_ratio * 100, 1),
                'zt_approx': zt_like,
                'dt_approx': dt_like,
                'up_5pct': up5,
                'down_5pct': down5,
            },
            'sentiment': {
                'temperature': temperature,
                'label': temp_label,
                'advice': '重仓出击' if temperature >= 70 else
                          '中等仓位' if temperature >= 50 else
                          '轻仓试错' if temperature >= 30 else
                          '空仓观望',
            },
            'total_amount_yi': round(total_amount, 0),
            'prev_day': prev_info,
        }

    def _get_major_indices(self, spot_df) -> List[dict]:
        """获取主要指数涨跌。"""
        # 从TDX读取指数数据
        indices = []
        index_codes = {
            '999999': ('上证指数', 'sh'),
            '399001': ('深证成指', 'sz'),
            '399006': ('创业板指', 'sz'),
            '688001': ('科创50', 'sh'),  # 用 000688
        }

        # 改用正确的指数代码
        correct_codes = {
            '999999': ('上证指数', 'sh'),
            '399001': ('深证成指', 'sz'),
            '399006': ('创业板指', 'sz'),
            '000688': ('科创50', 'sh'),
        }

        for code, (name, market) in correct_codes.items():
            try:
                df = self.tdx.read_daily(code, market)
                if df is not None and len(df) >= 2:
                    today = float(df['close'].iloc[-1])
                    yesterday = float(df['close'].iloc[-2])
                    chg_pct = (today - yesterday) / yesterday * 100 if yesterday > 0 else 0
                    today_open = float(df['open'].iloc[-1])
                    open_chg = (today_open - yesterday) / yesterday * 100 if yesterday > 0 else 0
                    indices.append({
                        'name': name,
                        'code': code,
                        'close': round(today, 2),
                        'change_pct': round(chg_pct, 2),
                        'open_change_pct': round(open_chg, 2),
                        'volume': int(float(df['volume'].iloc[-1])),
                    })
            except Exception:
                pass

        return indices

    def _get_prev_market_day(self) -> dict:
        """获取前一交易日涨跌家数对比（简化版：用上证涨跌幅代表）"""
        try:
            df = self.tdx.read_daily('999999', 'sh')
            if df is not None and len(df) >= 3:
                today_chg = (float(df['close'].iloc[-1]) - float(df['close'].iloc[-2])) / float(df['close'].iloc[-2]) * 100
                yesterday_chg = (float(df['close'].iloc[-2]) - float(df['close'].iloc[-3])) / float(df['close'].iloc[-3]) * 100
                return {
                    'today_index_chg': round(today_chg, 2),
                    'yesterday_index_chg': round(yesterday_chg, 2),
                    'trend': '↑连续走强' if today_chg > 0 and yesterday_chg > 0 else
                             '↓连续走弱' if today_chg < 0 and yesterday_chg < 0 else
                             '↗反弹' if today_chg > 0 and yesterday_chg < 0 else
                             '↘转弱',
                }
        except Exception:
            pass
        return {}

    # ═══════════════════════════════════════════════════════════════════════
    # 三维：板块热点
    # ═══════════════════════════════════════════════════════════════════════

    def _analyze_sectors(self, limit_ups: List, auctions: List) -> dict:
        """板块热点分析。"""
        # ── 涨停板块分布 ──
        sector_zt = Counter()
        sector_leader = {}  # sector → leader_code
        for lu in limit_ups:
            sec = lu.board_type or '其他'
            sector_zt[sec] += 1
            if sec not in sector_leader or lu.consecutive > sector_leader.get(sec + '_cons', 0):
                sector_leader[sec] = lu.code
                sector_leader[sec + '_cons'] = lu.consecutive

        top_sectors = sector_zt.most_common(15)
        sector_list = []
        for sec, count in top_sectors:
            leader_code = sector_leader.get(sec, '')
            leader_name = ''
            if leader_code:
                # 从涨停池找leader名称
                for lu in limit_ups:
                    if lu.code == leader_code:
                        leader_name = lu.name
                        break
            sector_list.append({
                'sector': sec,
                'zt_count': count,
                'leader_code': leader_code,
                'leader_name': leader_name or self._get_name(leader_code),
            })

        # ── 板块竞价强度（板块内竞价高开的股票数） ──
        auction_map = {a.code: a for a in auctions}
        sector_auction_strength = defaultdict(lambda: {'high_open': 0, 'total': 0, 'top_amount': 0})
        for lu in limit_ups:
            sec = lu.board_type or '其他'
            a = auction_map.get(lu.code)
            if a:
                sector_auction_strength[sec]['total'] += 1
                if a.open_change_pct >= 3:
                    sector_auction_strength[sec]['high_open'] += 1
                sector_auction_strength[sec]['top_amount'] += a.auction_amount

        # 取竞价最强的板块
        sector_strength_list = []
        for sec, info in sector_auction_strength.items():
            if info['total'] >= 2:
                strength = info['high_open'] / max(info['total'], 1)
                sector_strength_list.append({
                    'sector': sec,
                    'zt_count': sector_zt.get(sec, 0),
                    'high_open_ratio': round(strength * 100, 1),
                    'avg_auction_amount': round(info['top_amount'] / max(info['total'], 1), 0),
                })
        sector_strength_list.sort(key=lambda x: -x['high_open_ratio'])
        sector_strength_list = sector_strength_list[:10]

        # ── 热点板块主线判断 ──
        main_line = '无明确主线'
        if top_sectors:
            top3_total = sum(c for _, c in top_sectors[:3])
            if top3_total >= 15:
                main_line = f'{top_sectors[0][0]}领涨({top_sectors[0][1]}家)+{top_sectors[1][0]}({top_sectors[1][1]}家)'
            elif top_sectors[0][1] >= 5:
                main_line = f'{top_sectors[0][0]}单主线({top_sectors[0][1]}家)'

        return {
            'main_line': main_line,
            'top_sectors': sector_list,
            'strong_auction_sectors': sector_strength_list,
            'total_sectors_with_zt': len([s for s, c in top_sectors if c > 0]),
        }

    # ═══════════════════════════════════════════════════════════════════════
    # 四维：连板梯队
    # ═══════════════════════════════════════════════════════════════════════

    def _analyze_ladder(self, limit_ups: List, auctions: List) -> dict:
        """连板梯队分析。"""
        if not limit_ups:
            return {'error': '无涨停数据', 'ladder': []}

        auction_map = {a.code: a for a in auctions}

        # ── 按连板数分组 ──
        by_cons = defaultdict(list)
        for lu in limit_ups:
            cons = max(1, lu.consecutive)
            by_cons[cons].append(lu)

        # ── 最高板 ──
        max_cons = max(by_cons.keys()) if by_cons else 0
        top_dragons = by_cons.get(max_cons, [])

        # ── 构建梯队 ──
        ladder = []
        for cons in sorted(by_cons.keys(), reverse=True):
            stocks = by_cons[cons]
            level_name = f'{cons}连板'
            if cons >= 5:
                level_label = '🐉妖股'
            elif cons >= 4:
                level_label = '👑空间龙'
            elif cons >= 3:
                level_label = '🔥高度板'
            elif cons >= 2:
                level_label = '📈接力板'
            else:
                level_label = '1️⃣首板'

            stock_list = []
            for lu in sorted(stocks, key=lambda x: -(x.seal_amount or 0))[:20]:
                a = auction_map.get(lu.code)
                auction_info = None
                if a:
                    auction_info = {
                        'open_pct': round(a.open_change_pct, 1),
                        'amount': round(a.auction_amount, 0),
                        'vol_0925': a.vol_0925,
                    }

                # 判断封板质量
                is_yizi = lu.board_type == '一字板'
                seal_quality = '🟢一字板' if is_yizi else \
                               '🟢封死' if lu.is_seal and not lu.is_broken else \
                               '🟡炸板回封' if lu.is_seal and lu.is_broken else \
                               '🔴未封死'

                stock_list.append({
                    'code': lu.code,
                    'name': lu.name or self._get_name(lu.code),
                    'consecutive': lu.consecutive,
                    'board_type': lu.board_type or '换手板',
                    'seal_quality': seal_quality,
                    'seal_amount': round(lu.seal_amount, 0),
                    'float_market_cap': round(lu.float_market_cap, 0),
                    'limit_up_time': lu.limit_up_time,
                    'auction': auction_info,
                })

            ladder.append({
                'level': cons,
                'label': level_label,
                'name': level_name,
                'count': len(stocks),
                'stocks': stock_list[:15],
            })

        # ── 断板预警（昨日涨停今日未涨停+低开） ──
        broken_warnings = []
        for lu in limit_ups:
            a = auction_map.get(lu.code)
            if a and a.open_change_pct < -2 and lu.consecutive >= 2:
                broken_warnings.append({
                    'code': lu.code,
                    'name': lu.name or self._get_name(lu.code),
                    'consecutive': lu.consecutive,
                    'open_pct': round(a.open_change_pct, 1),
                    'warning': '⚠昨日连板今日低开·警惕核按钮',
                })

        # ── 龙虎榜概览 ──
        top_dragon_list = []
        for lu in sorted(top_dragons, key=lambda x: -(x.consecutive or 0))[:5]:
            a = auction_map.get(lu.code)
            top_dragon_list.append({
                'code': lu.code,
                'name': lu.name or self._get_name(lu.code),
                'consecutive': lu.consecutive,
                'board_type': lu.board_type or '换手板',
                'seal_amount': round(lu.seal_amount, 0),
                'float_market_cap': round(lu.float_market_cap, 0),
                'open_pct': round(a.open_change_pct, 1) if a else 0,
            })

        # ── 1进2候补（昨日首板，今日竞价表现） ──
        one_to_two = []
        for lu in limit_ups:
            if lu.consecutive != 1 or not lu.is_first:
                continue
            if lu.board_type == '一字板':
                continue
            a = auction_map.get(lu.code)
            if a and a.open_change_pct >= 2:
                yesterday_max = self._read_trailing_max_volume(lu.code)
                vol_ratio = round(a.auction_volume / max(yesterday_max, 1), 2) if yesterday_max > 0 else 0
                one_to_two.append({
                    'code': lu.code,
                    'name': lu.name or self._get_name(lu.code),
                    'limit_up_time': lu.limit_up_time,
                    'seal_amount': round(lu.seal_amount, 0),
                    'open_pct': round(a.open_change_pct, 1),
                    'vol_ratio': vol_ratio,
                    'score': self._score_one_to_two(lu, a, vol_ratio),
                })
        one_to_two.sort(key=lambda x: -x['score'])
        one_to_two = one_to_two[:15]

        return {
            'max_consecutive': max_cons,
            'total_limit_ups': len(limit_ups),
            'ladder': ladder,
            'top_dragons': top_dragon_list,
            'broken_warnings': broken_warnings[:10],
            'one_to_two_candidates': one_to_two,
            # 梯队统计
            'summary': {
                f'{c}连板': len(by_cons.get(c, []))
                for c in sorted(by_cons.keys(), reverse=True)
            },
        }

    def _score_one_to_two(self, lu, a, vol_ratio: float) -> int:
        """对1进2候补打分。"""
        score = 30
        # 封板时间
        try:
            t_str = str(lu.limit_up_time).replace(':', '')[:4]
            t = int(t_str) if t_str else 1500
            if t <= 1000: score += 25
            elif t <= 1030: score += 18
            elif t <= 1100: score += 10
            elif t <= 1400: score += 5
        except (ValueError, TypeError):
            pass

        # 竞价高开
        if a.open_change_pct >= 5: score += 20
        elif a.open_change_pct >= 3: score += 12
        elif a.open_change_pct >= 2: score += 6

        # 量比
        if vol_ratio >= 0.5: score += 20
        elif vol_ratio >= 0.3: score += 10
        elif vol_ratio >= 0.15: score += 5

        # 流通市值
        if 10 <= lu.float_market_cap <= 80: score += 10
        elif lu.float_market_cap <= 100: score += 5

        # 封单强度
        if lu.float_market_cap > 0 and lu.seal_amount > 0:
            strength = lu.seal_amount / (lu.float_market_cap * 10000)
            if strength > 0.02: score += 10
            elif strength > 0.01: score += 5

        return min(100, score)

    # ═══════════════════════════════════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════════════════════════════════

    def _read_trailing_max_volume(self, code: str) -> int:
        """快速读取 TDX .day 文件尾部 20 条记录的成交量最大值。"""
        market = 'sh' if code.startswith('6') else 'sz'
        if code.startswith(('8', '4')):
            market = 'bj'
        try:
            mkt_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), 'data',
                'tdx', market
            )
            # TdxReader 内部的 _market_dir
            tdx_mkt = self.tdx._market_dir(market) if hasattr(self.tdx, '_market_dir') else mkt_dir
            fpath = os.path.join(tdx_mkt, f'{market}{code}.day')
        except Exception:
            return 0

        if not os.path.exists(fpath):
            return 0
        fsize = os.path.getsize(fpath)
        if fsize < RECORD_SIZE * 2:
            return 0
        read_size = min(RECORD_SIZE * 20, fsize)
        with open(fpath, 'rb') as f:
            f.seek(fsize - read_size)
            tail = f.read(read_size)
        max_vol = 0
        for i in range(len(tail) // RECORD_SIZE):
            offset = i * RECORD_SIZE
            vol = struct.unpack('I', tail[offset+24:offset+28])[0]
            if vol > max_vol:
                max_vol = vol
        return max_vol


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='竞价分析')
    p.add_argument('--json', action='store_true', help='输出JSON')
    args = p.parse_args()

    analyzer = AuctionAnalyzer()
    result = analyzer.analyze()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        # 格式化输出
        opening = result['auction_opening']
        market = result['market_env']
        sectors = result['sector_heat']
        ladder = result['limit_up_ladder']

        print(f"\n{'='*65}")
        print(f"  📊 竞价分析 — {result['timestamp']}")
        print(f"{'='*65}")

        print(f"\n  ═══ 一、开盘竞价 ═══")
        print(f"  情绪: {opening.get('opening_mood', 'N/A')}")
        dist = opening.get('distribution', {})
        print(f"  分布: " + ' | '.join(f'{k}:{v}' for k, v in dist.items()))

        print(f"\n  ═══ 二、大盘环境 ═══")
        for idx in market.get('indices', []):
            print(f"  {idx['name']}: {idx['close']} ({idx['change_pct']:+.2f}%)")
        bread = market.get('breadth', {})
        print(f"  涨跌: ↑{bread.get('up', 0)} ↓{bread.get('down', 0)} "
              f"(上涨率{bread.get('up_ratio', 0)}%)")
        sent = market.get('sentiment', {})
        print(f"  情绪温度: {sent.get('temperature', 0)}° {sent.get('label', '')}"
              f" → {sent.get('advice', '')}")

        print(f"\n  ═══ 三、板块热点 ═══")
        print(f"  主线: {sectors.get('main_line', 'N/A')}")
        for s in sectors.get('top_sectors', [])[:8]:
            print(f"  {s['sector']}: {s['zt_count']}家涨停 "
                  f"龙头:{s.get('leader_name', '?')}")

        print(f"\n  ═══ 四、连板梯队 ═══")
        print(f"  最高板: {ladder.get('max_consecutive', 0)}连板")
        print(f"  涨停总数: {ladder.get('total_limit_ups', 0)}只")
        for level in ladder.get('ladder', []):
            print(f"  {level['label']} {level['name']}: {level['count']}只")
        for w in ladder.get('broken_warnings', [])[:5]:
            print(f"  ⚠ {w['name']}({w['consecutive']}连板) 低开{w['open_pct']}%")

        print(f"\n{'='*65}\n")
