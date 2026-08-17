"""首页全功能总览测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_ledger_summary_api(tmp_path, monkeypatch):
    from ashare_review.prediction_ledger import service as ledger_service
    from ashare_review.prediction_ledger.service import record_day
    from ashare_review.web.app import app
    monkeypatch.setattr(ledger_service, 'DB_PATH', str(tmp_path / 't.db'))
    record_day({
        'sentiment': {'picks': [{'code': '600001', 'name': 'A', 'score': 60, 'reasons': []}]},
        'cycle': {'stage': '发酵期', 'next_bias': 'up', 'metrics': {'total_zt': 60}},
        'auction_forecast': {'forecast': '偏强', 'direction': 'high'},
    }, '20260814', str(tmp_path / 't.db'))
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.get('/api/ledger/summary')
    assert rv.status_code == 200
    data = rv.get_json()
    assert 'picks' in data and 'cycle' in data and 'auction' in data
    assert data['picks']['total'] == 1


def test_ledger_summary_api_empty(tmp_path, monkeypatch):
    from ashare_review.prediction_ledger import service as ledger_service
    from ashare_review.web.app import app
    monkeypatch.setattr(ledger_service, 'DB_PATH', str(tmp_path / 'empty.db'))
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.get('/api/ledger/summary')
    assert rv.status_code == 200
    data = rv.get_json()
    assert data['picks']['total'] == 0 and data['picks']['rate'] is None
