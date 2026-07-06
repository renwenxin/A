"""Flask Web 应用"""
from flask import Flask, render_template, jsonify, request
from ..screening.one_two import OneTwoScreener
from ..screening.institution import InstitutionScreener
from ..screening.leader import LeaderScreener
from ..screening.breakout import BreakoutScreener
from ..screening.sector_divergence import SectorDivergenceScreener
from ..screening.auction import AuctionScreener
from ..alpha.screener import FactorScreener
from ..report.daily import DailyReport
from ..report.weekly import WeeklyReport
from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher
from ..analysis.pick_analysis import analyze_pick
import json
import asyncio

# ---- SSE Streaming Support (added for Vibe-Trading integration) ----
import queue
import threading
import uuid
import time
from flask import Response, stream_with_context

# Simple in-memory task queue (production can use Redis)
_task_queues: dict[str, queue.Queue] = {}
_task_results: dict[str, dict] = {}


def _create_task() -> str:
    task_id = uuid.uuid4().hex[:12]
    _task_queues[task_id] = queue.Queue()
    _task_results[task_id] = {'status': 'pending', 'events': []}
    return task_id


def _emit_event(task_id: str, event_type: str, data: dict):
    msg = json.dumps({'type': event_type, 'data': data}, ensure_ascii=False)
    if task_id in _task_queues:
        _task_queues[task_id].put(msg)
    if task_id in _task_results:
        _task_results[task_id]['events'].append({'type': event_type, 'data': data})


def _complete_task(task_id: str, final_data: dict = None):
    if task_id in _task_results:
        _task_results[task_id]['status'] = 'done'
        if final_data:
            _task_results[task_id]['final'] = final_data
    _emit_event(task_id, 'done', final_data or {})


def _fail_task(task_id: str, error: str):
    if task_id in _task_results:
        _task_results[task_id]['status'] = 'error'
    _emit_event(task_id, 'error', {'message': error})


def _sse_stream(task_id: str):
    """SSE generator — reads from queue and pushes to client"""
    def generate():
        q = _task_queues.get(task_id)
        if not q:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': 'Task not found'}})}\n\n"
            return
        timeout = 300  # 5 minutes total timeout
        start = time.time()
        while time.time() - start < timeout:
            try:
                msg = q.get(timeout=5)
                yield f"data: {msg}\n\n"
                if '"type": "done"' in msg or '"type": "error"' in msg:
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        # Cleanup
        _task_queues.pop(task_id, None)
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

app = Flask(__name__)
tdx = TdxReader()
ak_fetcher = AkshareFetcher()

SCREENERS = {
    'one_two': OneTwoScreener(tdx, ak_fetcher),
    'institution': InstitutionScreener(tdx, ak_fetcher),
    'leader': LeaderScreener(tdx, ak_fetcher),
    'breakout': BreakoutScreener(tdx, ak_fetcher),
    'sector_divergence': SectorDivergenceScreener(tdx, ak_fetcher),
    'auction': AuctionScreener(tdx, ak_fetcher),
    'factor_momentum': FactorScreener(tdx, ak_fetcher, preset='momentum'),
    'factor_reversal': FactorScreener(tdx, ak_fetcher, preset='reversal'),
    'factor_quality': FactorScreener(tdx, ak_fetcher, preset='quality'),
    'factor_all': FactorScreener(tdx, ak_fetcher, preset='all'),
}


# ---- Chat API (Vibe-Trading integration) ----

@app.route('/api/chat', methods=['POST'])
def api_chat():
    body = request.get_json(silent=True) or {}
    message = body.get('message', '')
    if not message:
        return jsonify({'error': 'message is required'}), 400
    task_id = _create_task()
    def run():
        try:
            from ..agents.orchestrator import SwarmOrchestrator
            orch = SwarmOrchestrator()
            _emit_event(task_id, 'status', {'msg': f'开始分析...'})
            import re
            codes = re.findall(r'\b(\d{6})\b', message)
            if codes:
                for code in codes[:3]:
                    _emit_event(task_id, 'status', {'msg': f'正在分析 {code}...'})
                    plan = asyncio.run(orch.analyze_stock(code, '', message))
                    _emit_event(task_id, 'agent_result', plan.to_dict())
                    _emit_event(task_id, 'trading_plan', plan.to_dict())
            else:
                plan = asyncio.run(orch.analyze_stock('000001', '上证指数', message))
                _emit_event(task_id, 'trading_plan', plan.to_dict())
            _complete_task(task_id, {'done': True})
        except Exception as e:
            import traceback; traceback.print_exc()
            _fail_task(task_id, str(e))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return jsonify({'task_id': task_id, 'status': 'processing'})

