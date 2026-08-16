"""CLI：追溯导入历史精选到预测台账

用法: python -m ashare_review.prediction_ledger.migrate
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher
from .service import migrate_picks_history


def main() -> None:
    tdx = TdxReader()
    ak = AkshareFetcher()
    n = migrate_picks_history(tdx, ak)
    print(f'历史精选追溯完成，写入 {n} 条（幂等，可重复运行）')


if __name__ == '__main__':
    main()
