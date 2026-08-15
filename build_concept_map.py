# -*- coding: utf-8 -*-
"""概念映射表构建器 — 维护 ashare_review/data/concept_map.json（概念 → 成分股）

背景：复盘文章"从板块来看"原先只能写粗行业（专用设备/通信设备），因为涨停池只有"所属行业"列。
本表为每只股票提供"细分概念"归属（MLCC概念 / 算力租赁 / 医药电商…），
复盘报告据此聚合成"MLCC概念 3 家涨停，龙头XX"。

数据来源（三个通道，任选其一/组合）：
1. 同花顺概念成分页第一页  --seed-ths   （本机可达，但只取第一页成分，标记 partial）
2. 东财概念成分全量        --em-concepts（需东财接口可达；不可达时自动跳过并提示）
3. 手工 CSV 维护           --import-csv （最可靠，成分全量，标记 manual）

用法:
    python build_concept_map.py --list                       # 查看当前映射表
    python build_concept_map.py --seed-ths MLCC概念,算力租赁   # 从同花顺抓指定概念第一页成分
    python build_concept_map.py --seed-ths                   # 抓映射表里所有 partial 的概念
    python build_concept_map.py --em-concepts MLCC概念        # 东财全量成分覆盖指定概念
    python build_concept_map.py --import-csv my_concepts.csv # 手工维护：概念名,代码[,代码...] 每行
    python build_concept_map.py --drop 医药商业              # 删除一个概念

CSV 格式：每行一个概念，第二列起为成分代码（6位，可逗号/竖线/空格分隔）：
    MLCC概念,002585,300285,300136
    算力租赁,300167,600602
    医药商业,600272,600216

约定：
- 一个概念永远不被删除，除非 --drop 明确指定（合并时保留历史成分，避免每日波动丢数据）。
- partial=True 表示成分不完整（只抓了同花顺第一页），报告中不会把"没匹配到"当成"不属于"。
"""
import argparse
import json
import os
import sys

DATA_DIR = os.path.join('ashare_review', 'data')
CONCEPT_MAP_FILE = os.path.join(DATA_DIR, 'concept_map.json')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def _norm(code) -> str:
    """6位股票代码，去掉空白/小数点"""
    s = str(code).strip().replace('.', '')
    if s.isdigit() and len(s) <= 6:
        return s.zfill(6)
    return s


def _is_stock_code(code: str) -> bool:
    """过滤掉同花顺概念指数(886xxx/885xxx)等非个股代码"""
    return code.startswith(('0', '3', '6', '4', '8')) and not code.startswith(('88', '89'))


def load_map() -> dict:
    if os.path.exists(CONCEPT_MAP_FILE):
        try:
            with open(CONCEPT_MAP_FILE, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f'[警告] 读取概念映射表失败: {e}，按空表处理')
    return {'updated': '', 'notes': '', 'concepts': {}}