@app.route('/api/chat/stream/<task_id>')
def api_chat_stream(task_id):
    return _sse_stream(task_id)

@app.route('/api/chat/history')
def api_chat_history():
    return jsonify({'history': []})

@app.route('/api/agent/analyze', methods=['POST'])
def api_agent_analyze():
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    strategy = body.get('strategy', 'leader')
    if not code:
        return jsonify({'error': 'code is required'}), 400
    task_id = _create_task()
    def run():
        try:
            from ..agents.orchestrator import SwarmOrchestrator
            orch = SwarmOrchestrator()
            name = ''
            if strategy in SCREENERS:
                name = SCREENERS[strategy]._get_name(code)
            _emit_event(task_id, 'status', {'msg': f'AI分析 {name}({code})...'})
            plan = asyncio.run(orch.analyze_stock(
                code, name, json.dumps({'strategy': strategy}, ensure_ascii=False)))
            _emit_event(task_id, 'trading_plan', plan.to_dict())
            _complete_task(task_id, plan.to_dict())
        except Exception as e:
            import traceback; traceback.print_exc()
            _fail_task(task_id, str(e))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return jsonify({'task_id': task_id, 'status': 'processing'})

# ---- Alpha API (Vibe-Trading integration) ----
@app.route('/api/alpha/list')
def api_alpha_list():
    zoo = request.args.get('zoo', '')
    from ..alpha.registry import get_registry
    r = get_registry()
    factors = r.list_by_zoo(zoo) if zoo else r.list_all()
    return jsonify({'total': len(factors), 'factors': r.summary()})

@app.route('/api/alpha/eval', methods=['POST'])
def api_alpha_eval():
    body = request.get_json(silent=True) or {}
    factor_id = body.get('factor_id', '')
    code = body.get('code', '600519')
    from ..alpha.registry import get_registry
    from ..alpha.evaluator import evaluate_factor
    r = get_registry()
    factor = r.get(factor_id)
    if factor is None:
        return jsonify({'error': f'Factor {factor_id} not found'}), 404
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith('8') or code.startswith('4'):
        market = 'bj'
    df = tdx.read_daily(code, market)
    if df.empty:
        return jsonify({'error': f'No data for {code}'}), 404
    report = evaluate_factor(factor, df)
    return jsonify(report.to_dict())

@app.route('/api/alpha/compare', methods=['POST'])
def api_alpha_compare():
    body = request.get_json(silent=True) or {}
    factor_ids = body.get('factor_ids', [])
    code = body.get('code', '600519')
    from ..alpha.compare import compare_factors
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith('8') or code.startswith('4'):
        market = 'bj'
    df = tdx.read_daily(code, market)
    if df.empty:
        return jsonify({'error': f'No data for {code}'}), 404
    results = compare_factors(factor_ids, df)
    return jsonify({'factors': results})


