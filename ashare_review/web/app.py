"""Flask Web 应用 - 精简版（仅保留竞价交易流程）"""
from flask import Flask, render_template, jsonify, request
from ..screening.one_two import OneTwoScreener
from ..screening.institution import InstitutionScreener
from ..screening.leader import LeaderScreener
from ..screening.breakout import BreakoutScreener
from ..screening.sector_divergence import SectorDivergenceScreener
from ..screening.auction import AuctionScreener
from ..screening.five_indicator import (
    FiveIndicatorScreener, StartBreakoutScreener, StartBreakoutScreenerV2, StartBreakoutScreenerV3, RelayScreener,
    NPatternScreener, IceBottomScreener,
)
from ..alpha.screener import FactorScreener
from ..report.daily import DailyReport
from ..report.weekly import WeeklyReport
from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher
from ..data.f10_fetcher import F10Fetcher
from ..analysis.pick_analysis import analyze_pick
from ..analysis.stock_f10_analysis import analyze_stock
from ..utils.cache import (
    CACHE_DIR,
    cache_get, cache_set, cache_clear, cache_clear_all,
    cache_get_persistent, cache_set_persistent, cache_clear_persistent, cache_meta_persistent,
    cache_status as get_cache_status,
)
from ..analysis.strategy_regime import live_diagnosis as ld
import json
import sqlite3
import os
from datetime import datetime

# 复盘文章生成器（本地 Ollama）— 项目根目录下的独立脚本
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from generate_review_article import generate_article, _write_output as _write_article_output

app = Flask(__name__)


@app.before_request
def _reject_cross_site_requests():
    """CSRF 防护：非安全方法（POST/PUT/DELETE）必须来自本机。

    浏览器跨站请求会携带 Origin/Referer 头，来源不是本机时直接拒绝；
    命令行/脚本客户端（无 Origin/Referer）不受影响。
    """
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None
    origin = request.headers.get('Origin') or request.headers.get('Referer') or ''
    if origin:
        from urllib.parse import urlparse
        try:
            host = urlparse(origin).netloc.split(':')[0]
        except Exception:
            host = ''
        # 同源判定：Origin/Referer 的 host 必须与请求目标一致（含本机回环）
        if host and host != request.host.split(':')[0] and host not in ('127.0.0.1', 'localhost'):
            return jsonify({'error': '跨站请求被拒绝（CSRF 防护）'}), 403
    return None


tdx = TdxReader()
ak_fetcher = AkshareFetcher()
f10_fetcher = F10Fetcher()

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
    'start_breakout': StartBreakoutScreener(tdx, ak_fetcher),
    'start_breakout_v2': StartBreakoutScreenerV2(tdx, ak_fetcher),
    'start_breakout_v3': StartBreakoutScreenerV3(tdx, ak_fetcher),
    'relay': RelayScreener(tdx, ak_fetcher),
    'n_pattern': NPatternScreener(tdx, ak_fetcher),
    'ice_bottom': IceBottomScreener(tdx, ak_fetcher),
    'five_indicator': FiveIndicatorScreener(tdx, ak_fetcher),
}


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
    from datetime import datetime as _dt
    weekday_cn = '一二三四五六日'
    today = _dt.now().strftime('%Y-%m-%d') + ' 周' + weekday_cn[_dt.now().weekday()]
    return render_template('index.html', today=today)


@app.route('/screening')
def screening():
    return render_template('screening.html')


@app.route('/breakout')
def breakout():
    return render_template('breakout.html')


@app.route('/breakout_v2')
def breakout_v2():
    return render_template('breakout_v2.html')


@app.route('/api/screen/<strategy>')
def api_screen(strategy):
    if strategy not in SCREENERS:
        return jsonify({'error': 'Unknown strategy'}), 404

    refresh = request.args.get('refresh', '0') == '1'
    cache_key = f'screen_{strategy}'

    if not refresh:
        cached = cache_get(cache_key)
        if cached is not None:
            return jsonify(cached)

    screener = SCREENERS[strategy]
    try:
        results = screener.screen()
        all_data = [{
            'code': r.code, 'name': r.name, 'score': r.score,
            'reasons': r.reasons, 'detail': r.detail
        } for r in results]
        # 代码段跟随（逻辑哥接力战法）：统计今日晋级主攻代码段，供前端展示
        try:
            segment_stats = screener.segment_stats()
        except Exception:
            segment_stats = {'dominant': None, 'label': ''}
        top_3 = _enrich_top_3(all_data[:3], strategy)
        result = {
            'strategy': strategy,
            'strategy_name': screener.name,
            'total': len(all_data),
            'top_3': top_3,
            'results': all_data,
            'segment_stats': segment_stats,
            '_cached': False,
        }
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ── 基金挑选板块（养基体系） ──

@app.route('/fund_screening')
def fund_screening():
    """基金挑选板块：按板块筛出符合养基体系五条件的主动基金（每板块 Top5）"""
    return render_template('fund_screening.html')


@app.route('/api/fund_screen')
def api_fund_screen():
    """基金筛选：天天基金排行（业绩）+ 雪球单只规模 → 五条件打分 → 每板块 Top5。

    ?refresh=1 强制重筛（重拉排行 + 补拉规模）。
    """
    refresh = request.args.get('refresh', '0') == '1'
    cache_key = 'fund_screen'
    if not refresh:
        cached = cache_get(cache_key)
        if cached is not None:
            return jsonify(cached)

    from ..fund_screener import FundScreener
    try:
        result = FundScreener().screen()
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/fund_search')
def api_fund_search():
    """基金关键词搜索：输入任意关键词匹配主动基金名，按养基体系五条件打分返回 Top5。"""
    kw = request.args.get('q', '').strip()
    if not kw:
        return jsonify({'keyword': '', 'matched': 0, 'funds': [], 'note': '请输入搜索关键词'})

    from ..fund_screener import FundScreener
    try:
        return jsonify(FundScreener().search(kw))
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ── 启动突破 API（zigzag找顶线 + MAVOL180） ──

