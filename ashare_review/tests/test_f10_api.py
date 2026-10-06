# -*- coding: utf-8 -*-
"""F10 API 层测试 — 防回归

背景(2026-09-02): /api/stock/f10/<code> 路由只存在于工作树、从未提交，
随 app.py 截断事故丢失 → 前端 fetch 落入 404 HTML 页 → 报
"Unexpected token '<', "<!doctype "... is not valid JSON"。
本测试锁定: 路由存在 + 始终返回 JSON + data 契约(f10/analysis/ctx/fetched_at)。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


_FAKE_F10 = {
    'code': '600127',
    'fetched_date': '2026-09-02',
    'fetched_at': '2026-09-02 12:00:00',
    'company': {'name': '金健米业', 'industry': '农副食品加工业'},
    'business': {'scope': '粮油食品加工'},
    'financial': {'report_date': '2026-03-31'},
    'shareholders': {'top10': []},
    'valuation': {'pe_ttm': 30.0, 'pb': 2.0},
    'lhb': {'records': []},
}


def _client(monkeypatch):
    from ashare_review.web import app as app_module
    monkeypatch.setattr(app_module.f10_fetcher, 'get_f10',
                        lambda code, market='', refresh=False: dict(_FAKE_F10))
    monkeypatch.setattr(app_module, '_build_f10_ctx',
                        lambda code, f10: {'change_pct': 1.5, 'limit_info': None,
                                           'hot_boards': [], 'board_names': ['农副食品加工业']})
    return app_module.app.test_client()


def test_f10_route_exists_and_returns_json(monkeypatch):
    """路由存在且返回 JSON（不是 HTML 404 页）"""
    c = _client(monkeypatch)
    r = c.get('/api/stock/f10/600127')
    assert r.status_code == 200, r.status_code
    # 关键: content-type 必须是 JSON —— HTML 会让前端 r.json() 抛 Unexpected token '<'
    assert 'application/json' in r.content_type, r.content_type
    d = r.get_json()
    assert d['success'] is True
    data = d['data']
    for key in ('f10', 'analysis', 'ctx', 'fetched_at'):
        assert key in data, key
    assert data['f10']['company']['name'] == '金健米业'
    assert data['fetched_at'] == '2026-09-02 12:00:00'
    # 九维分析结构
    assert 'dims' in data['analysis']


def test_f10_refresh_param(monkeypatch):
    """?refresh=1 透传到数据层"""
    from ashare_review.web import app as app_module
    seen = {}

    def _fake_get(code, market='', refresh=False):
        seen['refresh'] = refresh
        return dict(_FAKE_F10)

    monkeypatch.setattr(app_module.f10_fetcher, 'get_f10', _fake_get)
    monkeypatch.setattr(app_module, '_build_f10_ctx',
                        lambda code, f10: {'change_pct': 0, 'limit_info': None,
                                           'hot_boards': [], 'board_names': []})
    c = app_module.app.test_client()
    c.get('/api/stock/f10/600127?refresh=1')
    assert seen.get('refresh') is True
    c.get('/api/stock/f10/600127')
    assert seen.get('refresh') is False


def test_f10_invalid_code_returns_json(monkeypatch):
    """非法代码 → 200 + JSON error（不能是 HTML）"""
    c = _client(monkeypatch)
    r = c.get('/api/stock/f10/abc')
    assert r.status_code == 200
    assert 'application/json' in r.content_type
    d = r.get_json()
    assert d['success'] is False and d['error']


def test_f10_fetch_failure_returns_json(monkeypatch):
    """数据层抛异常 → JSON error 而非 500 HTML"""
    from ashare_review.web import app as app_module

    def _boom(code, market='', refresh=False):
        raise RuntimeError('网络不通')

    monkeypatch.setattr(app_module.f10_fetcher, 'get_f10', _boom)
    c = app_module.app.test_client()
    r = c.get('/api/stock/f10/600127')
    assert r.status_code == 200
    assert 'application/json' in r.content_type
    d = r.get_json()
    assert d['success'] is False and '网络不通' in d['error']


def test_build_f10_ctx_contract(monkeypatch):
    """ctx 契约: change_pct/limit_info/hot_boards/board_names 四键齐全"""
    from ashare_review.web import app as app_module
    class FakeTdx:
        def read_daily(self, code, market):
            import pandas as pd
            return pd.DataFrame({'close': [10.0, 10.5]})
    monkeypatch.setattr(app_module, 'tdx', FakeTdx())
    monkeypatch.setattr(app_module, 'ak_fetcher', type('A', (), {
        'get_limit_up_pool': staticmethod(lambda trade_date=None: [])})())
    ctx = app_module._build_f10_ctx('600127', _FAKE_F10)
    assert set(ctx.keys()) == {'change_pct', 'limit_info', 'hot_boards', 'board_names'}
    assert ctx['change_pct'] == 5.0
    assert ctx['board_names']  # F10 company industry 至少有一项
