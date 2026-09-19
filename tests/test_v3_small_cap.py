"""启动突破 V3 — 小票过滤 单元测试（逻辑哥五打法一：只做小票）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_float_mcap_yi():
    from ashare_review.tools.sim_portfolio import float_mcap_yi
    # 流通股本 1e7 手 = 1e9 股；收盘 10 元 → 100 亿
    assert float_mcap_yi(1e7, 10.0) == 100.0
    assert float_mcap_yi(1e7, 5.0) == 50.0
    # 缺数据 → None（调用方放行）
    assert float_mcap_yi(None, 10.0) is None
    assert float_mcap_yi(1e7, None) is None
    assert float_mcap_yi(0, 10.0) is None
