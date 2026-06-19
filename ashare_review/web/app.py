"""Flask Web 应用"""
from flask import Flask, render_template, jsonify, request
from ..screening.one_two import OneTwoScreener
from ..screening.institution import InstitutionScreener
from ..screening.leader import LeaderScreener
from ..screening.breakout import BreakoutScreener
from ..screening.sector_divergence import SectorDivergenceScreener
from ..screening.auction import AuctionScreener
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
                loop = asyncio.new_event_loop()
                for code in codes[:3]:
                    _emit_event(task_id, 'status', {'msg': f'正在分析 {code}...'})
                    plan = loop.run_until_complete(orch.analyze_stock(code, '', message))
                    _emit_event(task_id, 'agent_result', plan.to_dict())
                    _emit_event(task_id, 'trading_plan', plan.to_dict())
                loop.close()
            else:
                loop = asyncio.new_event_loop()
                plan = loop.run_until_complete(orch.analyze_stock('000001', '上证指数', message))
                loop.close()
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
            loop = asyncio.new_event_loop()
            plan = loop.run_until_complete(orch.analyze_stock(code, name, json.dumps({'strategy': strategy}, ensure_ascii=False)))
            loop.close()
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



@app.route('/api/screen/summary')
def api_screen_summary():
    """总筛选: 对龙头/突破/板块分歧/机构票做汇总，找出重叠标的

    注入当日实时行情快照（spot_df），确保盘中也能看到当天可介入机会。
    """
    strategies = ['leader', 'breakout', 'sector_divergence', 'institution']

    # ---- 当日实时行情快照 ----
    spot_map = {}
    data_freshness = '收盘数据'
    try:
        spot_df = ak_fetcher.get_spot_df()
        if spot_df is not None and not spot_df.empty:
            from datetime import datetime, time
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

    all_by_code = {}
    strategy_names = {}

    for name in strategies:
        if name not in SCREENERS:
            continue
        screener = SCREENERS[name]
        try:
            results = screener.screen()
            strategy_names[name] = screener.name
            for r in results:
                if r.code not in all_by_code:
                    all_by_code[r.code] = {
                        'code': r.code, 'name': r.name,
                        'strategies': [], 'scores': {}, 'reasons': {},
                    }
                all_by_code[r.code]['strategies'].append(name)
                all_by_code[r.code]['scores'][name] = r.score
                all_by_code[r.code]['reasons'][name] = r.reasons[:3]
        except Exception as e:
            import traceback
            traceback.print_exc()

    # ---- 当日行情注入 + 汇总 ----
    summary = []
    for code, info in all_by_code.items():
        match_count = len(info['strategies'])
        if match_count < 2:
            continue
        avg_score = sum(info['scores'].values()) / match_count
        strategy_labels = [strategy_names.get(s, s) for s in info['strategies']]
        match_detail = []
        for s in info['strategies']:
            match_detail.append({
                'strategy': strategy_names.get(s, s),
                'strategy_key': s,
                'score': round(info['scores'].get(s, 0)),
                'reasons': info['reasons'].get(s, []),
            })

        spot = spot_map.get(code, {})
        summary.append({
            'code': info['code'],
            'name': info['name'],
            'match_count': match_count,
            'avg_score': round(avg_score),
            'strategies': strategy_labels,
            'match_detail': match_detail,
            'today_price': spot.get('price', 0),
            'today_change': spot.get('change_pct', None),
            'today_amount': spot.get('amount_yi', 0),
        })

    summary.sort(key=lambda x: (x['match_count'], x['avg_score']), reverse=True)

    # Top 3 enriched cards (use today_change from spot)
    top_3_enriched = []
    for s in summary[:3]:
        detail = {
            'match_count': s['match_count'],
            'strategies': s['strategies'],
            'match_detail': s['match_detail'],
            'today_change': s['today_change'],
            'today_price': s['today_price'],
            'today_amount': s['today_amount'],
        }
        analysis = analyze_pick(s['code'], tdx, 'leader', detail)
        top_3_enriched.append({
            'code': s['code'], 'name': s['name'],
            'score': s['avg_score'],
            'reasons': [f'匹配{s["match_count"]}个战法: {", ".join(s["strategies"])}'],
            'detail': detail,
            'analysis': analysis,
        })

    return jsonify({
        'strategy': 'summary',
        'strategy_name': '总筛选(重叠标的)',
        'total': len(summary),
        'top_3': top_3_enriched,
        'results': summary,
        'strategy_names': {k: v for k, v in strategy_names.items()},
        'data_freshness': data_freshness,
        'note': f'数据: {data_freshness} | 扫描{len(strategies)}个策略，发现{len(summary)}个重叠标的（≥2战法）',
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

        return render_template('stock_detail.html',
                             code=code, name=name,
                             latest=latest, tech=tech_summary,
                             ma_status=ma_status, chip=chip,
                             recent_bars=recent_bars,
                             data_freshness=data_freshness,
                             spot=spot)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return render_template('stock_detail.html', code=code, error=str(e))


@app.route('/chat')
def chat():
    return render_template('chat.html')

@app.route('/alpha')
def alpha_page():
    return render_template('alpha.html')

@app.route('/strategies')
def strategies_page():
    return render_template('strategies.html')


def run(host='127.0.0.1', port=5000, debug=True):
    app.run(host=host, port=port, debug=debug)