@app.route('/api/alpha/screen', methods=['POST'])
def api_alpha_screen():
    """全市场因子筛选 — 按因子横截面排名选股"""
    body = request.get_json(silent=True) or {}
    preset = body.get('preset', 'momentum')
    top_n = body.get('top_n', 30)
    max_stocks = body.get('max_stocks', 500)
    factor_ids = body.get('factor_ids', None)

    from ..alpha.batch import batch_calculate

    try:
        results = batch_calculate(
            tdx=tdx,
            factor_ids=factor_ids,
            preset=preset,
            top_n=top_n,
            max_stocks=max_stocks,
        )
        return jsonify({
            'preset': preset,
            'total': len(results),
            'results': results,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ---- Strategy API (Vibe-Trading integration) ----
@app.route('/api/strategy/templates')
def api_strategy_templates():
    from ..nl_strategy.templates import BUILTIN_TEMPLATES
    data = {k: {'name': v.name, 'description': v.description, 'conditions_count': len(v.conditions)} for k, v in BUILTIN_TEMPLATES.items()}
    return jsonify({'templates': data})

@app.route('/api/strategy/parse', methods=['POST'])
def api_strategy_parse():
    body = request.get_json(silent=True) or {}
    description = body.get('description', '')
    if not description:
        return jsonify({'error': 'description is required'}), 400
    from ..nl_strategy.parser import parse_strategy
    result = parse_strategy(description)
    if result['success']:
        return jsonify({'success': True, 'spec': result['spec'].to_dict()})
    return jsonify({'success': False, 'error': result.get('error', 'Parse failed')})

@app.route('/api/strategy/execute', methods=['POST'])
def api_strategy_execute():
    body = request.get_json(silent=True) or {}
    spec_dict = body.get('spec', {})
    template_id = body.get('template', '')
    from ..nl_strategy.spec import StrategySpec
    from ..nl_strategy.templates import BUILTIN_TEMPLATES
    from ..nl_strategy.executor import execute_strategy
    if template_id and template_id in BUILTIN_TEMPLATES:
        spec = BUILTIN_TEMPLATES[template_id]
    elif spec_dict:
        spec = StrategySpec.from_dict(spec_dict)
    else:
        return jsonify({'error': 'spec or template required'}), 400
    results = execute_strategy(spec)
    return jsonify({'strategy': spec.name, 'total': len(results), 'results': results})

@app.route('/api/strategy/backtest', methods=['POST'])
def api_strategy_backtest():
    body = request.get_json(silent=True) or {}
    spec_dict = body.get('spec', {})
    days = body.get('days', 60)
    from ..nl_strategy.spec import StrategySpec
    from ..nl_strategy.executor import execute_strategy
    spec = StrategySpec.from_dict(spec_dict)
    results = execute_strategy(spec)
    return jsonify({'strategy': spec.name, 'days': days, 'results_count': len(results), 'note': '完整回测需接入 backtest 引擎'})


def _enrich_top_3(data: list, strategy: str) -> list:
    """为 Top 3 标的追加技术面分析 + 操作建议"""
    enriched = []
    for item in data:
        analysis = analyze_pick(item['code'], tdx, strategy, item.get('detail', {}))
        item['analysis'] = analysis
        enriched.append(item)
    return enriched


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/screening')
def screening():
    return render_template('screening.html')


@app.route('/api/screen/<strategy>')
def api_screen(strategy):
    if strategy not in SCREENERS:
        return jsonify({'error': 'Unknown strategy'}), 404
    screener = SCREENERS[strategy]
    try:
        results = screener.screen()
        all_data = [{
            'code': r.code, 'name': r.name, 'score': r.score,
            'reasons': r.reasons, 'detail': r.detail
        } for r in results]
        top_3 = _enrich_top_3(all_data[:3], strategy)
        return jsonify({
            'strategy': strategy,
            'strategy_name': screener.name,
            'total': len(all_data),
            'top_3': top_3,
            'results': all_data,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500



@app.route('/api/screen/optimized')
def api_screen_optimized():
    """优化总筛选: 多因子竞价精选（竞价量价50% + 龙虎榜25% + 涨停基因15% + 量比10%）

    候选池: 竞价高开2%-6%标的 + T-1龙虎榜净买入标的
    每日竞价阶段精选Top3
    """
    from datetime import datetime, time, date, timedelta
    from collections import defaultdict

    # ---- 当日实时行情快照 ----
    spot_map = {}
    data_freshness = '收盘数据'
    try:
        spot_df = ak_fetcher.get_spot_df()
        if spot_df is not None and not spot_df.empty:
            now = datetime.now()
            is_trading = (now.weekday() < 5 and
                          time(9, 30) <= now.time() <= time(15, 0))
            data_freshness = '盘中实时' if is_trading else '当日收盘'
            for _, row in spot_df.iterrows():
                c = str(row.get('代码', '')).zfill(6)
                try:
                    pct = float(row.get('涨跌幅', 0))
                except (ValueError, TypeError):
                    pct = 0
                try:
                    price = float(row.get('最新价', 0))
                except (ValueError, TypeError):
                    price = 0
                try:
                    amt = float(row.get('成交额', 0))
                except (ValueError, TypeError):
                    amt = 0
                spot_map[c] = {
                    'price': round(price, 2),
                    'change_pct': round(pct, 1),
                    'amount_yi': round(amt / 1e8, 1) if amt > 0 else 0,
                }
    except Exception:
        pass

    # ---- Step 1: 竞价抢筹基础筛选 ----
    if 'auction' not in SCREENERS:
        return jsonify({'error': '竞价抢筹筛选器未就绪'}), 500
    try:
        auction_results = SCREENERS['auction'].screen()
    except Exception as e:
        return jsonify({'error': f'竞价筛选失败: {e}'}), 500

    # ---- Step 2: 龙虎榜数据（T-1日） ----
    lhb_codes = set()
    lhb_detail = {}
    yesterday = date.today() - timedelta(days=1)
    while yesterday.weekday() >= 5:
        yesterday -= timedelta(days=1)
    try:
        lhb_list = ak_fetcher.get_lhb(yesterday.strftime('%Y%m%d'))
        for l in lhb_list:
            if l.net_amount and l.net_amount > 0:
                lhb_codes.add(l.code)
                lhb_detail[l.code] = {
                    'net_amount': l.net_amount,
                    'buy_amount': l.buy_amount,
                    'sell_amount': l.sell_amount,
                    'reason': l.reason,
                }
    except Exception:
        pass

    # ---- Step 3: 多因子综合评分 ----
    optimized = []
    for r in auction_results:
        code = r.code
        mf_score = r.score * 0.5
        bonuses = []

        # 龙虎榜因子（25%）
        if code in lhb_codes:
            lhb = lhb_detail[code]
            net_amt = lhb['net_amount']
            if net_amt > 10000:
                mf_score += 25
                bonuses.append(f'龙虎榜净买{net_amt:.0f}万(强力)')
            elif net_amt > 5000:
                mf_score += 18
                bonuses.append(f'龙虎榜净买{net_amt:.0f}万')
            elif net_amt > 1000:
                mf_score += 10
                bonuses.append(f'龙虎榜净买{net_amt:.0f}万')
            else:
                mf_score += 5
                bonuses.append('龙虎榜净买入')

        # 涨停基因因子（15%）
        lu_count = r.detail.get('limit_up_count', 0) if isinstance(r.detail, dict) else 0
        if lu_count >= 5:
            mf_score += 15
            bonuses.append(f'涨停基因活跃({lu_count}次)')
        elif lu_count >= 2:
            mf_score += min(lu_count * 3, 12)
            bonuses.append(f'涨停基因({lu_count}次)')

        # 量比因子（10%）
        for reason in r.reasons:
            if '爆量' in reason or '巨量' in reason:
                mf_score += 10
                break
            elif '放量' in reason:
                mf_score += 6
                break

        # 量价配合加分
        has_vol_price = any('量价齐升' in rr for rr in r.reasons)
        if has_vol_price:
            mf_score += 5
            bonuses.append('量价齐升')

        optimized.append({
            'code': code, 'name': r.name,
            'score': min(round(mf_score), 100),
            'reasons': r.reasons + bonuses,
            'detail': r.detail,
        })

    optimized.sort(key=lambda x: x['score'], reverse=True)
    top_3 = _enrich_top_3(optimized[:3], 'optimized')

    # 注入龙虎榜详情和当日行情
    for item in top_3:
        if item['code'] in lhb_detail:
            item['lhb'] = lhb_detail[item['code']]
        spot = spot_map.get(item['code'])
        if spot:
            item['today_change'] = spot['change_pct']
            item['today_price'] = spot['price']

    return jsonify({
        'strategy': 'optimized',
        'strategy_name': '优化总筛选（多因子竞价精选）',
        'total': len(optimized),
        'top_3': top_3,
        'results': optimized,
        'note': f'数据: {data_freshness} | 多因子: 竞价50%+龙虎榜25%+涨停基因15%+量比10% | 龙虎榜: {len(lhb_codes)}只净买',
    })


@app.route('/api/screen/lhb_auction')
def api_screen_lhb_auction():
    """龙虎榜竞价: T-1龙虎榜净买入 ∩ T日竞价抢筹（机构+游资共振确认）"""
    from datetime import datetime, time, date, timedelta

    # ---- 当日行情 ----
    spot_map = {}
    data_freshness = '收盘数据'
    try:
        spot_df = ak_fetcher.get_spot_df()
        if spot_df is not None and not spot_df.empty:
            now = datetime.now()
            is_trading = (now.weekday() < 5 and
                          time(9, 30) <= now.time() <= time(15, 0))
            data_freshness = '盘中实时' if is_trading else '当日收盘'
            for _, row in spot_df.iterrows():
                c = str(row.get('代码', '')).zfill(6)
                try:
                    pct = float(row.get('涨跌幅', 0))
                except (ValueError, TypeError):
                    pct = 0
                try:
                    price = float(row.get('最新价', 0))
                except (ValueError, TypeError):
                    price = 0
                spot_map[c] = {'price': round(price, 2), 'change_pct': round(pct, 1)}
    except Exception:
        pass

    # ---- Step 1: T-1龙虎榜净买入 ----
    yesterday = date.today() - timedelta(days=1)
    while yesterday.weekday() >= 5:
        yesterday -= timedelta(days=1)
    lhb_codes = set()
    lhb_detail = {}
    try:
        lhb_list = ak_fetcher.get_lhb(yesterday.strftime('%Y%m%d'))
        for l in lhb_list:
            if l.net_amount and l.net_amount > 0:
                lhb_codes.add(l.code)
                lhb_detail[l.code] = {
                    'name': l.name,
                    'net_amount': l.net_amount,
                    'buy_amount': l.buy_amount,
                    'sell_amount': l.sell_amount,
                    'reason': l.reason,
                }
    except Exception:
        pass

    if not lhb_codes:
        return jsonify({
            'strategy': 'lhb_auction',
            'strategy_name': '龙虎榜竞价',
            'total': 0, 'top_3': [], 'results': [],
            'note': f'龙虎榜: T-1({yesterday})暂无净买入标的',
        })

    # ---- Step 2: T日竞价抢筹 ----
    if 'auction' not in SCREENERS:
        return jsonify({'error': '竞价抢筹筛选器未就绪'}), 500
    try:
        auction_results = SCREENERS['auction'].screen()
    except Exception as e:
        return jsonify({'error': f'竞价筛选失败: {e}'}), 500

    # ---- Step 3: 交叉筛选 ----
    auc_codes = {r.code: r for r in auction_results}
    overlap = lhb_codes & set(auc_codes.keys())

    results = []
    for code in overlap:
        auc_r = auc_codes[code]
        lhb = lhb_detail[code]
        bonus = 5 if lhb['net_amount'] > 10000 else (3 if lhb['net_amount'] > 5000 else 0)
        results.append({
            'code': code, 'name': auc_r.name,
            'score': min(auc_r.score + bonus, 100),
            'reasons': auc_r.reasons + [
                f'龙虎榜净买{lhb["net_amount"]:.0f}万',
                f'上榜: {lhb.get("reason", "机构游资介入")}',
            ],
            'detail': {**auc_r.detail,
                       'lhb_net_amount': lhb['net_amount'],
                       'lhb_buy': lhb['buy_amount'],
                       'lhb_sell': lhb['sell_amount']},
        })

    results.sort(key=lambda x: x['score'], reverse=True)
    top_3 = _enrich_top_3(results[:3], 'lhb_auction')

    for item in top_3:
        if item['code'] in lhb_detail:
            item['lhb'] = lhb_detail[item['code']]
        spot = spot_map.get(item['code'])
        if spot:
            item['today_change'] = spot['change_pct']
            item['today_price'] = spot['price']

    return jsonify({
        'strategy': 'lhb_auction',
        'strategy_name': '龙虎榜竞价（机构+游资共振）',
        'total': len(results),
        'top_3': top_3,
        'results': results,
        'note': f'数据: {data_freshness} | 龙虎榜T-1({yesterday}): {len(lhb_codes)}只净买 | 竞价确认: {len(results)}只共振',
    })


@app.route('/review')
def review():
    trade_date = request.args.get('date', None)  # 支持 ?date=20260612
    try:
        report = DailyReport(tdx, ak_fetcher).generate(trade_date)
        if request.args.get('llm') == '1':
            report['llm_summary'] = DailyReport(tdx, ak_fetcher).generate_llm_summary(trade_date)
    except Exception as e:
        import traceback
        traceback.print_exc()
        report = {'date': 'N/A', 'total_limit_ups': 0, 'error': str(e)}
    return render_template('review.html', report=report)


@app.route('/events')
def events_page():
    """事件驱动专题页"""
    from datetime import date
    from ..report.events import get_events_for_period, search_events

    trade_date = request.args.get('date', None)
    ref_date = date.today()
    if trade_date:
        try:
            ref_date = date.fromisoformat(trade_date)
        except ValueError:
            pass

    events_data = get_events_for_period(ref_date)
    return render_template('events.html', events=events_data, ref_date=ref_date.isoformat())


@app.route('/api/events/search')
def api_events_search():
    """搜索事件 + 实时新闻"""
    from datetime import date, datetime
    from ..report.events import search_events, match_event_stocks

    keyword = request.args.get('q', '').strip()
    source = request.args.get('source', 'events')  # events | news | all

    result = {
        'events': [],
        'news': [],
        'keyword': keyword,
        'searched_at': datetime.now().isoformat(),
    }

    if not keyword:
        return jsonify(result)

    # 搜索事件
    if source in ('events', 'all'):
        matched = search_events(keyword)
        result['events'] = matched

    # 搜索实时新闻（akshare）
    if source in ('news', 'all'):
        try:
            import akshare as ak
            # 使用akshare的财联社电报/新闻接口
            news_list = []
            try:
                # 财联社电报
                df = ak.stock_info_global_cls()
                if df is not None and not df.empty:
                    cols = list(df.columns)
                    title_col = next((c for c in cols if 'title' in c.lower() or '标题' in c), cols[0] if cols else None)
                    time_col = next((c for c in cols if 'time' in c.lower() or '时间' in c), None)
                    for _, row in df.head(50).iterrows():
                        title = str(row.get(title_col, '')) if title_col else ''
                        if keyword.lower() in title.lower():
                            news_list.append({
                                'title': title,
                                'time': str(row.get(time_col, '')) if time_col else '',
                                'source': '财联社',
                            })
            except Exception:
                pass

            # 备用：东方财富新闻
            if not news_list:
                try:
                    df2 = ak.stock_zh_ah_name()
                    # 此接口不一定有新闻，使用其他方式
                except Exception:
                    pass

            # 如果akshare新闻接口不可用，返回提示
            if not news_list:
                news_list = [{
                    'title': f'实时新闻接口暂不可用，请在财经网站搜索「{keyword}」获取最新资讯',
                    'time': datetime.now().strftime('%H:%M'),
                    'source': '提示',
                    'is_placeholder': True,
                }]

            result['news'] = news_list[:20]
        except Exception as e:
            import traceback
            traceback.print_exc()
            result['news'] = [{
                'title': f'新闻获取失败: {str(e)}',
                'time': '',
                'source': '错误',
                'is_placeholder': True,
            }]

    result['total'] = len(result['events']) + len(result['news'])
    return jsonify(result)


@app.route('/api/events/data')
def api_events_data():
    """获取事件数据JSON（用于动态加载/刷新）"""
    from datetime import date
    from ..report.events import get_events_for_period

    ref_date = date.today()
    trade_date = request.args.get('date', None)
    if trade_date:
        try:
            ref_date = date.fromisoformat(trade_date)
        except ValueError:
            pass

    return jsonify(get_events_for_period(ref_date))


@app.route('/stock/<code>')
def stock_detail(code):
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith('8') or code.startswith('4'):
        market = 'bj'
    try:
        df = tdx.read_daily(code, market)
        if df.empty or len(df) < 10:
            return render_template('stock_detail.html', code=code, error='数据不足')

        # ---- TDX 技术指标 ----
        from ..analysis.indicators import enrich_all
        df = enrich_all(df)
        latest = df.iloc[-1].to_dict()
        prev = df.iloc[-2].to_dict() if len(df) >= 2 else latest

        # ---- 当日实时行情 ----
        spot = {}
        data_freshness = 'TDX收盘'
        try:
            from datetime import datetime, time
            spot_df = ak_fetcher.get_spot_df()
            if spot_df is not None and not spot_df.empty:
                match = spot_df[spot_df['代码'] == code]
                if not match.empty:
                    row = match.iloc[0]
                    spot_change = float(row.get('涨跌幅', 0))
                    spot_price = float(row.get('最新价', 0))
                    spot_open = float(row.get('今开', 0)) if '今开' in row.index else 0
                    spot_high = float(row.get('最高', 0))
                    spot_low = float(row.get('最低', 0))
                    spot_vol = int(row.get('成交量', 0))
                    spot_amt = float(row.get('成交额', 0))
                    now = datetime.now()
                    is_trading = (now.weekday() < 5 and
                                  time(9, 30) <= now.time() <= time(15, 0))
                    data_freshness = '盘中实时' if is_trading else '当日收盘'
                    spot = {
                        'price': round(spot_price, 2),
                        'change_pct': round(spot_change, 1),
                        'open': round(spot_open, 2),
                        'high': round(spot_high, 2),
                        'low': round(spot_low, 2),
                        'volume': spot_vol,
                        'amount_yi': round(spot_amt / 1e8, 1) if spot_amt > 0 else 0,
                    }
        except Exception:
            pass

        # 名称
        name = ''
        for s in SCREENERS.values():
            n = s._get_name(code)
            if n:
                name = n
                break

        # ---- 均线状态（基于TDX，支撑/压力仍然有效） ----
        ma_status = []
        tdx_close = float(latest['close'])
        for p in [5, 10, 20, 60, 89, 250]:
            ma_val = latest.get(f'ma{p}', 0)
            if ma_val and ma_val > 0:
                # 用当日实时价判断站上/跌破（如果有）
                ref_price = spot.get('price', tdx_close) if spot else tdx_close
                above = ref_price > ma_val
                pct = (ref_price - ma_val) / ma_val * 100
                ma_status.append({
                    'period': p, 'above': above,
                    'ma_price': round(float(ma_val), 2),
                    'pct_from_ma': round(float(pct), 1),
                })

        # 筹码分析
        chip = {}
        try:
            from ..analysis.chip import calc_chip_distribution, detect_chip_patterns
            chip = calc_chip_distribution(df)
            chip['patterns'] = detect_chip_patterns(df)
        except Exception:
            pass

        # 最近20条日线（TDX历史 + 当日spot数据注入首行）
        import numpy as np
        recent_bars = []
        for i in range(max(0, len(df) - 20), len(df)):
            row = df.iloc[i]
            chg = row.get('change_pct', 0)
            if chg is None or (isinstance(chg, float) and np.isnan(chg)):
                chg = 0
            vr = row.get('volume_ratio', 1)
            if vr is None or (isinstance(vr, float) and np.isnan(vr)):
                vr = 1
            recent_bars.append({
                'date': str(row.get('trade_date', '')),
                'open': round(float(row['open']), 2),
                'high': round(float(row['high']), 2),
                'low': round(float(row['low']), 2),
                'close': round(float(row['close']), 2),
                'volume': int(row['volume']),
                'change_pct': round(float(chg), 1),
                'volume_ratio': round(float(vr), 1),
            })
        # 如果当日有spot数据且不在TDX中，追加为当日行
        if spot and recent_bars:
            last_bar_date = recent_bars[-1]['date']
            today_str = datetime.now().strftime('%Y-%m-%d')
            if last_bar_date != today_str:
                recent_bars.append({
                    'date': today_str,
                    'open': spot.get('open', 0),
                    'high': spot.get('high', 0),
                    'low': spot.get('low', 0),
                    'close': spot.get('price', 0),
                    'volume': spot.get('volume', 0),
                    'change_pct': spot.get('change_pct', 0),
                    'volume_ratio': 0,
                })

        # ---- 技术面摘要（TDX + spot混合） ----
        close = spot.get('price', float(latest['close']))
        change_pct = spot.get('change_pct', float(latest.get('change_pct', 0)))
        vol_ratio = float(latest.get('volume_ratio', 1))
        dif = float(latest.get('macd_dif', 0))
        dea = float(latest.get('macd_dea', 0))
        bar_val = float(latest.get('macd_bar', 0))

        tech_summary = {
            'change_pct': round(change_pct, 1),
            'vol_ratio': round(vol_ratio, 1),
            'volume': spot.get('volume', int(latest['volume'])),
            'amount_yi': spot.get('amount_yi', round(float(latest.get('amount', 0)) / 1e8, 1)),
            'amplitude': round(float(latest.get('amplitude', 0)), 1),
            'ma5': round(float(latest.get('ma5', 0)), 2),
            'ma10': round(float(latest.get('ma10', 0)), 2),
            'ma20': round(float(latest.get('ma20', 0)), 2),
            'ma60': round(float(latest.get('ma60', 0)), 2),
            'ma89': round(float(latest.get('ma89', 0)), 2),
            'ma250': round(float(latest.get('ma250', 0)), 2),
            'macd_dif': round(dif, 3),
            'macd_dea': round(dea, 3),
            'macd_bar': round(bar_val, 3),
            'macd_golden': dif > dea,
            'macd_above_zero': dif > 0,
            # 当日实时
            'today_open': spot.get('open', float(latest['open'])),
            'today_high': spot.get('high', float(latest['high'])),
            'today_low': spot.get('low', float(latest['low'])),
            'tdx_close': round(float(latest['close']), 2),
        }

        # ---- 入场/止损/止盈（基于完整技术分析） ----
        suggestion = {}
        try:
            from ..analysis.pick_analysis import analyze_pick
            pick = analyze_pick(code, tdx, 'leader', {})
            suggestion = pick.get('suggestion', {})
        except Exception:
            pass

        return render_template('stock_detail.html',
                             code=code, name=name,
                             latest=latest, tech=tech_summary,
                             ma_status=ma_status, chip=chip,
                             recent_bars=recent_bars,
                             data_freshness=data_freshness,
                             spot=spot,
                             suggestion=suggestion)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return render_template('stock_detail.html', code=code, error=str(e))


@app.route('/chat')
def chat():
    return render_template('chat.html')


@app.route('/chart')
def chart_page():
    """看盘页面 — TradingView 风格K线图"""
    code = request.args.get('code', '000001')
    return render_template('chart.html', default_code=code)


@app.route('/api/chart/kline')
def api_chart_kline():
    """K线数据接口 — 多周期支持

    Query params:
        code: 股票代码 (6位), 必填
        period: 周期 daily/60min/30min/15min/5min, 默认 daily
    """
    code = request.args.get('code', '')
    period = request.args.get('period', 'daily')

    if not code or len(code) != 6:
        return jsonify({'error': 'code is required (6 digits)'}), 400

    # 确定市场
    if code.startswith('6'):
        market = 'sh'
    elif code.startswith('0') or code.startswith('3'):
        market = 'sz'
    elif code.startswith('8') or code.startswith('4'):
        market = 'bj'
    else:
        return jsonify({'error': f'Unknown market for code: {code}'}), 400

    try:
        if period == 'daily':
            # 日线 — 通达信本地数据
            df = tdx.read_daily(code, market)
            if df.empty:
                return jsonify({'error': f'No daily data for {code}', 'code': code}), 404

            # 转成 lightweight-charts 需要的格式
            bars = []
            for _, row in df.iterrows():
                d = row['trade_date']
                bars.append({
                    'time': d.isoformat() if hasattr(d, 'isoformat') else str(d),
                    'open': round(float(row['open']), 2),
                    'high': round(float(row['high']), 2),
                    'low': round(float(row['low']), 2),
                    'close': round(float(row['close']), 2),
                    'volume': int(row['volume']),
                })
        else:
            # 分钟线 — akshare 在线获取
            period_map = {'60min': '60', '30min': '30', '15min': '15', '5min': '5'}
            ak_period = period_map.get(period, '60')

            df = ak_fetcher.get_min_kline(code, period=ak_period)
            if df.empty:
                return jsonify({
                    'error': f'No minute data for {code} period={period}',
                    'code': code,
                    'fallback': True,
                }), 404

            bars = []
            for _, row in df.iterrows():
                t = row['time']
                bars.append({
                    'time': t.isoformat() if hasattr(t, 'isoformat') else str(t),
                    'open': round(float(row['open']), 2),
                    'high': round(float(row['high']), 2),
                    'low': round(float(row['low']), 2),
                    'close': round(float(row['close']), 2),
                    'volume': int(row['volume']),
                })

        # 获取股票名称（从行情快照查找）
        name = ''
        try:
            spot_df = ak_fetcher.get_spot_df()
            if spot_df is not None and not spot_df.empty:
                row = spot_df[spot_df['代码'] == code]
                if not row.empty:
                    name = str(row.iloc[0].get('名称', ''))
        except Exception:
            pass

        return jsonify({
            'code': code,
            'name': name,
            'period': period,
            'market': market,
            'total': len(bars),
            'bars': bars,
        })

    except FileNotFoundError as e:
        return jsonify({'error': str(e), 'code': code}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'code': code}), 500


def run(host='127.0.0.1', port=5000, debug=True):
    app.run(host=host, port=port, debug=debug)
