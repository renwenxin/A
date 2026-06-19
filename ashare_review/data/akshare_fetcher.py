"""akshare数据获取 + SQLite缓存

适配 akshare 各版本字段名差异，自动检测并映射。
网络环境: 部分 eastmoney 推流主机被代理拦截时，自动切换同花顺等备用数据源。
"""
import akshare as ak
import pandas as pd
import sqlite3
import json
import os
import struct
import time
import requests
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from .models import LimitUpInfo, AuctionInfo, LhbInfo, StockInfo


def _clean_proxy():
    """清除代理环境变量，避免部分 eastmoney 推流主机连接失败"""
    for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY'):
        os.environ.pop(k, None)


class AkshareFetcher:
    """A股行情数据获取器，带SQLite缓存和自动降级"""

    def __init__(self, cache_db: str = 'ashare_review/cache.db'):
        self.cache_db = cache_db
        _clean_proxy()
        self._init_cache()

    # ------------------------------------------------------------------
    # 缓存
    # ------------------------------------------------------------------
    def _init_cache(self):
        conn = sqlite3.connect(self.cache_db, timeout=10)
        conn.execute('PRAGMA journal_mode=WAL')       # WAL 模式允许读写并发
        conn.execute('PRAGMA busy_timeout=5000')       # 忙等待 5 秒
        conn.execute('''CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY, data TEXT, updated TIMESTAMP)''')
        conn.commit()
        conn.close()

    def _cache_get(self, key: str, ttl_minutes: int = 5) -> Optional[str]:
        try:
            conn = sqlite3.connect(self.cache_db, timeout=5)
            row = conn.execute(
                'SELECT data, updated FROM cache WHERE key=?', (key,)
            ).fetchone()
            conn.close()
            if row:
                age = (datetime.now() - datetime.fromisoformat(row[1])).total_seconds()
                if age < ttl_minutes * 60:
                    return row[0]
        except Exception:
            pass  # 缓存读取失败不影响主流程
        return None

    def _cache_set(self, key: str, data: str):
        try:
            conn = sqlite3.connect(self.cache_db, timeout=10)
            conn.execute('INSERT OR REPLACE INTO cache VALUES (?, ?, ?)',
                         (key, data, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        except Exception:
            pass  # 缓存写入失败不影响主流程

    # ------------------------------------------------------------------
    # 涨停池
    # ------------------------------------------------------------------
    def get_limit_up_pool(self, trade_date: Optional[str] = None) -> List[LimitUpInfo]:
        """当日涨停板列表 — 自动适配 akshare 各版本字段名"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        cache_key = f'zt_pool_{trade_date}'
        cached = self._cache_get(cache_key, ttl_minutes=5)
        if cached:
            return [LimitUpInfo(**d) for d in json.loads(cached)]

        df = None
        # 优先使用 stock_zt_pool_em（含 代码 + 封单资金 + 首次封板时间 + 炸板次数）
        try:
            df = ak.stock_zt_pool_em(date=trade_date)
        except Exception:
            pass
        # 备用: stock_zt_pool_strong_em（无 代码 列！需从 名称 反查或用 序号）
        if df is None or df.empty:
            try:
                df = ak.stock_zt_pool_strong_em(date=trade_date)
            except Exception:
                return []

        if df is None or df.empty:
            return []

        cols = list(df.columns)
        print(f"[akshare] 涨停池列名: {cols}")

        # ----- 字段自适应映射 -----
        # 代码
        code_col = next((c for c in cols if c == '代码'), None)
        # 名称
        name_col = next((c for c in cols if c == '名称'), None)

        # 涨停时间: 首次封板时间 > 封板时间 > 涨停时间
        time_col = next((c for c in cols if c in ('首次封板时间', '封板时间', '涨停时间')), None)

        # 封单额(元): 封单资金 > 封单额
        seal_col = next((c for c in cols if c in ('封单资金', '封单额')), None)

        # 成交额(元)
        turnover_col = next((c for c in cols if c == '成交额'), None)

        # 流通市值(元)
        cap_col = next((c for c in cols if c == '流通市值'), None)

        # 连板数
        cons_col = next((c for c in cols if c == '连板数'), None)

        # 炸板次数
        broken_col = next((c for c in cols if c == '炸板次数'), None)

        # 涨停统计 (格式: '封板次数/总次数')
        stat_col = next((c for c in cols if c == '涨停统计'), None)

        # 所属行业
        industry_col = next((c for c in cols if c == '所属行业'), None)

        results = []
        for _, row in df.iterrows():
            # 代码：优先从 代码 列取，没有则从 名称 反查（不精确但至少不崩溃）
            code = str(row.get(code_col, '')) if code_col else ''
            if not code or code == 'nan':
                # strong_em 没有代码列，尝试从缓存/名称匹配
                code = ''
            code = code.zfill(6) if code and code != '0' * len(code) else ''

            if not code:
                continue  # 无代码的跳过（strong_em 需要额外处理）

            # 涨停时间
            limit_up_time = str(row.get(time_col, '')) if time_col else ''

            # 封单额（元→万元）
            seal_amount = 0.0
            if seal_col:
                try:
                    seal_amount = float(row[seal_col]) / 10000
                except (ValueError, TypeError):
                    pass

            # 成交额（元→万元）
            turnover = 0.0
            if turnover_col:
                try:
                    turnover = float(row[turnover_col]) / 10000
                except (ValueError, TypeError):
                    pass

            # 流通市值（元→亿）
            float_market_cap = 0.0
            if cap_col:
                try:
                    float_market_cap = float(row[cap_col]) / 1e8
                except (ValueError, TypeError):
                    pass

            # 连板数
            consecutive = 1
            if cons_col:
                try:
                    consecutive = int(row[cons_col])
                except (ValueError, TypeError):
                    pass

            # 炸板次数 → is_broken
            broken_cnt = 0
            broken_known = False  # 是否从可靠来源获取了炸板信息
            if broken_col:
                try:
                    raw_broken = row[broken_col]
                    broken_cnt = int(raw_broken)
                    broken_known = True
                except (ValueError, TypeError):
                    pass
            is_broken = broken_cnt > 0

            # 封死判断 — 优先用炸板次数，缺失时用涨停统计兜底
            is_seal = True
            if broken_known:
                is_seal = broken_cnt == 0
            elif stat_col:
                # 涨停统计格式: '封板次数/总次数' 或 '/'. 如 '1/1' 表示封死，'0/1' 或 '/1' 表示炸板
                stat_raw = str(row.get(stat_col, ''))
                if '/' in stat_raw:
                    parts = stat_raw.split('/')
                    try:
                        sealed_cnt = int(parts[0]) if parts[0].strip() else 0
                        is_seal = sealed_cnt > 0
                    except (ValueError, IndexError):
                        pass
                elif stat_raw.isdigit():
                    is_seal = int(stat_raw) > 0
            # else: 既无炸板次数也无涨停统计列，保守默认 True（涨跌停池中绝大部分是封死状态）

            # 首板 = 连板数 == 1
            is_first = consecutive <= 1

            # 板型: 用 所属行业 代替（原 API 无涨停类型字段），同时保留涨停统计
            board_type = ''
            if industry_col:
                board_type = str(row.get(industry_col, ''))

            results.append(LimitUpInfo(
                code=code,
                name=str(row.get(name_col, '')) if name_col else '',
                limit_up_time=limit_up_time,
                seal_amount=seal_amount,
                turnover=turnover,
                float_market_cap=float_market_cap,
                consecutive=consecutive,
                is_first=is_first,
                is_seal=is_seal,
                is_broken=is_broken,
                board_type=board_type,
            ))

        print(f"[akshare] 涨停池解析: {len(results)} 只 (总数{len(df)})")

        if results:
            self._cache_set(cache_key, json.dumps([{
                'code': r.code, 'name': r.name, 'limit_up_time': r.limit_up_time,
                'seal_amount': r.seal_amount, 'turnover': r.turnover,
                'float_market_cap': r.float_market_cap, 'consecutive': r.consecutive,
                'is_first': r.is_first, 'is_seal': r.is_seal, 'is_broken': r.is_broken,
                'board_type': r.board_type
            } for r in results]))
        return results

    # ------------------------------------------------------------------
    # 龙虎榜
    # ------------------------------------------------------------------
    def get_lhb(self, trade_date: Optional[str] = None) -> List[LhbInfo]:
        """龙虎榜 — 使用 start_date/end_date 参数"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        cache_key = f'lhb_{trade_date}'
        cached = self._cache_get(cache_key, ttl_minutes=60)
        if cached:
            return [LhbInfo(**d) for d in json.loads(cached)]

        results = []
        try:
            df = ak.stock_lhb_detail_em(start_date=trade_date, end_date=trade_date)
            if df is not None and not df.empty:
                cols = list(df.columns)
                print(f"[akshare] 龙虎榜列名: {cols}")
                # 实际列名: 序号, 代码, 名称, 上榜日, 解读, 收盘价, 涨跌幅,
                #   龙虎榜净买额, 龙虎榜买入额, 龙虎榜卖出额, 龙虎榜成交额, 市场总成交额,
                #   净买额占总成交比, 成交额占总成交比, 换手率, 流通市值, 上榜原因,
                #   上榜后1日, 上榜后2日, 上榜后5日, 上榜后10日
                code_col = next((c for c in cols if c == '代码'), None)
                name_col = next((c for c in cols if c == '名称'), None)
                reason_col = next((c for c in cols if c == '上榜原因'), None)
                buy_col = next((c for c in cols if c in ('龙虎榜买入额', '买入金额', '买入总计')), None)
                sell_col = next((c for c in cols if c in ('龙虎榜卖出额', '卖出金额', '卖出总计')), None)
                net_col = next((c for c in cols if c in ('龙虎榜净买额', '买卖净额', '净买额', '净买入额')), None)

                for _, row in df.iterrows():
                    code = str(row.get(code_col, '')).zfill(6) if code_col else ''
                    if not code or code == '000000':
                        continue
                    buy_raw = float(row.get(buy_col, 0) or 0) if buy_col else 0
                    sell_raw = float(row.get(sell_col, 0) or 0) if sell_col else 0
                    net_raw = float(row.get(net_col, 0) or 0) if net_col else (buy_raw - sell_raw)
                    results.append(LhbInfo(
                        code=code,
                        name=str(row.get(name_col, '')) if name_col else '',
                        trade_date=date.today(),
                        reason=str(row.get(reason_col, '')) if reason_col else '',
                        buy_amount=buy_raw / 10000,
                        sell_amount=sell_raw / 10000,
                        net_amount=net_raw / 10000,
                        seats=[]
                    ))
                print(f"[akshare] 龙虎榜解析: {len(results)} 条")
        except Exception as e:
            print(f"[akshare] 龙虎榜主接口失败: {e}")

        # 备用
        if not results:
            results = self.get_lhb_fallback(trade_date)

        if results:
            self._cache_set(cache_key, json.dumps([{
                'code': r.code, 'name': r.name, 'trade_date': str(r.trade_date),
                'reason': r.reason, 'buy_amount': r.buy_amount,
                'sell_amount': r.sell_amount, 'net_amount': r.net_amount, 'seats': r.seats
            } for r in results]))
        return results

    def get_lhb_fallback(self, trade_date: Optional[str] = None) -> List[LhbInfo]:
        """龙虎榜备用：stock_lhb_stock_em"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        cache_key = f'lhb_fb_{trade_date}'
        cached = self._cache_get(cache_key, ttl_minutes=60)
        if cached:
            return [LhbInfo(**d) for d in json.loads(cached)]
        results = []
        try:
            df = ak.stock_lhb_stock_em(date=trade_date)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.get('代码', '')).zfill(6)
                    if not code or code == '000000':
                        continue
                    buy_raw = float(row.get('买入金额', row.get('买入总计', 0)) or 0)
                    sell_raw = float(row.get('卖出金额', row.get('卖出总计', 0)) or 0)
                    net_raw = float(row.get('净买入额', row.get('净买额', 0)) or 0)
                    if net_raw == 0:
                        net_raw = buy_raw - sell_raw
                    results.append(LhbInfo(
                        code=code, name=str(row.get('名称', '')),
                        trade_date=date.today(),
                        reason=str(row.get('上榜原因', '')),
                        buy_amount=buy_raw / 10000,
                        sell_amount=sell_raw / 10000,
                        net_amount=net_raw / 10000,
                        seats=[]
                    ))
        except Exception as e:
            print(f"[akshare] 龙虎榜备用接口失败: {e}")
        if results:
            self._cache_set(cache_key, json.dumps([{
                'code': r.code, 'name': r.name, 'trade_date': str(r.trade_date),
                'reason': r.reason, 'buy_amount': r.buy_amount,
                'sell_amount': r.sell_amount, 'net_amount': r.net_amount, 'seats': r.seats
            } for r in results]))
        return results

    # ------------------------------------------------------------------
    # 集合竞价
    # ------------------------------------------------------------------
    _AUCTION_EM_BASE = 'http://push2ex.eastmoney.com/getStockFenShi'
    _CLIST_EM_BASE = 'https://push2.eastmoney.com/api/qt/clist/get'

    @staticmethod
    def _is_a_stock(code: str) -> bool:
        """判断代码是否为A股（排除可转债、基金、债券等）"""
        if len(code) != 6:
            return False
        # 深市: 000-003 主板, 300-301 创业板
        # 沪市: 600-605 主板, 688 科创板
        if code.startswith(('000', '001', '002', '003')):
            return True
        if code.startswith(('300', '301')):
            return True
        if code.startswith(('600', '601', '603', '605')):
            return True
        if code.startswith('688'):
            return True
        return False

    def get_auction_data(self) -> List[AuctionInfo]:
        """获取全市场集合竞价数据

        优先使用东方财富 clist API 获取股票列表（带开盘涨跌幅，可预筛选），
        clist 被限流时自动回退到 TDX 本地股票列表。
        结果按交易日缓存 4 小时，覆盖整个盘中时段。
        """
        cache_key = f'auction_{datetime.now().strftime("%Y%m%d")}'
        cached = self._cache_get(cache_key, ttl_minutes=240)
        if cached:
            return [AuctionInfo(**d) for d in json.loads(cached)]

        results = []
        try:
            # 第一步：获取股票列表（clist 优先，TDX 回退）
            stock_list = self._fetch_stock_list_clist()
            use_tdx_fallback = False

            if not stock_list:
                print('[auction] clist 失败，回退到 TDX 本地股票列表')
                stock_list = self._fetch_stock_list_tdx()
                use_tdx_fallback = True

            if not stock_list:
                print('[auction] TDX 股票列表也为空，放弃')
                return []

            # 第二步：筛选有竞价异动潜力的股票
            # clist 路径：有涨跌幅和量比，可精准筛选
            # TDX 路径：无涨跌幅数据，全量获取
            if use_tdx_fallback:
                candidates = stock_list   # 全量获取
                rest = []
            else:
                candidates, rest = [], []
                for s in stock_list:
                    if abs(s['open_change_pct']) >= 1.0 or s['volume_ratio'] >= 2.0:
                        candidates.append(s)
                    else:
                        rest.append(s)

            # 第三步：为所有股票生成基础 AuctionInfo
            base_map = {}
            for s in stock_list:
                info = AuctionInfo(
                    code=s['code'], name=s['name'],
                    auction_volume=0, auction_amount=0,
                    auction_price=s.get('open', 0),
                    open_change_pct=s.get('open_change_pct', 0),
                    preclose_volume=0,
                )
                results.append(info)
                base_map[s['code']] = info

            print(f'[auction] 候选竞价异动: {len(candidates)} 只')

            # 第四步：并发获取候选股票的竞价分时明细
            if candidates:
                start = time.time()
                with ThreadPoolExecutor(max_workers=20) as pool:
                    futures = {
                        pool.submit(self._fetch_one_auction, s['code'], s['market']): s['code']
                        for s in candidates
                    }
                    for future in as_completed(futures):
                        try:
                            detail = future.result()
                            code = futures[future]
                            if detail is not None and code in base_map:
                                base_map[code].auction_volume = detail.auction_volume
                                base_map[code].auction_amount = detail.auction_amount
                                if detail.auction_price > 0:
                                    base_map[code].auction_price = detail.auction_price
                                if detail.open_change_pct != 0:
                                    base_map[code].open_change_pct = detail.open_change_pct
                                # 用 push2ex 返回的名称覆盖（TDX 回退时名称为空）
                                if detail.name:
                                    base_map[code].name = detail.name
                        except Exception:
                            pass
                elapsed = time.time() - start
                print(f'[auction] 竞价明细获取完成, 耗时 {elapsed:.0f}s')

            print(f'[auction] 总计: {len(results)} 只')

            # 第五步：剔除 ST / *ST 股票
            # clist 路径已在 _fetch_stock_list_clist 中过滤，这里是 TDX 回退路径的补充过滤
            st_names = {'ST', '*ST', 'SST', 'S*ST', 'NST'}
            before = len(results)
            results = [r for r in results
                       if not any(r.name.startswith(p) for p in st_names)]
            if len(results) < before:
                print(f'[auction] 剔除 ST: {before} → {len(results)} 只')

        except Exception as e:
            print(f'[auction] 获取失败: {e}')

        if results:
            self._cache_set(cache_key, json.dumps([{
                'code': r.code, 'name': r.name, 'auction_volume': r.auction_volume,
                'auction_amount': r.auction_amount, 'auction_price': r.auction_price,
                'open_change_pct': r.open_change_pct, 'preclose_volume': r.preclose_volume,
                'vol_0924': r.vol_0924, 'vol_0925': r.vol_0925,
            } for r in results]))
        return results

    def _fetch_stock_list_clist(self) -> List[dict]:
        """从东方财富 clist API 获取全市场A股列表及开盘数据（并发翻页）

        返回 [{'code', 'market', 'name', 'open', 'prev_close',
               'open_change_pct', 'volume', 'amount', 'volume_ratio'}, ...]
        market: '0'=深圳, '1'=上海
        """
        url = self._CLIST_EM_BASE
        base_params = {
            'fid': 'f3', 'po': '1', 'pz': '100',
            'np': '1', 'fltt': '2', 'invt': '2',
            'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',
            'fields': 'f2,f3,f5,f6,f8,f12,f14,f17,f18',
        }

        # 先获取第一页确定总页数
        try:
            resp = requests.get(url, params={**base_params, 'pn': '1'}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            total = data.get('data', {}).get('total', 0)
            if total == 0:
                return []
            total_pages = min((total + 99) // 100, 60)
        except Exception as e:
            print(f'[auction] clist 首页请求失败: {e}')
            return []

        # 并发获取所有页
        def _fetch_page(pn: int) -> List[dict]:
            try:
                r = requests.get(url, params={**base_params, 'pn': str(pn)}, timeout=15)
                r.raise_for_status()
                d = r.json()
                items = []
                for item in (d.get('data', {}).get('diff') or []):
                    code = str(item.get('f12', '')).zfill(6)
                    if not code or len(code) != 6:
                        continue
                    name = str(item.get('f14', ''))
                    # 剔除 ST / *ST / SST / S*ST 等风险警示股
                    if name.startswith(('ST', '*ST', 'SST', 'S*ST', 'NST')):
                        continue
                    open_price = float(item.get('f17', 0) or 0)
                    prev_close = float(item.get('f18', 0) or 0)
                    if prev_close <= 0 or open_price == 0:
                        continue
                    market = '1' if code.startswith('6') else '0'
                    open_change_pct = (open_price - prev_close) / prev_close * 100
                    items.append({
                        'code': code,
                        'market': market,
                        'name': name,
                        'open': open_price,
                        'prev_close': prev_close,
                        'open_change_pct': round(open_change_pct, 2),
                        'volume': float(item.get('f5', 0) or 0),       # 成交量(手)
                        'amount': float(item.get('f6', 0) or 0),       # 成交额(元)
                        'volume_ratio': float(item.get('f8', 0) or 0), # 量比
                    })
                return items
            except Exception:
                return []

        results = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_fetch_page, p): p for p in range(1, total_pages + 1)}
            for future in as_completed(futures):
                try:
                    items = future.result()
                    if items:
                        results.extend(items)
                except Exception:
                    pass

        print(f'[auction] clist 并发翻页完成: {len(results)} 只')
        return results

    def _fetch_stock_list_tdx(self) -> List[dict]:
        """从 TDX 本地 .day 文件获取全市场股票列表（clist 被限流时的回退方案）

        返回 [{'code', 'market', 'name', 'open', 'prev_close',
               'open_change_pct', 'volume', 'amount', 'volume_ratio'}, ...]
        注意：TDX 数据不含实时涨跌幅，open/open_change_pct/volume_ratio 均为 0，
        因此后续会走全量 push2ex 获取路径。
        """
        try:
            from .tdx_reader import TdxReader, RECORD_SIZE as _REC
            tdx = TdxReader()
            stocks = tdx.list_stocks()
            results = []
            for code, market in stocks:
                # 过滤北交所 + 非股票品种（可转债/基金/债券等）
                if market == 'bj':
                    continue
                if not self._is_a_stock(code):
                    continue
                # 读最后一条记录获取最新收盘价
                mkt_dir = {'sh': 'sh', 'sz': 'sz'}.get(market, market)
                fpath = os.path.join(tdx._market_dir(mkt_dir), f'{mkt_dir}{code}.day')
                if not os.path.exists(fpath):
                    continue
                fsize = os.path.getsize(fpath)
                if fsize < 32:
                    continue
                with open(fpath, 'rb') as f:
                    f.seek(fsize - 32)
                    last = f.read(32)
                _, _, _, _, close, _, _, _ = struct.unpack('IIIIIfII', last)
                if close == 0:
                    continue
                name = ''  # TDX .day 不含名称，后续 push2ex 会补充
                # ST 过滤：无法从名称判断，push2ex 返回的名称在后续覆盖
                em_market = '1' if code.startswith('6') else '0'
                results.append({
                    'code': code,
                    'market': em_market,
                    'name': name,
                    'open': 0,
                    'prev_close': close / 100.0,
                    'open_change_pct': 0,
                    'volume': 0,
                    'amount': 0,
                    'volume_ratio': 0,
                })
            # 用 push2ex 获取的名称覆盖，同时过滤 ST
            print(f'[auction] TDX 股票列表: {len(results)} 只')
            return results
        except Exception as e:
            print(f'[auction] TDX 股票列表获取失败: {e}')
            return []

    def _fetch_one_auction(self, code: str, market: str) -> Optional[AuctionInfo]:
        """获取单只股票的竞价分时明细，聚合为 AuctionInfo

        分时数据格式: {"t": 91500, "p": 11240, "v": 204, "bs": 4}
        t=时间(HHMMSS), p=价格*1000, v=成交量(手), bs=买卖方向
        """
        url = self._AUCTION_EM_BASE
        params = {
            'pagesize': '100',
            'ut': '7eea3edcaed734bea9cbfc24409ed989',
            'dpt': 'wzfscj',
            'pageindex': '0',
            'id': code,
            'sort': '1',
            'ft': '1',
            'code': code,
            'market': market,
        }
        try:
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            ds = data.get('data')
            if not ds:
                return None
            records = ds.get('data', [])
            if not records:
                return None

            # 聚合竞价期 (t <= 92500) 的数据，同时按分钟分组
            auction_vol_lots = 0       # 手
            auction_amount_yuan = 0.0  # 元
            auction_price = 0.0
            vol_0924_lots = 0          # 9:24分成交量(手)
            vol_0925_lots = 0          # 9:25分成交量(手)

            for r in records:
                t = r.get('t', 0)
                if t > 92500:
                    break
                v = r.get('v', 0)         # 手
                p = r.get('p', 0) / 1000  # 元/股
                auction_vol_lots += v
                auction_amount_yuan += v * 100 * p
                auction_price = p         # 最后一笔为开盘价

                # 按分钟统计：9:24 (92400-92459) 和 9:25 (92500-92559)
                if 92400 <= t <= 92459:
                    vol_0924_lots += v
                elif 92500 <= t <= 92559:
                    vol_0925_lots += v

            if auction_vol_lots == 0:
                return None

            prev_close = ds.get('cp', 0) / 1000  # 昨收
            open_change_pct = 0.0
            if prev_close > 0 and auction_price > 0:
                open_change_pct = round(
                    (auction_price - prev_close) / prev_close * 100, 2)

            name_raw = ds.get('n', '')
            # 名称可能因编码问题显示为乱码，优先使用 clist 中已正确解析的名称
            # 不做额外编码修复 — 实测 resp.json() 能正确解码 UTF-8 中的中文

            return AuctionInfo(
                code=code,
                name=name_raw,
                auction_volume=int(auction_vol_lots * 100),           # 竞价成交量(股), 与TDX单位一致
                auction_amount=round(auction_amount_yuan / 10000, 2),  # 竞价成交额(万元)
                auction_price=auction_price,
                open_change_pct=open_change_pct,
                preclose_volume=0,
                vol_0924=int(vol_0924_lots * 100),                     # 9:24分竞价量(股)
                vol_0925=int(vol_0925_lots * 100),                     # 9:25分竞价量(股)
            )
        except Exception as e:
            # 单个股票失败不影响全局
            return None

    # ------------------------------------------------------------------
    # 概念板块行情
    # ------------------------------------------------------------------
    def get_concept_boards(self) -> pd.DataFrame:
        """概念板块行情 — em 被代理拦截时降级到 ths"""
        cache_key = 'concept_boards_v2'
        cached = self._cache_get(cache_key, ttl_minutes=10)
        if cached:
            from io import StringIO
            return pd.read_json(StringIO(cached))
        df = self._try_fetch_board('concept')
        if df is not None and not df.empty:
            self._cache_set(cache_key, df.to_json())
        return df if df is not None else pd.DataFrame()

    def get_industry_boards(self) -> pd.DataFrame:
        """行业板块行情"""
        cache_key = 'industry_boards_v2'
        cached = self._cache_get(cache_key, ttl_minutes=10)
        if cached:
            from io import StringIO
            return pd.read_json(StringIO(cached))
        df = self._try_fetch_board('industry')
        if df is not None and not df.empty:
            self._cache_set(cache_key, df.to_json())
        return df if df is not None else pd.DataFrame()

    def _try_fetch_board(self, kind: str) -> Optional[pd.DataFrame]:
        """
        尝试多种方式获取板块行情:
        1. eastmoney (em) — 字段丰富但可能被代理拦截
        2. 同花顺 (ths) — 只有 name+code，需额外获取详情
        3. 从涨停池按行业聚合 — 兜底方案
        """
        # 方案1: eastmoney
        try:
            if kind == 'concept':
                df = ak.stock_board_concept_name_em()
            else:
                df = ak.stock_board_industry_name_em()
            if df is not None and not df.empty:
                print(f"[akshare] {kind}板块(em): {len(df)} 条")
                return df
        except Exception as e:
            print(f"[akshare] {kind}板块(em)失败: {e}")

        # 方案2: 同花顺 (+ 详情)
        try:
            if kind == 'concept':
                df = ak.stock_board_concept_name_ths()
            else:
                df = ak.stock_board_industry_name_ths()
            if df is not None and not df.empty:
                print(f"[akshare] {kind}板块(ths): {len(df)} 条, 列名: {list(df.columns)}")
                # ths 只返回 name + code，格式化为统一结构
                result_rows = []
                for _, row in df.iterrows():
                    result_rows.append({
                        '名称': str(row.get('name', '')),
                        '涨跌幅': 0.0,   # ths 列表无涨跌幅
                        '领涨股票': '',
                    })
                return pd.DataFrame(result_rows)
        except Exception as e:
            print(f"[akshare] {kind}板块(ths)失败: {e}")

        return None

    # ------------------------------------------------------------------
    # 机构持股家数
    # ------------------------------------------------------------------
    def get_institution_holder_count(self) -> Dict[str, int]:
        """获取全市场股票的基金持有家数（季度数据，缓存 30 天）

        通过 stock_report_fund_hold('基金持仓') 获取最新季度数据，
        返回 {code: 持有基金家数}。基金家数是机构持股家数的主要组成部分。
        """
        cache_key = 'inst_holder_count'
        cached = self._cache_get(cache_key, ttl_minutes=43200)  # 30 天
        if cached:
            return json.loads(cached)

        # 计算最近两个季度的日期 (格式: YYYYMMDD)
        today = date.today()
        def _quarter_dates():
            """生成最近两个季度的最后一天"""
            for offset in range(4):  # 最多回退4个季度
                q_month = ((today.month - 1) // 3) * 3 + 3 - offset * 3
                q_year = today.year
                if q_month <= 0:
                    q_month += 12
                    q_year -= 1
                # 季度最后一天
                if q_month in (3, 6, 9, 12):
                    import calendar
                    last_day = calendar.monthrange(q_year, q_month)[1]
                    yield f'{q_year}{q_month:02d}{last_day}'

        result = {}
        quarter_dates = list(_quarter_dates())
        df = None
        try:
            for q_date in quarter_dates:
                try:
                    df = ak.stock_report_fund_hold(symbol='基金持仓', date=q_date)
                    if df is not None and not df.empty:
                        print(f'[inst] 使用季度: {q_date}')
                        break
                except Exception:
                    continue
            else:
                # 所有季度都失败
                df = None

            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    try:
                        code = str(row.iloc[1]).zfill(6)
                        holders = int(row.iloc[3])
                        if code and len(code) == 6:
                            result[code] = holders
                    except (ValueError, IndexError):
                        continue
                print(f'[inst] 机构持股数据: {len(result)} 只')
        except Exception as e:
            print(f'[inst] 机构持股数据获取失败: {e}')

        if result:
            self._cache_set(cache_key, json.dumps(result))
        return result

    # ------------------------------------------------------------------
    # 实时行情快照
    # ------------------------------------------------------------------
    def get_spot_df(self) -> pd.DataFrame:
        """实时行情快照 — clist HTTP API 优先，竞价数据回退，TDX 兜底

        返回 DataFrame，列名兼容 InstitutionScreener: 代码, 名称, 涨跌幅, 流通市值
        """
        cache_key = 'spot_all_v2'
        cached = self._cache_get(cache_key, ttl_minutes=5)
        if cached:
            from io import StringIO
            return pd.read_json(StringIO(cached))

        df = self._fetch_spot_from_clist()
        if df is None or df.empty:
            print('[spot] clist 失败，回退到竞价数据')
            df = self._fetch_spot_from_auction()
        if df is None or df.empty:
            print('[spot] 竞价数据也为空，回退到 TDX 本地数据')
            df = self._fetch_spot_from_tdx()

        if df is not None and not df.empty:
            self._cache_set(cache_key, df.to_json())
        return df if df is not None else pd.DataFrame()

    def _fetch_spot_from_clist(self) -> Optional[pd.DataFrame]:
        """从 clist API 获取全市场实时行情快照（并发翻页）"""
        url = self._CLIST_EM_BASE
        base_params = {
            'fid': 'f3', 'po': '1', 'pz': '100',
            'np': '1', 'fltt': '2', 'invt': '2',
            'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',
            'fields': 'f2,f3,f5,f6,f12,f14,f20,f21',
        }
        # 先获取总页数
        try:
            resp = requests.get(url, params={**base_params, 'pn': '1'}, timeout=15)
            resp.raise_for_status()
            total = resp.json().get('data', {}).get('total', 0)
            if total == 0:
                return None
            total_pages = min((total + 99) // 100, 60)
        except Exception as e:
            print(f'[spot] clist 首页失败: {e}')
            return None

        def _fetch_page(pn: int) -> List[dict]:
            try:
                r = requests.get(url, params={**base_params, 'pn': str(pn)}, timeout=15)
                r.raise_for_status()
                rows = []
                for item in (r.json().get('data', {}).get('diff') or []):
                    name = str(item.get('f14', ''))
                    if name.startswith(('ST', '*ST', 'SST', 'S*ST', 'NST')):
                        continue
                    change_pct = float(item.get('f3', 0) or 0)
                    float_mcap = float(item.get('f21', 0) or 0)  # 流通市值(元)
                    turnover_amount = float(item.get('f6', 0) or 0)  # 成交额(元)
                    rows.append({
                        '代码': str(item.get('f12', '')).zfill(6),
                        '名称': name,
                        '涨跌幅': change_pct,
                        '流通市值': float_mcap,
                        '最新价': float(item.get('f2', 0) or 0),
                        '总市值': float(item.get('f20', 0) or 0),
                        '成交额': turnover_amount,
                    })
                return rows
            except Exception:
                return []

        all_rows = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_fetch_page, p): p for p in range(1, total_pages + 1)}
            for future in as_completed(futures):
                items = future.result()
                if items:
                    all_rows.extend(items)

        print(f'[spot] clist 获取 {len(all_rows)} 只')
        return pd.DataFrame(all_rows) if all_rows else None

    def _fetch_spot_from_auction(self) -> Optional[pd.DataFrame]:
        """从已缓存的竞价数据构造行情快照（clist 被限流时的回退方案）

        用开盘涨跌幅作为日内涨跌幅的近似，流通市值用 TDX 补充。
        """
        try:
            auctions = self.get_auction_data()
            if not auctions:
                return None
            rows = []
            for a in auctions:
                rows.append({
                    '代码': a.code,
                    '名称': a.name,
                    '涨跌幅': a.open_change_pct,   # 开盘涨跌幅近似日内涨跌幅
                    '流通市值': 0,                   # 竞价数据不含市值，后续可从 TDX 补充
                    '最新价': a.auction_price,
                    '总市值': 0,
                    '成交额': 0,  # 竞价成交额与全天成交额差距极大，设为0让 TDX 兜底补充
                })
            print(f'[spot] 竞价数据构造 {len(rows)} 只')
            return pd.DataFrame(rows)
        except Exception as e:
            print(f'[spot] 竞价数据回退失败: {e}')
            return None

    def _fetch_spot_from_tdx(self) -> pd.DataFrame:
        """从 TDX 本地数据构造基础行情快照（无实时涨跌幅，作为 clist 回退）"""
        try:
            from .tdx_reader import TdxReader, RECORD_SIZE as _REC
            tdx = TdxReader()
            stocks = tdx.list_stocks()
            rows = []
            for code, market in stocks:
                if market == 'bj':
                    continue
                if not self._is_a_stock(code):
                    continue
                mkt_dir = {'sh': 'sh', 'sz': 'sz'}.get(market, market)
                fpath = os.path.join(tdx._market_dir(mkt_dir), f'{mkt_dir}{code}.day')
                if not os.path.exists(fpath):
                    continue
                fsize = os.path.getsize(fpath)
                if fsize < 32:
                    continue
                with open(fpath, 'rb') as f:
                    f.seek(fsize - 32)
                    last = f.read(32)
                _, _, _, _, close, _, _, _ = struct.unpack('IIIIIfII', last)
                if close == 0:
                    continue
                rows.append({
                    '代码': code,
                    '名称': '',
                    '涨跌幅': 0,
                    '流通市值': 0,
                    '最新价': close / 100.0,
                    '总市值': 0,
                    '成交额': 0,  # TDX 不含日成交额，clist 回退时用 0 占位
                })
            print(f'[spot] TDX 获取 {len(rows)} 只')
            return pd.DataFrame(rows)
        except Exception as e:
            print(f'[spot] TDX 回退失败: {e}')
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # 板块涨停分布（从涨停池按行业聚合，兜底热点板块）
    # ------------------------------------------------------------------
    def get_sector_zt_distribution(self, trade_date: Optional[str] = None) -> List[dict]:
        """从涨停池按所属行业聚合涨停数，作为热点板块兜底"""
        limit_ups = self.get_limit_up_pool(trade_date)
        from collections import Counter
        sector_count = Counter(lu.board_type for lu in limit_ups if lu.board_type)
        return [{'名称': s, '涨停数': c} for s, c in sector_count.most_common(15)]