@app.route('/api/breakout/screen')
def api_breakout_screen():
    """启动突破筛选：找顶线下方候选 + 今日突破买入信号。

    选股：沪深主板 · 年涨停>10 · 非ST · 距zigzag找顶线≤10%
    买入信号：收盘突破找顶线 + 成交量>MAVOL180
    """
    refresh = request.args.get('refresh', '0') == '1'
    cache_key = 'breakout_screen'

    if not refresh:
        cached = cache_get(cache_key)
        if cached is not None:
            return jsonify(cached)

    from ..screening.five_indicator import StartBreakoutScreenerV2
    from ..analysis.indicators import calc_ma, calc_zigzag_find_top_line
    import numpy as np

    screener = StartBreakoutScreenerV2(tdx, ak_fetcher)
    try:
        # ── 第一步: 运行V2筛选器，获取距找顶线≤10%的候选 ──
        screen_results = screener.screen()
        candidates = []
        for r in screen_results:
            candidates.append({
                'code': r.code,
                'name': r.name,
                'score': r.score,
                'reasons': r.reasons,
                'limit_count': r.detail.get('limit_count', 0),
                'dist_pct': r.detail.get('dist_top_line', 0),
                'top_line': r.detail.get('top_line', 0),
                'sector': r.detail.get('sector', ''),
            })

        # ── 第二步: 对候选股检查今日是否触发买入信号 ──
        # 买入条件（与 vol180_breakout_backtest.py 回测一致）:
        #   收盘价 > 找顶线 AND 成交量 > MAVOL180
        #   硬性: 前一日收盘在找顶线下方 且 距顶线 0~10%（真突破）
        #   硬性: 非一字板（开盘 < 收盘×1.095）
        buy_signals = []
        for r in screen_results:
            code = r.code
            df = None
            market = 'sh' if code.startswith('6') else 'sz'
            if code.startswith(('8', '4')):
                market = 'bj'
            try:
                df = tdx.read_daily(code, market)
                if df is None or df.empty or len(df) < 180:
                    continue
                df = calc_ma(df, [5, 10])
                df = calc_zigzag_find_top_line(df)
                df['mavol180'] = df['volume'].rolling(180).mean() * 1.2

                idx = len(df) - 1
                close = float(df['close'].iloc[idx])
                open_p = float(df['open'].iloc[idx])
                vol = float(df['volume'].iloc[idx])
                top_line = float(df['find_top_line'].iloc[idx])
                mavol180 = float(df['mavol180'].iloc[idx])
                ma5 = float(df['ma5'].iloc[idx]) if 'ma5' in df.columns else 0
                ma10 = float(df['ma10'].iloc[idx]) if 'ma10' in df.columns else 0

                if np.isnan(top_line) or top_line <= 0:
                    continue
                if np.isnan(mavol180) or mavol180 <= 0:
                    continue

                # ── 条件1+2: 收盘突破找顶线 AND 量 > MAVOL180 ──
                if not (close > top_line and vol > mavol180):
                    continue

                # ── 硬性: 非一字板 ──
                if open_p >= close * 1.095:
                    continue

                # ── 硬性: 前一日收盘在找顶线下方 且 距顶线 0~10%（真突破） ──
                if idx <= 0:
                    continue
                prev_close = float(df['close'].iloc[idx - 1])
                prev_top = float(df['find_top_line'].iloc[idx - 1])
                if np.isnan(prev_top) or prev_top <= 0 or prev_close > prev_top:
                    continue
                prev_dist = (prev_top - prev_close) / prev_top * 100
                if not (0 < prev_dist <= 10):
                    continue

                vol_ratio = vol / mavol180
                break_pct = round((close - top_line) / top_line * 100, 1)
                chg = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0

                # ── 评分（与回测 _check_signal 一致，基础分20，满分约110+） ──
                score = 20.0
                reasons_parts = []
                if prev_dist <= 3:
                    score += 25  # 紧贴压力位，蓄势充分
                    reasons_parts.append(f'紧贴找顶线{prev_dist:.1f}%→突破{break_pct:+.1f}%')
                elif prev_dist <= 5:
                    score += 20
                    reasons_parts.append(f'距找顶线{prev_dist:.1f}%→突破{break_pct:+.1f}%')
                else:
                    score += 15
                    reasons_parts.append(f'距找顶线{prev_dist:.1f}%→突破{break_pct:+.1f}%')

                if vol_ratio >= 3.0:
                    score += 20
                    reasons_parts.append(f'爆量{vol_ratio:.1f}倍MAVOL180')
                elif vol_ratio >= 2.0:
                    score += 15
                    reasons_parts.append(f'显著放量{vol_ratio:.1f}倍MAVOL180')
                elif vol_ratio >= 1.5:
                    score += 10
                    reasons_parts.append(f'放量{vol_ratio:.1f}倍MAVOL180')
                else:
                    score += 5
                    reasons_parts.append(f'突破MAVOL180({vol_ratio:.1f}倍)')

                if ma5 > 0 and ma10 > 0:
                    if ma5 > ma10:
                        score += 8
                        reasons_parts.append('MA5>MA10多头')
                    if close > ma5:
                        score += 5
                        reasons_parts.append('站上MA5')

                if chg >= 9.5:
                    score += 12
                    reasons_parts.append(f'涨停突破{chg:.1f}%')
                elif chg >= 7:
                    score += 6
                    reasons_parts.append(f'大阳突破{chg:.1f}%')
                else:
                    score += 2
                    reasons_parts.append(f'涨幅{chg:.1f}%')

                buy_signals.append({
                    'code': code,
                    'name': r.name,
                    'score': round(score),
                    'close': round(close, 2),
                    'top_line': round(top_line, 2),
                    'break_pct': break_pct,
                    'vol_ratio': round(vol_ratio, 1),
                    'mavol180': round(mavol180, 0),
                    'prev_dist_pct': round(prev_dist, 1),
                    'change_pct': round(chg, 1),
                    'reasons': '; '.join(reasons_parts),
                    'sector': r.detail.get('sector', ''),
                })
            except Exception:
                continue

        buy_signals.sort(key=lambda x: x['score'], reverse=True)

        result = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'candidates': candidates,
            'buy_signals': buy_signals[:10],
            'total_candidates': len(candidates),
            'total_buy': len(buy_signals),
            '_cached': False,
        }
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ── 启动突破 V2 模拟面板 API ──

@app.route('/api/breakout_v2/simulation')
def api_breakout_v2_simulation():
    """刷新每日状态: 基于现有观察池，刷新价格/信号/持仓（不重建候选池）。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    cache_key = 'breakout_v2_sim'
    cached = cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    try:
        sp = Vol180SimPortfolio()
        today = datetime.now().strftime('%Y-%m-%d')
        sp.refresh_daily_status(trade_date=today, mode='v2')
        result = sp.get_summary()
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/breakout_v2/simulation/scan', methods=['POST'])
def api_breakout_v2_simulation_scan():
    """运行扫描: 强制重建候选池 + 全量筛选观察池 + 检测信号（重量级操作）。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    trade_date = body.get('date', None)
    try:
        cache_clear('breakout_v2_sim')  # 清空缓存
        sp = Vol180SimPortfolio()
        result = sp.run_daily(trade_date=trade_date, mode='v2', force_rebuild_pool=True)
        sp._state['last_update_v2'] = result.get('date', '')
        sp._save()
        summary = sp.get_summary()
        cache_set('breakout_v2_sim', summary)
        return jsonify({'success': True, 'stats': result, 'summary': summary})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/breakout_v2/simulation/record_buy', methods=['POST'])
def api_breakout_v2_simulation_record_buy():
    """记录模拟买入 V2。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    price = body.get('price', 0)
    if not code:
        return jsonify({'error': 'code required'}), 400
    sp = Vol180SimPortfolio()
    ok = sp.record_buy(code, actual_price=float(price) if price else None)
    return jsonify({'success': ok, 'summary': sp.get_summary()})


@app.route('/api/breakout_v2/simulation/record_sell', methods=['POST'])
def api_breakout_v2_simulation_record_sell():
    """记录模拟卖出 V2。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    price = body.get('price', 0)
    if not code or not price:
        return jsonify({'error': 'code and price required'}), 400
    sp = Vol180SimPortfolio()
    ok = sp.record_sell(code, float(price))
    return jsonify({'success': ok, 'summary': sp.get_summary()})


@app.route('/api/breakout_v2/simulation/delete', methods=['POST'])
def api_breakout_v2_simulation_delete():
    """删除持仓/就绪记录 V2。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    if not code:
        return jsonify({'error': 'code required'}), 400
    sp = Vol180SimPortfolio()
    ok = sp.delete_holding(code)
    return jsonify({'success': ok, 'summary': sp.get_summary()})


# ── 启动突破 V3 模拟面板 API ──

@app.route('/breakout_v3')
def breakout_v3():
    return render_template('breakout_v3.html')


@app.route('/v4_monitor')
def v4_monitor():
    """V4 策略实盘监控手册"""
    return render_template('v4_monitor.html')


@app.route('/api/v4/baseline')
def api_v4_baseline():
    """返回 V4 回测基线数据（含牛熊市分割统计）。

    优先读取缓存文件 v4_baseline_cache.json，
    如不存在则返回默认值（与 v4_monitor.html 中的硬编码一致）。
    """
    import json as _json
    baseline_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'data', 'v4_baseline_cache.json'
    )
    if os.path.exists(baseline_path):
        try:
            with open(baseline_path, 'r', encoding='utf-8') as f:
                return jsonify(_json.load(f))
        except Exception:
            pass

    # 回退默认值
    defaults = {
        'meta': {
            'generated_at': 'unknown',
            'backtest_period': '2022-01 ~ 2026-07',
            'note': '缓存文件不存在，返回默认估计值。请运行 generate_v4_baseline.py 生成准确数据。',
        },
        'full_cycle': {
            'total_trades': 997,
            'win_rate': 50.6,
            'avg_net_return': 1.82,
            'avg_win': 9.22,
            'avg_loss': -5.74,
            'profit_factor': 1.64,
            'cumulative_return': 179.0,
            'annualized_return': 26.3,
            'max_drawdown': 17.53,
            'sharpe_ratio': 1.52,
            'sh_above_ma20_pct': 50,
            'avg_hold_days': 4.8,
            'avg_positions': 7.2,
        },
        'bear_2022_2024': {
            'trades': 551,
            'win_rate': 48.6,
            'avg_net_return': 0.79,
            'avg_win': None,
            'avg_loss': None,
            'profit_factor': None,
            'cumulative_return': 47.5,
            'annualized_return': None,
            'max_drawdown': None,
            'sharpe_ratio': 0.84,
            'avg_hold_days': None,
            'avg_positions': None,
            'sh_above_ma20_pct': 44,
        },
        'bull_2025_2026': {
            'trades': 446,
            'win_rate': 52.9,
            'avg_net_return': 3.10,
            'avg_win': None,
            'avg_loss': None,
            'profit_factor': None,
            'cumulative_return': 89.2,
            'annualized_return': None,
            'max_drawdown': None,
            'sharpe_ratio': 3.05,
            'avg_hold_days': None,
            'avg_positions': None,
            'sh_above_ma20_pct': 63,
        },
    }
    return jsonify(defaults)


@app.route('/api/breakout_v3/predict')
def api_breakout_v3_predict():
    """明日突破预测: 基于 watch 蓄势池（压力位下方）预测次日放量突破。

    特征评分（历史校准: 基准次日突破率15.3% → 贴压力位≤3%组合 34~38%）:
      距压力位≤3% / 试盘摸高 / 多头排列 / 股性 / 量能。
    先验证昨日预测（查 TDX 今日收盘是否站上当日压力位），再生成今日预测并落台账。
    """
    from ..tools.breakout_predict import BreakoutPredictor
    try:
        pred = BreakoutPredictor()
        verified = pred.verify_pending()
        top = pred.predict(top_n=10)
        stats = pred.stats()
        return jsonify({
            'date': datetime.now().strftime('%Y-%m-%d'),
            'verified_today': verified,
            'predictions': top,
            'stats': stats,
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/breakout_v3/simulation')
def api_breakout_v3_simulation():
    """刷新每日状态 V3: 竞价确认 + N字反包 + 移动止盈。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    cache_key = 'breakout_v3_sim'
    cached = cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    try:
        sp = Vol180SimPortfolio()
        today = datetime.now().strftime('%Y-%m-%d')
        sp.refresh_daily_status(trade_date=today, mode='v3')
        result = sp.get_summary()
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/breakout_v3/simulation/scan', methods=['POST'])
def api_breakout_v3_simulation_scan():
    """运行扫描 V3: 强制重建候选池 + 全量筛选 + 板块共振评分。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    trade_date = body.get('date', None)
    try:
        cache_clear('breakout_v3_sim')  # 清空缓存
        sp = Vol180SimPortfolio()
        result = sp.run_daily(trade_date=trade_date, mode='v3', force_rebuild_pool=True)
        sp._state['last_update_v2'] = result.get('date', '')
        sp._save()
        summary = sp.get_summary()
        cache_set('breakout_v3_sim', summary)
        return jsonify({'success': True, 'stats': result, 'summary': summary})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/breakout_v3/simulation/record_buy', methods=['POST'])
def api_breakout_v3_simulation_record_buy():
    """记录模拟买入 V3。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    price = body.get('price', 0)
    if not code:
        return jsonify({'error': 'code required'}), 400
    sp = Vol180SimPortfolio()
    ok = sp.record_buy(code, actual_price=float(price) if price else None)
    cache_clear('breakout_v3_sim')
    return jsonify({'success': ok, 'summary': sp.get_summary()})


