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


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/screening')
def screening():
    return render_template('screening.html', screeners=list(SCREENERS.keys()))


@app.route('/api/screen/<strategy>')
def api_screen(strategy):
    if strategy not in SCREENERS:
        return jsonify({'error': 'Unknown strategy'}), 404
    screener = SCREENERS[strategy]
    try:
        results = screener.screen()
        return jsonify([{
            'code': r.code, 'name': r.name, 'score': r.score,
            'reasons': r.reasons, 'detail': r.detail
        } for r in results])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/screen/all')
def api_screen_all():
    all_results = {}
    for name, screener in SCREENERS.items():
        try:
            results = screener.screen()
            all_results[name] = [{
                'code': r.code, 'name': r.name, 'score': r.score,
                'reasons': r.reasons, 'detail': r.detail
            } for r in results[:10]]
        except Exception as e:
            all_results[name] = {'error': str(e)}
    return jsonify(all_results)


@app.route('/review')
def review():
    try:
        report = DailyReport(tdx, ak_fetcher).generate()
    except Exception as e:
        report = {'date': 'N/A', 'total_limit_ups': 0, 'error': str(e)}
    return render_template('review.html', report=report)


@app.route('/stock/<code>')
def stock_detail(code):
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith('8') or code.startswith('4'):
        market = 'bj'
    try:
        df = tdx.read_daily(code, market)
        from ..analysis.indicators import enrich_all
        df = enrich_all(df)
        latest = df.iloc[-1].to_dict() if not df.empty else {}
        return render_template('stock_detail.html', code=code, latest=latest)
    except Exception as e:
        return render_template('stock_detail.html', code=code, error=str(e))


def run(host='127.0.0.1', port=5000, debug=True):
    app.run(host=host, port=port, debug=debug)