def save_map(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    # 排序，方便 diff
    data['concepts'] = {k: data['concepts'][k] for k in sorted(data['concepts'])}
    with open(CONCEPT_MAP_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    n = len(data['concepts'])
    total = sum(len(c.get('members', [])) for c in data['concepts'].values())
    print(f'[完成] 已写入 {CONCEPT_MAP_FILE}：{n} 个概念，共 {total} 条成分')


def _merge_members(concepts: dict, name: str, members: list, source: str, partial: bool) -> None:
    """合并成分：保留历史成分，新增去重；source=东财/manual 时视为全量，替换"""
    members = sorted({_norm(m) for m in members if _is_stock_code(_norm(m))})
    cur = concepts.get(name)
    if cur is None:
        cur = {'members': [], 'partial': partial, 'source': source}
        concepts[name] = cur
    if source in ('em', 'manual') or cur.get('partial'):
        # 全量来源或旧数据不完整 → 直接替换
        cur['members'] = members
    else:
        merged = set(cur.get('members', [])) | set(members)
        cur['members'] = sorted(merged)
    cur['partial'] = partial
    cur['source'] = source


# ------------------------------------------------------------------
# 通道一：同花顺概念成分页第一页（本机可达，partial）
# ------------------------------------------------------------------
def seed_ths(concepts: dict, names: list) -> None:
    import requests
    import re
    # 概念名 → 同花顺概念代码（从同花顺概念列表反查）
    try:
        import akshare as ak
        board = ak.stock_board_concept_name_ths()
        code_map = {str(r['name']): str(r['code']) for _, r in board.iterrows()}
    except Exception as e:
        print(f'[错误] 获取同花顺概念列表失败: {e}')
        return

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    for name in names:
        ths_code = code_map.get(name)
        if not ths_code:
            print(f'[跳过] 同花顺概念列表里没有「{name}」，请用 --em-concepts 或 --import-csv')
            continue
        try:
            r = requests.get(f'http://q.10jqka.com.cn/gn/detail/code/{ths_code}/',
                             timeout=10, headers=headers)
            if r.status_code != 200:
                print(f'[失败] 「{name}」页面 {r.status_code}')
                continue
            members = re.findall(r'>(\d{6})<', r.text)
            _merge_members(concepts, name, members, source='ths', partial=True)
            print(f'[OK] 「{name}」同花顺第一页 {len(members)} 条成分（partial）')
        except Exception as e:
            print(f'[失败] 「{name}」{e!r}')


# ------------------------------------------------------------------
# 通道二：东财概念成分全量（需东财可达）
# ------------------------------------------------------------------
def seed_em(concepts: dict, names: list) -> None:
    import akshare as ak
    for name in names:
        try:
            df = ak.stock_board_concept_cons_em(symbol=name)
            if df is None or df.empty:
                print(f'[失败] 「{name}」无成分数据')
                continue
            members = df['代码'].astype(str).tolist()
            _merge_members(concepts, name, members, source='em', partial=False)
            print(f'[OK] 「{name}」东财全量 {len(members)} 条成分')
        except Exception as e:
            print(f'[失败] 「{name}」东财不可达: {e!r}')


# ------------------------------------------------------------------
# 通道三：手工 CSV 维护（全量）
# ------------------------------------------------------------------
def import_csv(concepts: dict, csv_path: str) -> None:
    if not os.path.exists(csv_path):
        print(f'[错误] 文件不存在: {csv_path}')
        return
    with open(csv_path, encoding='utf-8') as f:
        lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith('#')]
    for ln in lines:
        parts = [p.strip() for p in ln.replace('|', ',').replace(' ', ',').split(',')]
        name = parts[0]
        members = [p for p in parts[1:] if p]
        if not name or not members:
            print(f'[跳过] 无法解析行: {ln}')
            continue
        _merge_members(concepts, name, members, source='manual', partial=False)
        print(f'[OK] 「{name}」手工导入 {len(members)} 条成分')


def show_list(data: dict) -> None:
    concepts = data.get('concepts', {})
    if not concepts:
        print('映射表为空。可用 --seed-ths / --em-concepts / --import-csv 填充。')
        return
    print(f"更新日期: {data.get('updated') or '-'}")
    for name, c in sorted(concepts.items()):
        members = c.get('members', [])
        tag = 'partial' if c.get('partial') else c.get('source', '')
        print(f'  {name}: {len(members)} 只 [{tag}]')


def main():
    ap = argparse.ArgumentParser(description='概念映射表构建器（维护 concept_map.json）')
    ap.add_argument('--list', action='store_true', help='查看当前映射表')
    ap.add_argument('--seed-ths', nargs='?', const='__all__', default=None,
                    help='从同花顺抓概念第一页成分（partial）。默认抓映射表里所有 partial 概念，或逗号分隔概念名')
    ap.add_argument('--em-concepts', nargs='+', default=None,
                    help='东财全量成分覆盖（需东财可达），逗号/空格分隔概念名')
    ap.add_argument('--import-csv', default=None, metavar='FILE',
                    help='从 CSV 手工导入：每行 概念名,代码[,代码...]')
    ap.add_argument('--drop', nargs='+', default=None, help='删除指定概念')
    ap.add_argument('--today', default=None, help='日期标签（默认今天，YYYY-MM-DD）')
    args = ap.parse_args()

    data = load_map()
    concepts = data.setdefault('concepts', {})

    from datetime import datetime
    data['updated'] = args.today or datetime.now().strftime('%Y-%m-%d')

    if args.drop:
        for name in args.drop:
            if name in concepts:
                del concepts[name]
                print(f'[删除] {name}')
            else:
                print(f'[跳过] 不存在: {name}')
    if args.seed_ths is not None:
        names = [] if args.seed_ths == '__all__' else [n.strip() for n in args.seed_ths.split(',') if n.strip()]
        if not names:
            names = [n for n, c in concepts.items() if c.get('partial')]
        seed_ths(concepts, names or list(concepts))
    if args.em_concepts:
        seed_em(concepts, args.em_concepts)
    if args.import_csv:
        import_csv(concepts, args.import_csv)

    if not (args.drop or args.seed_ths or args.em_concepts or args.import_csv or args.list):
        ap.print_help()
        return
    if args.list:
        show_list(data)
    save_map(data)


if __name__ == '__main__':
    main()