@app.route('/api/breakout_v3/simulation/record_sell', methods=['POST'])
def api_breakout_v3_simulation_record_sell():
    """记录模拟卖出 V3。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    price = body.get('price', 0)
    if not code or not price:
        return jsonify({'error': 'code and price required'}), 400
    sp = Vol180SimPortfolio()
    ok = sp.record_sell(code, float(price))
    cache_clear('breakout_v3_sim')
    return jsonify({'success': ok, 'summary': sp.get_summary()})


@app.route('/api/breakout_v3/simulation/finished')
def api_breakout_v3_finished():
    """获取所有已完成交易记录（可编辑）。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    try:
        sp = Vol180SimPortfolio()
        finished = sp.get_all_finished()
        # 计算统计
        total = len(finished)
        wins = sum(1 for f in finished if f.get('is_win'))
        net_rets = [f.get('net_ret', 0) for f in finished]
        total_ret = round(sum(net_rets), 2)
        avg_ret = round(total_ret / max(total, 1), 2)
        return jsonify({
            'finished': finished,
            'stats': {
                'total': total, 'wins': wins, 'losses': total - wins,
                'win_rate': round(wins / max(total, 1) * 100, 1),
                'total_return': total_ret, 'avg_return': avg_ret,
            }
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/breakout_v3/simulation/trade/<code>', methods=['PUT'])
def api_breakout_v3_update_trade(code):
    """更新已完成交易记录的字段。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    try:
        sp = Vol180SimPortfolio()
        ok = sp.update_finished(code, body)
        if not ok:
            return jsonify({'error': '交易记录不存在', 'success': False}), 404
        return jsonify({
            'success': True,
            'finished': sp.get_all_finished(),
            'summary': sp.get_summary(),
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/breakout_v3/simulation/trade/<code>', methods=['DELETE'])
def api_breakout_v3_delete_trade(code):
    """删除已完成交易记录。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    try:
        sp = Vol180SimPortfolio()
        ok = sp.delete_finished(code)
        if not ok:
            return jsonify({'error': '交易记录不存在', 'success': False}), 404
        return jsonify({
            'success': True,
            'finished': sp.get_all_finished(),
            'summary': sp.get_summary(),
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/breakout_v3/simulation/delete', methods=['POST'])
def api_breakout_v3_simulation_delete():
    """删除持仓/就绪记录 V3。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    if not code:
        return jsonify({'error': 'code required'}), 400
    sp = Vol180SimPortfolio()
    ok = sp.delete_holding(code)
    return jsonify({'success': ok, 'summary': sp.get_summary()})


# ── 涨停复制战法 ──

@app.route('/zt_replica')
def zt_replica():
    return render_template('zt_replica.html')


@app.route('/api/zt_replica/simulation')
def api_zt_replica_simulation():
    """涨停复制战法: 刷新每日状态。"""
    from ..tools.zt_replica_portfolio import ZTReplicaSimPortfolio
    cache_key = 'zt_replica_sim'
    cached = cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    try:
        sp = ZTReplicaSimPortfolio()
        today = datetime.now().strftime('%Y-%m-%d')
        sp.refresh_daily_status(trade_date=today)
        result = sp.get_summary()
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/zt_replica/simulation/scan', methods=['POST'])
def api_zt_replica_simulation_scan():
    """涨停复制战法: 全量扫描（重建候选池+筛选+检测信号）。"""
    from ..tools.zt_replica_portfolio import ZTReplicaSimPortfolio
    body = request.get_json(silent=True) or {}
    trade_date = body.get('date', None)
    try:
        cache_clear('zt_replica_sim')  # 清空缓存
        sp = ZTReplicaSimPortfolio()
        result = sp.run_daily(trade_date=trade_date, force_rebuild_pool=True)
        sp._state['last_update'] = result.get('date', '')
        sp._save()
        summary = sp.get_summary()
        cache_set('zt_replica_sim', summary)
        return jsonify({'success': True, 'stats': result, 'summary': summary})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/zt_replica/simulation/record_buy', methods=['POST'])
def api_zt_replica_simulation_record_buy():
    """涨停复制: 记录模拟买入。"""
    from ..tools.zt_replica_portfolio import ZTReplicaSimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    price = body.get('price', 0)
    if not code: return jsonify({'error': 'code required'}), 400
    sp = ZTReplicaSimPortfolio()
    ok = sp.record_buy(code, actual_price=float(price) if price else None)
    cache_clear('zt_replica_sim')
    return jsonify({'success': ok, 'summary': sp.get_summary()})


@app.route('/api/zt_replica/simulation/record_sell', methods=['POST'])
def api_zt_replica_simulation_record_sell():
    """涨停复制: 记录模拟卖出。"""
    from ..tools.zt_replica_portfolio import ZTReplicaSimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', ''); price = body.get('price', 0)
    if not code or not price: return jsonify({'error': 'code and price required'}), 400
    sp = ZTReplicaSimPortfolio()
    ok = sp.record_sell(code, float(price))
    cache_clear('zt_replica_sim')
    return jsonify({'success': ok, 'summary': sp.get_summary()})


@app.route('/api/zt_replica/simulation/finished')
def api_zt_replica_finished():
    """获取涨停复制已完成交易记录（可编辑）。"""
    from ..tools.zt_replica_portfolio import ZTReplicaSimPortfolio
    try:
        sp = ZTReplicaSimPortfolio()
        finished = sp.get_all_finished()
        total = len(finished)
        wins = sum(1 for f in finished if f.get('is_win'))
        net_rets = [f.get('net_ret', 0) for f in finished]
        total_ret = round(sum(net_rets), 2)
        avg_ret = round(total_ret / max(total, 1), 2)
        return jsonify({
            'finished': finished,
            'stats': {
                'total': total, 'wins': wins, 'losses': total - wins,
                'win_rate': round(wins / max(total, 1) * 100, 1),
                'total_return': total_ret, 'avg_return': avg_ret,
            }
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/zt_replica/simulation/trade/<code>', methods=['PUT'])
def api_zt_replica_update_trade(code):
    """更新涨停复制已完成交易记录的字段。"""
    from ..tools.zt_replica_portfolio import ZTReplicaSimPortfolio
    body = request.get_json(silent=True) or {}
    try:
        sp = ZTReplicaSimPortfolio()
        ok = sp.update_finished(code, body)
        if not ok:
            return jsonify({'error': '交易记录不存在', 'success': False}), 404
        return jsonify({
            'success': True,
            'finished': sp.get_all_finished(),
            'summary': sp.get_summary(),
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/zt_replica/simulation/trade/<code>', methods=['DELETE'])
def api_zt_replica_delete_trade(code):
    """删除涨停复制已完成交易记录。"""
    from ..tools.zt_replica_portfolio import ZTReplicaSimPortfolio
    try:
        sp = ZTReplicaSimPortfolio()
        ok = sp.delete_finished(code)
        if not ok:
            return jsonify({'error': '交易记录不存在', 'success': False}), 404
        return jsonify({
            'success': True,
            'finished': sp.get_all_finished(),
            'summary': sp.get_summary(),
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/zt_replica/simulation/delete', methods=['POST'])
def api_zt_replica_simulation_delete():
    """涨停复制: 删除持仓/就绪记录。"""
    from ..tools.zt_replica_portfolio import ZTReplicaSimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    if not code: return jsonify({'error': 'code required'}), 400
    sp = ZTReplicaSimPortfolio()
    ok = sp.delete_holding(code)
    return jsonify({'success': ok, 'summary': sp.get_summary()})


# ── 统一模拟持仓管理 ──

@app.route('/sim_portfolio')
def sim_portfolio():
    return render_template('sim_portfolio.html')


@app.route('/api/sim_portfolio/state')
def api_sim_portfolio_state():
    """获取模拟持仓完整状态。"""
    from ..tools.sim_holdings import SimPortfolio
    sp = SimPortfolio()
    return jsonify(sp.get_full_state())


@app.route('/api/sim_portfolio/search')
def api_sim_portfolio_search():
    """搜索股票代码/名称。"""
    from ..tools.sim_holdings import SimPortfolio
    keyword = request.args.get('q', '').strip()
    if len(keyword) < 1:
        return jsonify({'results': []})
    sp = SimPortfolio()
    results = sp.search_stocks(keyword, limit=20)
    return jsonify({'results': results})


@app.route('/api/sim_portfolio/watch/add', methods=['POST'])
def api_sim_portfolio_watch_add():
    """添加标的到备选池。"""
    from ..tools.sim_holdings import SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '').strip()
    notes = body.get('notes', '')
    if not code:
        return jsonify({'success': False, 'error': '请输入股票代码'}), 400
    sp = SimPortfolio()
    result = sp.add_to_watch(code, notes)
    if result.get('success'):
        return jsonify(result)
    return jsonify(result), 400


@app.route('/api/sim_portfolio/watch/remove', methods=['POST'])
def api_sim_portfolio_watch_remove():
    """从备选池移除标的。"""
    from ..tools.sim_holdings import SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    if not code: return jsonify({'error': 'code required'}), 400
    sp = SimPortfolio()
    ok = sp.remove_from_watch(code)
    return jsonify({'success': ok})


@app.route('/api/sim_portfolio/buy', methods=['POST'])
def api_sim_portfolio_buy():
    """记录买入：从备选池移入持仓；若已持有同一代码则加仓(自动重算均价)。"""
    from ..tools.sim_holdings import SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    price = body.get('price', 0)
    buy_date = body.get('buy_date', None)
    shares = body.get('shares', 0)
    if not code or not price:
        return jsonify({'error': 'code and price required'}), 400
    sp = SimPortfolio()
    result = sp.record_buy(code, float(price), buy_date=buy_date, shares=int(shares))
    return jsonify({'success': True, 'trade': result, 'state': sp.get_full_state()})


@app.route('/api/sim_portfolio/sell', methods=['POST'])
def api_sim_portfolio_sell():
    """记录卖出：支持部分卖出(减仓)与全部卖出(清仓)。

    请求体可选 shares: 指定卖出股数；省略或 >= 持仓则清仓。
    """
    from ..tools.sim_holdings import SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    price = body.get('price', 0)
    sell_date = body.get('sell_date', None)
    shares = body.get('shares', 0)
    if not code or not price:
        return jsonify({'error': 'code and price required'}), 400
    sp = SimPortfolio()
    result = sp.record_sell(code, float(price), sell_date=sell_date, shares=int(shares or 0))
    if result.get('success'):
        return jsonify(result)
    return jsonify(result), 400


@app.route('/api/sim_portfolio/trade/<tid>', methods=['PUT'])
def api_sim_portfolio_update_trade(tid):
    """更新已完成交易记录。"""
    from ..tools.sim_holdings import SimPortfolio
    body = request.get_json(silent=True) or {}
    sp = SimPortfolio()
    ok = sp.update_finished(tid, body)
    if not ok:
        return jsonify({'error': '交易记录不存在', 'success': False}), 404
    return jsonify({'success': True, 'state': sp.get_full_state()})


@app.route('/api/sim_portfolio/trade/<tid>', methods=['DELETE'])
def api_sim_portfolio_delete_trade(tid):
    """删除已完成交易记录。"""
    from ..tools.sim_holdings import SimPortfolio
    sp = SimPortfolio()
    ok = sp.delete_finished(tid)
    if not ok:
        return jsonify({'error': '交易记录不存在', 'success': False}), 404
    return jsonify({'success': True, 'state': sp.get_full_state()})


# ── V4 实盘候选池（V4 监控手册下方模块） ──

@app.route('/api/v4_pool/scan', methods=['POST'])
def api_v4_pool_scan():
    """运行扫描: 清空之前扫描的候选池并重新全量筛选（同启动突破）。"""
    from ..tools.v4_pool import V4PoolMonitor
    body = request.get_json(silent=True) or {}
    trade_date = body.get('date', None)
    try:
        cache_clear('v4_pool_state')  # 重建后缓存作废
        mon = V4PoolMonitor()
        result = mon.scan(trade_date=trade_date)
        cache_set('v4_pool_state', result)
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/v4_pool/state')
def api_v4_pool_state():
    """刷新状态: 竞价确认昨日选出的标的 + 市场情绪 + 预估价格区间。

    ?fresh=1 → 绕过缓存强制重算（9:25 后竞价数据更新时使用）。
    """
    from ..tools.v4_pool import V4PoolMonitor
    fresh = request.args.get('fresh', '') == '1'
    if not fresh:
        cached = cache_get('v4_pool_state')
        if cached is not None:
            return jsonify(cached)
    try:
        mon = V4PoolMonitor()
        result = mon.refresh()
        cache_set('v4_pool_state', result)
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/v4_pool/mood')
def api_v4_pool_mood():
    """市场情绪: 基于上一交易日判断。"""
    from ..tools.v4_pool import V4PoolMonitor
    try:
        return jsonify(V4PoolMonitor().market_mood())
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/zt_replica/backtest', methods=['POST'])
def api_zt_replica_backtest():
    """运行涨停复制战法一年回测，生成xlsx报告。"""
    import threading
    from ..analysis.zt_replica_backtest import ZTReplicaBacktest, export_xlsx
    from datetime import date, timedelta

    try:
        end_date = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=365)

        output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'analysis')
        output_path = os.path.join(output_dir, 'zt_replica_backtest_result.xlsx')

        bt = ZTReplicaBacktest()
        results = bt.run(start_date=start_date, end_date=end_date)

        if not results or not results.get('trades'):
            return jsonify({'error': '回测未产生任何交易记录'}), 500

        export_xlsx(results, output_path)

        trades = results.get('trades', [])
        total = len(trades)
        wins = sum(1 for t in trades if t['is_win'])
        losses = total - wins
        win_rate = wins / max(total, 1) * 100
        net_rets = [t['net_ret'] for t in trades]
        avg_ret = float(__import__('numpy').mean(net_rets)) if net_rets else 0
        avg_win = float(__import__('numpy').mean([r for r in net_rets if r > 0])) if wins > 0 else 0
        avg_loss = float(__import__('numpy').mean([r for r in net_rets if r <= 0])) if losses > 0 else 0
        max_win = max(net_rets) if net_rets else 0
        max_loss = min(net_rets) if net_rets else 0
        pf = abs(sum(r for r in net_rets if r > 0) / min(sum(r for r in net_rets if r <= 0), -0.01)) if losses > 0 else 999
        avg_days = float(__import__('numpy').mean([t['days_held'] for t in trades])) if trades else 0

        replica_success = sum(1 for t in trades if t.get('replica_success'))
        replica_wins = sum(1 for t in trades if t.get('replica_success') and t['is_win'])
        replica_wr = replica_wins / max(replica_success, 1) * 100

        # 按信号类型统计
        from collections import defaultdict
        by_sig = defaultdict(list)
        for t in trades:
            sig = t.get('sig_type', '未知')
            by_sig[sig].append(t)

        by_sig_stats = {}
        for sig, items in by_sig.items():
            n = len(items)
            w = sum(1 for t in items if t['is_win'])
            by_sig_stats[sig] = {
                'trades': n,
                'win_rate': round(w / max(n, 1) * 100, 1),
                'avg_return': round(float(__import__('numpy').mean([t['net_ret'] for t in items])), 2),
            }

        return jsonify({
            'success': True,
            'xlsx_path': output_path,
            'xlsx_filename': 'zt_replica_backtest_result.xlsx',
            'stats': {
                'total_trades': total,
                'wins': wins,
                'losses': losses,
                'win_rate': round(win_rate, 1),
                'avg_return': round(avg_ret, 2),
                'avg_win': round(avg_win, 2),
                'avg_loss': round(avg_loss, 2),
                'max_win': round(max_win, 2),
                'max_loss': round(max_loss, 2),
                'profit_factor': round(pf, 2),
                'avg_hold_days': round(avg_days, 1),
                'cumulative_return': results.get('cumulative_return', 0),
                'max_drawdown': results.get('max_drawdown', 0),
                'replica_success': replica_success,
                'replica_win_rate': round(replica_wr, 1),
                'total_signals': results.get('total_signals', 0),
                'total_buys': results.get('total_buys', 0),
                'by_sig_type': by_sig_stats,
            }
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ── 竞价分析 ──

@app.route('/auction_analysis')
def auction_analysis():
    return render_template('auction_analysis.html')


@app.route('/api/auction_analysis', methods=['POST'])
def api_auction_analysis():
    """运行全市场竞价四维分析。"""
    from ..analysis.auction_analysis import AuctionAnalyzer
    cache_key = 'auction_analysis'

    # 检查是否有强制刷新标记
    body = request.get_json(silent=True) or {}
    force_refresh = body.get('refresh', False)

    if not force_refresh:
        cached = cache_get(cache_key)
        if cached is not None:
            return jsonify(cached)

    try:
        analyzer = AuctionAnalyzer()
        result = analyzer.analyze()
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ── 启动突破 模拟面板 API（VOL180方法 V1） ──

@app.route('/api/breakout/simulation')
def api_breakout_simulation():
    """模拟交易面板: VOL180突破方法。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    cache_key = 'breakout_sim'
    cached = cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    try:
        sp = Vol180SimPortfolio()
        today = datetime.now().strftime('%Y-%m-%d')
        if sp._state.get('last_update', '') != today:
            sp.run_daily(trade_date=today, mode='v1')
        result = sp.get_summary()
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/breakout/simulation/scan', methods=['POST'])
def api_simulation_scan():
    """手动触发VOL180每日扫描。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    trade_date = body.get('date', None)
    try:
        cache_clear('breakout_sim')  # 清空缓存，强制重跑
        sp = Vol180SimPortfolio()
        result = sp.run_daily(trade_date=trade_date, mode='v1')
        summary = sp.get_summary()
        cache_set('breakout_sim', summary)
        return jsonify({'success': True, 'stats': result, 'summary': summary})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/breakout/simulation/record_buy', methods=['POST'])
def api_simulation_record_buy():
    """记录模拟买入。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    price = body.get('price', 0)
    if not code:
        return jsonify({'error': 'code required'}), 400
    sp = Vol180SimPortfolio()
    ok = sp.record_buy(code, actual_price=float(price) if price else None)
    return jsonify({'success': ok, 'summary': sp.get_summary()})


@app.route('/api/breakout/simulation/record_sell', methods=['POST'])
def api_simulation_record_sell():
    """记录模拟卖出。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    price = body.get('price', 0)
    if not code or not price:
        return jsonify({'error': 'code and price required'}), 400
    sp = Vol180SimPortfolio()
    ok = sp.record_sell(code, float(price))
    return jsonify({'success': ok, 'summary': sp.get_summary()})


@app.route('/api/breakout/simulation/delete', methods=['POST'])
def api_simulation_delete():
    """删除持仓/就绪记录（回退误操作）。"""
    from ..tools.sim_portfolio import Vol180SimPortfolio
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    if not code:
        return jsonify({'error': 'code required'}), 400
    sp = Vol180SimPortfolio()
    ok = sp.delete_holding(code)
    return jsonify({'success': ok, 'summary': sp.get_summary()})


# ── V2 状态机选股池 API ──

@app.route('/api/v2/pool')
def api_v2_pool():
    """获取 V2 选股池完整状态。"""
    from ..tools.daily_pool import V2PoolManager
    from datetime import date
    cache_key = 'v2_pool'
    cached = cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    pm = V2PoolManager()
    try:
        s = pm.get_status_summary()
        if not pm._is_trading_day(date.today()) and s['watch'] == 0 and s['ready'] == 0 and s['holding'] == 0:
            from ..utils.calendar import TradingCalendar
            try:
                cal = TradingCalendar()
                last_day = cal.prev_trading_day(date.today(), offset=1)
                if last_day:
                    ds = last_day.strftime('%Y-%m-%d') if hasattr(last_day, 'strftime') else str(last_day)[:10]
                    if pm.get_status_summary().get('last_update', '') != ds:
                        pm.run_daily_scan(trade_date=ds)
            except Exception:
                pass
        result = pm.get_pool_summary()
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/v2/scan', methods=['POST'])
def api_v2_scan():
    """运行 V2 每日扫描，更新池状态。"""
    from ..tools.daily_pool import V2PoolManager
    body = request.get_json(silent=True) or {}
    trade_date = body.get('date', None)
    pm = V2PoolManager()
    try:
        cache_clear('v2_pool')  # 清空缓存
        stats = pm.run_daily_scan(trade_date=trade_date)
        pool = pm.get_pool_summary()
        cache_set('v2_pool', pool)
        return jsonify({'success': True, 'stats': stats, 'pool': pool})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/v2/pool/record_buy', methods=['POST'])
def api_v2_record_buy():
    """记录实际买入 → 从 READY 移入 HOLDING。"""
    from ..tools.daily_pool import V2PoolManager
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    price = body.get('price', 0)
    buy_date = body.get('buy_date', None)
    if not code or not price:
        return jsonify({'error': 'code and price required'}), 400
    pm = V2PoolManager()
    result = pm.record_buy(code, float(price), buy_date=buy_date)
    return jsonify({'success': True, 'pool': result})


@app.route('/api/v2/pool/record_sell', methods=['POST'])
def api_v2_record_sell():
    """记录实际卖出 → 从 HOLDING 移入 FINISHED。"""
    from ..tools.daily_pool import V2PoolManager
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    price = body.get('price', 0)
    sell_date = body.get('sell_date', None)
    if not code or not price:
        return jsonify({'error': 'code and price required'}), 400
    pm = V2PoolManager()
    result = pm.record_sell(code, float(price), sell_date=sell_date)
    return jsonify({'success': True, 'pool': result})


@app.route('/api/v2/performance')
def api_v2_performance():
    """获取实盘交易绩效统计。"""
    from ..tools.daily_pool import V2PoolManager
    pm = V2PoolManager()
    return jsonify(pm.get_performance_stats())


@app.route('/api/v2/premarket')
def api_v2_premarket():
    """盘前准备数据 — 昨日收盘价、行业、可交易检查。"""
    from ..tools.daily_pool import V2PoolManager
    from ..data.tdx_reader import TdxReader
    from ..data.akshare_fetcher import AkshareFetcher

    cache_key = 'v2_premarket'
    cached = cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    pm = V2PoolManager()
    tdx = TdxReader()
    ak = AkshareFetcher()
    summary = pm.get_pool_summary()

    # 加载行业映射
    imap_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), 'data', 'industry_map.json')
    industry_map = {}
    if os.path.exists(imap_path):
        try:
            with open(imap_path, 'r', encoding='utf-8') as f:
                industry_map = json.load(f)
        except Exception:
            pass

    # 获取实时行情
    spot_map = {}
    try:
        spot_df = ak.get_spot_df()
        if spot_df is not None and not spot_df.empty:
            for _, row in spot_df.iterrows():
                c = str(row.get('代码', '')).strip().zfill(6)
                try:
                    spot_map[c] = {
                        'price': float(row.get('最新价', 0)),
                        'change_pct': float(row.get('涨跌幅', 0)),
                        'open': float(row.get('今开', 0)),
                        'high': float(row.get('最高', 0)),
                        'low': float(row.get('最低', 0)),
                        'amount_yi': float(row.get('成交额', 0)) / 1e8 if float(row.get('成交额', 0)) else 0,
                    }
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass

    buy_today = []
    for b in summary.get('buy_list', []):
        code = b['code']
        spot = spot_map.get(code, {})
        industry = industry_map.get(code, '')
        price = spot.get('price', 0)
        change_pct = spot.get('change_pct', 0)
        checks = {
            'has_price': price > 0,
            'is_limit_up': change_pct >= 9.5 if price > 0 else False,
            'is_limit_down': change_pct <= -9.5 if price > 0 else False,
        }
        buy_today.append({
            'code': code,
            'name': b.get('name', ''),
            'score': b.get('score', 0),
            'industry': industry,
            'prev_close': price,
            'change_pct': round(change_pct, 1),
            'amount_yi': spot.get('amount_yi', 0),
            'checks': checks,
        })

    sell_today = summary.get('daily_ops', {}).get('sell_today', [])
    holdings = summary.get('holding_list', [])

    industry_dist = {}
    for h in holdings:
        ind = industry_map.get(h.get('code', ''), '未知')
        industry_dist[ind] = industry_dist.get(ind, 0) + 1

    total_positions = len(holdings) + len(buy_today)
    max_positions = 8
    suggested_per_position = round(100 / max(total_positions, 1), 1)

    result = {
        'date': summary.get('status', {}).get('last_update', ''),
        'buy_today': buy_today,
        'sell_today': sell_today,
        'holdings': holdings,
        'industry_dist': industry_dist,
        'total_existing': len(holdings),
        'total_new': len(buy_today),
        'total_all': total_positions,
        'max_suggested': max_positions,
        'suggested_per_position': suggested_per_position,
    }
    cache_set(cache_key, result)
    return jsonify(result)


def _ledger_sync(report, trade_date):
    """把复盘报告预测写入台账并验证昨日预测（幂等，失败不影响复盘）"""
    if not report or report.get('error'):
        return
    try:
        from ..prediction_ledger.service import record_day, validate_pending, record_pick_auctions
        record_day(report, trade_date)
        record_pick_auctions(report, trade_date)
        validate_pending(tdx, ak_fetcher)
    except Exception:
        import traceback
        traceback.print_exc()


def _review_migrate_old_cache(cache_key: str, trade_date_ymd: str):
    """一次性迁移：把旧的按天复盘缓存（review_report_*.json）迁到持久缓存。

    旧文件名格式：review_report_latest_YYYY-MM-DD.json / review_report_{YYYYMMDD}_YYYY-MM-DD.json。
    只有 payload 的 date 与目标交易日一致且无 error 才采纳，防止误用别日期的快照。
    """
    import glob
    date_norm = trade_date_ymd.replace('-', '')
    picked, best_mtime = None, 0
    for pattern in (f'review_report_latest_*.json',
                    f'review_report_{date_norm}_*.json'):
        for path in glob.glob(os.path.join(CACHE_DIR, pattern)):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                payload = data.get('_payload')
                if not payload or payload.get('error'):
                    continue
                if str(payload.get('date', '')).replace('-', '') != date_norm:
                    continue
                mtime = os.path.getmtime(path)
                if mtime > best_mtime:
                    best_mtime = mtime
                    picked = payload
            except Exception:
                continue
    if picked is not None:
        cache_set_persistent(cache_key, picked)
    return picked


@app.route('/review')
def review():
    trade_date = request.args.get('date', None)
    refresh = request.args.get('refresh', '0') == '1'
    llm = request.args.get('llm', '0') == '1'

    # 统一日期为 YYYYMMDD（模板里的链接用的是 YYYY-MM-DD）
    if trade_date:
        trade_date = trade_date.replace('-', '')
    else:
        trade_date = DailyReport(tdx, ak_fetcher)._resolve_trade_date()

    cache_key = f'review_report_{trade_date}'

    # 未点刷新 → 用上次爬取保存的数据（跨日保留）；首次运行时迁移旧按天缓存
    if not refresh:
        payload = cache_get_persistent(cache_key)
        data_cached_at = cache_meta_persistent(cache_key).get('_cached_at', '')
        if payload is None:
            payload = _review_migrate_old_cache(cache_key, trade_date)
            if payload is not None:
                data_cached_at = cache_meta_persistent(cache_key).get('_cached_at', '')
        if payload is not None:
            if llm and not payload.get('llm_summary'):
                # 基于缓存数据补 LLM 综述，不重新爬取
                payload['llm_summary'] = DailyReport(tdx, ak_fetcher).generate_llm_summary(
                    trade_date=trade_date, data=payload)
                cache_set_persistent(cache_key, payload)
            # 缓存命中路径：仅渲染缓存页，不触发网络验证（缓存命中即免爬取）。
            # 新报告生成时（下方新生成路径）会自动记录当日预测并验证昨日；
            # 台账页的"验证未验证项"按钮（POST /api/ledger/validate）提供手动触发。
            return render_template('review_v2.html', report=payload, data_cached_at=data_cached_at)

    try:
        report = DailyReport(tdx, ak_fetcher).generate(trade_date)
        if llm:
            try:
                report['llm_summary'] = DailyReport(tdx, ak_fetcher).generate_llm_summary(
                    trade_date=trade_date, data=report)
            except Exception as e:
                report['llm_summary'] = f'AI综述生成失败: {e}'
    except Exception as e:
        import traceback; traceback.print_exc()
        report = {'date': 'N/A', 'total_limit_ups': 0, 'error': str(e)}

    # 只在成功时缓存，避免错误结果污染缓存
    if not report.get('error'):
        cache_set_persistent(cache_key, report)
        data_cached_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    else:
        cache_clear_persistent(cache_key)
        data_cached_at = ''
    _ledger_sync(report, trade_date)    # 新生成路径
    return render_template('review_v2.html', report=report, data_cached_at=data_cached_at)


@app.route('/regime_picks')
def regime_picks():
    """行情诊断 + 今日三战法标的"""
    refresh = request.args.get('refresh', '0') == '1'
    cache_key = 'regime_picks'
    if not refresh:
        cached = cache_get(cache_key)
        if cached is not None:
            return render_template('regime_picks.html', d=cached)
    try:
        d = ld.get_full_diagnosis()
        cache_set(cache_key, d)
    except Exception as e:
        import traceback; traceback.print_exc()
        d = {'error': str(e)}
    return render_template('regime_picks.html', d=d)


@app.route('/stock/<code>')
def stock_detail(code):
    if not (len(code) == 6 and code.isdigit()):
        return render_template('stock_detail.html', code=code, error='无效的股票代码')
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith('8') or code.startswith('4'):
        market = 'bj'
    try:
        df = tdx.read_daily(code, market)
        if df.empty or len(df) < 10:
            return render_template('stock_detail.html', code=code, error='数据不足')

        from ..analysis.indicators import enrich_all
        df = enrich_all(df)
        latest = df.iloc[-1].to_dict()
        prev = df.iloc[-2].to_dict() if len(df) >= 2 else latest

        # 当日实时行情
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
                    is_trading = (now.weekday() < 5 and time(9, 30) <= now.time() <= time(15, 0))
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

        # 均线状态
        ma_status = []
        tdx_close = float(latest['close'])
        for p in [5, 10, 20, 60, 89, 250]:
            ma_val = latest.get(f'ma{p}', 0)
            if ma_val and ma_val > 0:
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

        # 最近20条日线
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

        # 技术面摘要
        close = spot.get('price', float(latest['close']))
        change_pct = spot.get('change_pct', float(latest.get('change_pct', 0)))
        vol_ratio = float(latest.get('volume_ratio', 1))
        dif = float(latest.get('macd_dif', 0))
        dea = float(latest.get('macd_dea', 0))

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
            'macd_bar': round(float(latest.get('macd_bar', 0)), 3),
            'macd_golden': dif > dea,
            'macd_above_zero': dif > 0,
            'today_open': spot.get('open', float(latest['open'])),
            'today_high': spot.get('high', float(latest['high'])),
            'today_low': spot.get('low', float(latest['low'])),
            'tdx_close': round(float(latest['close']), 2),
        }

        # 入场/止损/止盈
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
        import traceback; traceback.print_exc()
        return render_template('stock_detail.html', code=code, error=str(e))


# ── 缓存管理 API ──

@app.route('/api/cache/status')
def api_cache_status():
    """查看当前缓存状态。"""
    return jsonify(get_cache_status())


@app.route('/api/cache/clear', methods=['POST'])
def api_cache_clear():
    """清空所有缓存（或指定命名空间）。"""
    body = request.get_json(silent=True) or {}
    namespace = body.get('namespace', None)
    if namespace:
        ok = cache_clear(namespace)
        return jsonify({'success': ok, 'cleared': namespace if ok else None})
    else:
        count = cache_clear_all()
        return jsonify({'success': True, 'cleared_count': count})


# ── AI 复盘文章生成（本地 Ollama） ──

_article_cache: dict = {}


@app.route('/api/review/article', methods=['POST'])
def api_review_article():
    """用本地 Ollama 按"逻辑哥"模板生成复盘文章。date 支持 YYYYMMDD 或 YYYY-MM-DD。"""
    body = request.get_json(silent=True) or {}
    date_raw = body.get('date') or request.args.get('date') or ''
    personal = (body.get('personal_note') or '').strip()
    # provider 白名单：默认仅本地 Ollama；如需允许其他 provider，设置环境变量
    # ASHARE_ARTICLE_PROVIDERS（逗号分隔），避免任意调用方触发付费 LLM 调用
    _allowed = {p.strip() for p in os.environ.get('ASHARE_ARTICLE_PROVIDERS', 'ollama').split(',') if p.strip()}
    provider = (body.get('provider') or 'ollama')
    if provider not in _allowed:
        provider = 'ollama'
    date_ymd = date_raw.replace('-', '') if date_raw else None

    cache_key = (date_ymd or 'latest', personal)
    if cache_key in _article_cache:
        hit = _article_cache[cache_key]
        return jsonify({'success': True, 'date': hit['date'], 'text': hit['text'], 'cached': True})

    try:
        result = generate_article(date_ymd=date_ymd, personal_note=personal, provider=provider)
        _article_cache[cache_key] = result
        # 顺手写一份到 outputs/，方便在磁盘上找到
        try:
            _write_article_output(result['text'], result['date'])
        except Exception:
            pass
        return jsonify({'success': True, 'date': result['date'], 'text': result['text']})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': f'生成失败: {e}'}), 500


# ======================================================================
# 预测台账（复盘预测的次日验证 + 准确率统计）
# ======================================================================

TYPE_LABELS = {'picks': '精选标的', 'cycle': '情绪周期', 'auction': '竞价预期'}
DIR_LABELS = {'up': '走强', 'down': '退潮', 'high': '高开', 'low': '低开', 'flat': '震荡/平淡'}
ACTUAL_LABELS = {'zt': '涨停', 'up3': '涨≥3%', 'up': '收涨', 'flat': '震荡', 'down': '大跌',
                 'high': '高开', 'low': '低开'}


@app.route('/prediction_ledger')
def prediction_ledger():
    from ..prediction_ledger.service import DB_PATH
    from ..prediction_ledger.store import LedgerStore
    store = LedgerStore(DB_PATH)
    window = request.args.get('days', 60, type=int)
    window = min(max(window, 1), 365)  # 钳制到 1..365
    rows = store.rows(window)
    summary = store.summary(window)
    auction_map = {}
    for ar in store.rows(window):
        if ar.get('pred_type') == 'pick_auction' and ar.get('direction'):
            auction_map[(ar['pred_date'], ar['item_key'])] = ar['direction']
    for r in rows:
        r['type_label'] = TYPE_LABELS.get(r['pred_type'], r['pred_type'])
        if r['pred_type'] == 'picks':
            r['dir_label'] = f"{r['score']}分" if r['score'] is not None else '—'
            r['auction_verdict'] = auction_map.get((r['pred_date'], r['item_key']))
            r['win_label'] = '胜' if r.get('hit') == 1 else ('负' if r.get('hit') == 0 else None)
            r['actual_label'] = ACTUAL_LABELS.get(r['actual'], r['actual'] or '—')
        else:
            r['dir_label'] = DIR_LABELS.get(r['direction'], r['direction'] or '—')
            r['auction_verdict'] = None
            r['win_label'] = '胜' if r.get('hit') == 1 else ('负' if r.get('hit') == 0 else None)
            r['actual_label'] = ACTUAL_LABELS.get(r['actual'], r['actual'] or '—')
        # 明细可读化
        try:
            d = json.loads(r.get('detail') or '{}')
        except Exception:
            d = {}
        if r['pred_type'] == 'picks':
            r['detail_text'] = '、'.join(d.get('reasons', []) or []) or '—'
        elif r['pred_type'] == 'cycle':
            r['detail_text'] = d.get('stage_desc', '') or '—'
        else:
            r['detail_text'] = d.get('forecast_desc', '') or '—'
    return render_template('prediction_ledger.html',
                           rows=rows, summary=summary,
                           type_labels=TYPE_LABELS, dir_labels=DIR_LABELS,
                           actual_labels=ACTUAL_LABELS, window=window)


@app.route('/api/ledger/validate', methods=['POST'])
def api_ledger_validate():
    from ..prediction_ledger.service import validate_pending
    try:
        n = validate_pending(tdx, ak_fetcher)
        return jsonify({'ok': True, 'validated': n})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/ledger/summary')
def api_ledger_summary():
    """预测台账统计摘要（首页徽标用，30 天窗口）"""
    from ..prediction_ledger.service import DB_PATH
    from ..prediction_ledger.store import LedgerStore
    store = LedgerStore(DB_PATH)
    return jsonify(store.summary())


# ── 消息雷达（事件驱动分析） ──

@app.route('/event_radar')
def event_radar():
    """事件驱动分析页面。"""
    from ..event_radar.presets import seed_default_themes
    from ..event_radar.themes import ThemesStore
    store = ThemesStore()
    seed_default_themes(store)
    weekday_cn = '一二三四五六日'
    now = datetime.now()
    today = now.strftime('%Y-%m-%d') + ' 周' + weekday_cn[now.weekday()]
    return render_template('event_radar.html', today=today)


@app.route('/api/radar/themes', methods=['GET', 'POST'])
def api_radar_themes():
    from ..event_radar.themes import Theme, ChainNode, ThemesStore
    store = ThemesStore()
    if request.method == 'GET':
        # 首次访问自动写入预置主题
        from ..event_radar.presets import seed_default_themes
        seed_default_themes(store)
        themes = [{'id': t.id, 'name': t.name, 'last_event': t.last_event, 'updated': t.updated,
                   'chain_nodes': [{'node': n.node, 'concept_name': n.concept_name,
                                    'manual_codes': n.manual_codes} for n in t.chain_nodes]}
                  for t in store.load()]
        return jsonify({'themes': themes, 'total': len(themes)})
    body = request.get_json(silent=True) or {}
    t = Theme(
        id=str(body.get('id', '')).strip(),
        name=str(body.get('name', '')).strip(),
        chain_nodes=[ChainNode(node=str(n.get('node', '')).strip(),
                               concept_name=str(n.get('concept_name', '')).strip(),
                               manual_codes=[str(c) for c in (n.get('manual_codes') or [])])
                     for n in (body.get('chain_nodes') or [])],
    )
    if not t.id or not t.name:
        return jsonify({'success': False, 'error': 'id 和 name 必填'}), 400
    return jsonify({'success': store.add(t)})


@app.route('/api/radar/themes/<theme_id>', methods=['PUT', 'DELETE'])
def api_radar_theme_item(theme_id):
    from ..event_radar.themes import Theme, ChainNode, ThemesStore
    store = ThemesStore()
    if request.method == 'DELETE':
        return jsonify({'success': store.delete(theme_id)})
    body = request.get_json(silent=True) or {}
    t = Theme(id=theme_id, name=str(body.get('name', '')),
              chain_nodes=[ChainNode(node=str(n.get('node', '')).strip(),
                                     concept_name=str(n.get('concept_name', '')).strip(),
                                     manual_codes=[str(c) for c in (n.get('manual_codes') or [])])
                           for n in (body.get('chain_nodes') or [])])
    return jsonify({'success': store.update(theme_id, t)})


@app.route('/api/radar/analyze', methods=['POST'])
def api_radar_analyze():
    """生成分析。body: {date?: str, events: [{theme_id, description}]}"""
    from ..event_radar.themes import ThemesStore
    from ..event_radar.events import RadarEvent, EventsStore
    from ..event_radar.analyze import analyze_event
    from ..event_radar.report import build_result, save_result

    body = request.get_json(silent=True) or {}
    trade_date = (body.get('date') or '').strip() or datetime.now().strftime('%Y-%m-%d')
    ev_inputs = body.get('events') or []
    if not ev_inputs:
        return jsonify({'success': False, 'error': '请至少选择一个事件'}), 400

    store = ThemesStore()
    estore = EventsStore()
    analyzed = []
    for item in ev_inputs:
        theme = store.get(str(item.get('theme_id', '')))
        if theme is None:
            continue
        desc = str(item.get('description', '')).strip()
        estore.add(RadarEvent(date=trade_date, theme_id=theme.id, description=desc))
        analyzed.append(analyze_event(theme, desc, tdx, ak_fetcher, trade_date))
    if not analyzed:
        return jsonify({'success': False, 'error': '未找到有效主题'}), 400
    result = build_result(trade_date, analyzed)
    save_result(result, trade_date)
    return jsonify({'success': True, 'result': result})


@app.route('/api/radar/results')
def api_radar_results():
    from ..event_radar.report import load_result
    d = (request.args.get('date') or '').strip() or datetime.now().strftime('%Y-%m-%d')
    return jsonify({'date': d, 'result': load_result(d)})


@app.route('/api/radar/export')
def api_radar_export():
    from ..event_radar.report import load_result, to_markdown
    d = (request.args.get('date') or '').strip() or datetime.now().strftime('%Y-%m-%d')
    r = load_result(d)
    if r is None:
        return jsonify({'error': f'{d} 无分析结果'}), 404
    md = to_markdown(r)
    saved = None
    try:
        out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'outputs')
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, f'事件雷达_{d}.md')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(md)
        saved = p
    except Exception:
        saved = None
    return jsonify({'success': True, 'markdown': md, 'saved': saved})


def run(host='127.0.0.1', port=5000, debug=None):
    # debug 默认 False（生产安全）；开发环境通过环境变量 ASHARE_DEBUG=1 或显式参数开启
    if debug is None:
        debug = os.environ.get('ASHARE_DEBUG', '') == '1'
    app.run(host=host, port=port, debug=debug)

# ======================================================================
# 策略验证台（统一回测 + 绩效对比）
# ======================================================================

@app.route('/strategy_bench')
def strategy_bench():
    from ..strategy_bench.adapters.registry import list_adapters
    from ..strategy_bench.service import DB_PATH
    from ..strategy_bench.store import BenchStore
    adapters = sorted(list_adapters(), key=lambda a: a.strategy_id)
    adapters_json = [{'strategy_id': a.strategy_id, 'name': a.name,
                      'description': a.description, 'param_schema': a.param_schema}
                     for a in adapters]
    store = BenchStore(DB_PATH)
    return render_template('strategy_bench.html',
                           adapters=adapters_json, snapshots=store.list_snapshots())


@app.route('/api/strategy_bench/run', methods=['POST'])
def api_strategy_bench_run():
    from ..strategy_bench.service import start_job
    data = request.get_json(silent=True) or {}
    strategy_id = data.get('strategy_id', '')
    params = data.get('params', {}) or {}
    job_id = start_job(strategy_id, params)
    return jsonify({'ok': True, 'job_id': job_id})


@app.route('/api/strategy_bench/job/<job_id>')
def api_strategy_bench_job(job_id):
    from ..strategy_bench.service import get_job
    job = get_job(job_id)
    if job is None:
        return jsonify({'error': 'job not found'}), 404
    return jsonify(job)


@app.route('/api/strategy_bench/snapshots')
def api_strategy_bench_snapshots():
    from ..strategy_bench.service import DB_PATH
    from ..strategy_bench.store import BenchStore
    strategy_id = request.args.get('strategy_id') or None
    store = BenchStore(DB_PATH)
    return jsonify({'snapshots': store.list_snapshots(strategy_id=strategy_id)})


@app.route('/api/strategy_bench/compare')
def api_strategy_bench_compare():
    from ..strategy_bench.service import DB_PATH
    from ..strategy_bench.store import BenchStore
    try:
        id_a = int(request.args.get('a', 0))
        id_b = int(request.args.get('b', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid id'}), 400
    if id_a <= 0 or id_b <= 0:
        return jsonify({'error': 'invalid id'}), 400
    store = BenchStore(DB_PATH)
    cmp = store.compare(id_a, id_b)
    if cmp is None:
        return jsonify({'error': 'snapshot not found'}), 404
    return jsonify(cmp)
# ======================================================================
# 风控规则层（Risk Engine）
# ======================================================================

@app.route('/api/risk/config')
def api_risk_config():
    from ..risk.store import RiskStore
    store = RiskStore()
    return jsonify(store.get_all())


@app.route('/api/risk/config', methods=['POST'])
def api_risk_config_save():
    from ..risk.store import RiskStore
    data = request.get_json(silent=True) or {}
    portfolio_id = data.get('portfolio_id', '')
    config = data.get('config', {}) or {}
    try:
        RiskStore().set(portfolio_id, config)
        return jsonify({'ok': True})
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


def _portfolio_risk_state(portfolio_id: str) -> dict:
    """读取对应 portfolio 的 state 文件，计算风控判定所需的组合状态。"""
    import json as _json
    if portfolio_id == 'vol180':
        from ..tools.sim_portfolio import STATE_FILE, INITIAL_CAPITAL as _ic
    else:
        from ..tools.zt_replica_portfolio import STATE_FILE, INITIAL_CAPITAL as _ic
    state_data = {}
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state_data = _json.load(f)
    except Exception:
        pass
    holdings = state_data.get('holding', {}) or {}
    ready = state_data.get('ready', {}) or {}
    positions = len(holdings) + len(ready)
    pos_val = sum(
        h.get('shares', 0) * (h.get('current_price', h.get('buy_price', 0)) or 0)
        for h in holdings.values()
    )
    init_cap = state_data.get('initial_capital', _ic)
    total_value = state_data.get('cash', init_cap) + pos_val
    hist_peak = init_cap
    for snap in state_data.get('portfolio_history', []) or []:
        hist_peak = max(hist_peak, snap.get('total', 0) or 0)
    return {'positions': positions, 'opened_today': state_data.get('today_buys', 0),
            'total_value': total_value, 'history_peak': hist_peak,
            'breaker_tripped': bool((state_data.get('last_risk') or {}).get('breaker_tripped', False))}


@app.route('/api/risk/status')
def api_risk_status():
    from ..risk.evaluate import evaluate
    from ..risk.store import RiskStore
    from ..analysis.strategy_regime import live_diagnosis as _ld
    portfolio_id = request.args.get('portfolio', 'vol180')
    if portfolio_id not in ('vol180', 'zt_replica'):
        return jsonify({'error': 'invalid portfolio'}), 400
    cfg = RiskStore().get(portfolio_id)
    try:
        regime = _ld.get_regime_diagnosis().get('regime', '震荡观望') or '震荡观望'
    except Exception:
        regime = '震荡观望'
    # ── 实时组合状态（读对应 portfolio 的 state 文件） ──
    state = _portfolio_risk_state(portfolio_id)
    risk = evaluate(cfg, state, regime)
    risk['regime'] = regime
    risk['portfolio'] = portfolio_id
    risk['positions'] = state['positions']
    risk['config'] = {k: v for k, v in cfg.items() if k != 'regime_scale'}
    return jsonify(risk)





# ======================================================================
# 今日1进2（视频方法论版）
# ======================================================================

@app.route('/one_two_picks')
def one_two_picks():
    from ..one_two_v2.service import LEDGER_DB, _today
    from ..one_two_v2.ledger import Ledger
    from ..one_two_v2.weights import WeightStore
    ledger = Ledger(LEDGER_DB)
    return render_template('one_two_picks.html',
                           weights=WeightStore().get(),
                           today_picks=ledger.list_picks(_today()),
                           stats=ledger.dimension_stats())


@app.route('/api/one_two/picks/run', methods=['POST'])
def api_one_two_picks_run():
    from ..one_two_v2.service import start_job
    data = request.get_json(silent=True) or {}
    job_id = start_job('picks', {'trade_date': data.get('trade_date') or ''})
    return jsonify({'ok': True, 'job_id': job_id})


@app.route('/api/one_two/picks')
def api_one_two_picks():
    from ..one_two_v2.service import LEDGER_DB, _today
    from ..one_two_v2.ledger import Ledger
    d = request.args.get('date') or _today()
    return jsonify({'picks': Ledger(LEDGER_DB).list_picks(d), 'date': d})


@app.route('/api/one_two/ledger/stats')
def api_one_two_ledger_stats():
    from ..one_two_v2.service import LEDGER_DB
    from ..one_two_v2.ledger import Ledger
    return jsonify(Ledger(LEDGER_DB).dimension_stats())


@app.route('/api/one_two/weights')
def api_one_two_weights():
    from ..one_two_v2.weights import WeightStore
    return jsonify(WeightStore().get())


@app.route('/api/one_two/weights', methods=['POST'])
def api_one_two_weights_save():
    from ..one_two_v2.weights import WeightStore
    data = request.get_json(silent=True) or {}
    try:
        WeightStore().set(data)
        return jsonify({'ok': True})
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/one_two/auction/refresh', methods=['POST'])
def api_one_two_auction_refresh():
    from ..one_two_v2.auction import grade_auction_ratio
    from ..one_two_v2.service import LEDGER_DB
    from ..one_two_v2.ledger import Ledger
    from ..one_two_v2.weights import WeightStore
    from ..data.akshare_fetcher import AkshareFetcher
    from ..utils.calendar import TradingCalendar
    from datetime import date as _date
    try:
        ak = AkshareFetcher()
        auctions = {a.code: a for a in ak.get_auction_data()}
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 502
    th = WeightStore().get()['thresholds']
    ledger = Ledger(LEDGER_DB)
    prev = TradingCalendar().prev_trading_day(_date.today(), offset=1).strftime('%Y%m%d')
    rows = ledger.list_picks(prev) or []
    out = []
    for r in rows:
        a = auctions.get(str(r['code']))
        if not a:
            continue
        g = grade_auction_ratio(a.auction_amount, float(r.get('mcap') or 0), th)
        out.append({'code': r['code'], 'name': r['name'], 'tactic': r['tactic'],
                    'open_change_pct': a.open_change_pct,
                    'auction_amount': a.auction_amount,
                    'ratio': g['ratio'], 'level': g['level'], 'label': g['label']})
    return jsonify({'ok': True, 'items': out})
