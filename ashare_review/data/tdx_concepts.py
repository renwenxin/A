# ashare_review/data/tdx_concepts.py
"""通达信本地概念库解析（infoharbor_block.dat → concept_map.json）

TDX 的 T0002/hq_cache/infoharbor_block.dat 是文本格式的概念板块定义：
  #GN_概念名,股票数,板块代码,创建日期,更新日期,,\r\n
  0#000008,1#600009,2#920445,...\r\n   （成分行，市场#代码，逗号分隔可跨多行）
市场标记：0=深(sz) 1=沪(sh) 2=北交所(bj)
完全离线，不依赖网络。
"""
import json
import os
from typing import Dict, List


def market_of(mark: int) -> str:
    return {0: 'sz', 1: 'sh', 2: 'bj'}.get(mark, 'sz')


def parse_infoharbor_block(path: str) -> Dict[str, List[str]]:
    """解析 infoharbor_block.dat，返回 {概念名: [6位代码, ...]}（无市场前缀）。"""
    with open(path, 'rb') as f:
        data = f.read()
    try:
        text = data.decode('gbk', errors='replace')
    except Exception:
        text = data.decode('utf-8', errors='replace')
    concepts: Dict[str, List[str]] = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('#GN_'):
            # 概念头：#GN_名称,股票数,板块代码,...
            parts = line[4:].split(',')
            if parts and parts[0]:
                current = parts[0]
                concepts[current] = []
            else:
                current = None
            continue
        if current is None:
            continue
        # 成分行：0#000008,1#600009,...
        for token in line.split(','):
            token = token.strip()
            if '#' in token:
                try:
                    mark_s, code = token.split('#', 1)
                    code = code.strip()
                    if len(code) == 6 and code.isdigit():
                        concepts[current].append(code)
                except (ValueError, IndexError):
                    continue
    return {k: v for k, v in concepts.items() if v}


def build_concept_map(path: str) -> Dict:
    """生成 concept_map.json 兼容结构：{concepts: {概念名: {members, partial, source}}}"""
    concepts = parse_infoharbor_block(path)
    result = {}
    for name, codes in concepts.items():
        result[name] = {
            'members': {c: 1 for c in codes},
            'partial': False,
            'source': 'tdx',
        }
    return {'updated': 'tdx_infoharbor', 'notes': '通达信本地概念库', 'concepts': result}


def main():
    """生成 data/concept_map.json（TDX 概念库版）。用法：python -m ashare_review.data.tdx_concepts"""
    import sys
    tdx_hq = os.environ.get('TDX_HQ_CACHE', r'D:\tdx\T0002\hq_cache')
    src = os.path.join(tdx_hq, 'infoharbor_block.dat')
    dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'data', 'concept_map.json')
    if not os.path.exists(src):
        print(f'未找到 {src}，请确认 TDX 安装路径')
        sys.exit(1)
    cm = build_concept_map(src)
    with open(dst, 'w', encoding='utf-8') as f:
        json.dump(cm, f, ensure_ascii=False, indent=2)
    print(f'生成 {dst}：{len(cm["concepts"])} 个概念，'
          f'成分股 {sum(len(c["members"]) for c in cm["concepts"].values())} 条')


if __name__ == '__main__':
    main()
