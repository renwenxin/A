# ashare_review/strategy_lab/collectors/__init__.py
"""三池 collector 注册表"""
from .breakout_v3 import collect_breakout_v3
from .one2 import collect_one2
from .boll_v9 import collect_boll_v9

COLLECTOR_IDS = ('breakout_v3', 'one2', 'boll_v9')
