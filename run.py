"""A股复盘选股系统 — 启动入口"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ashare_review.web.app import run

if __name__ == '__main__':
    print("=" * 50)
    print("  A股复盘选股系统 — 启动突破 V3")
    print("  浏览器打开 http://127.0.0.1:5000/breakout_v3")
    print("=" * 50)
    run(debug=True)
