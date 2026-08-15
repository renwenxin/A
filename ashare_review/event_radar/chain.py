"""产业链展开：节点 → 成分股（akshare 概念成分，失败降级 manual_codes）。"""
from typing import Tuple, List
from .themes import ChainNode


def resolve_node_stocks(node: ChainNode, fetcher) -> Tuple[List[str], str]:
    """返回 (成分股代码列表, 数据源标签)。

    数据源优先级：manual_codes（人工维护）> concept_name（东财概念成分股）。
    返回标签: 'manual' | 'concept' | 'unavailable'
    """
    if node.manual_codes:
        return sorted(set(node.manual_codes)), 'manual'
    if node.concept_name and fetcher is not None:
        try:
            codes = fetcher.get_concept_cons(node.concept_name)
            if codes:
                return codes, 'concept'
        except Exception:
            pass
    return [], 'unavailable'
