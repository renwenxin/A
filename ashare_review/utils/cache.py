"""
通用缓存管理器 — 文件级缓存，按日期自动失效

使用方式：
    from ..utils.cache import cache_get, cache_set, cache_clear, cache_clear_all

    # 读取缓存
    data = cache_get('review')
    if data is None:
        data = expensive_computation()
        cache_set('review', data)

    # 运行扫描时清空缓存
    cache_clear('review')  # 清空单个页面
    cache_clear_all()      # 清空所有页面
"""
import json
import os
import numpy as np
from datetime import date, datetime

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'cache')


class _NumpyEncoder(json.JSONEncoder):
    """处理 numpy 类型的 JSON 编码器"""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _ensure_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def _today_str() -> str:
    return date.today().strftime('%Y-%m-%d')


def _cache_path(namespace: str) -> str:
    """返回缓存文件路径: data/cache/{namespace}_{today}.json"""
    _ensure_dir()
    safe_name = namespace.replace('/', '_').replace('\\', '_')
    return os.path.join(CACHE_DIR, f'{safe_name}_{_today_str()}.json')


def cache_get(namespace: str) -> dict | list | None:
    """读取缓存。key 不存在或日期不是今天 → 返回 None。"""
    path = _cache_path(namespace)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # 验证日期一致性
        cached_date = data.get('_cached_date', '')
        if cached_date != _today_str():
            return None
        return data.get('_payload', None)
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def cache_set(namespace: str, payload) -> None:
    """写入缓存。payload 可以是 dict 或 list。"""
    path = _cache_path(namespace)
    data = {
        '_cached_date': _today_str(),
        '_cached_at': datetime.now().strftime('%H:%M:%S'),
        '_namespace': namespace,
        '_payload': payload,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, cls=_NumpyEncoder)


def cache_clear(namespace: str) -> bool:
    """清空指定命名空间的今天缓存。返回 True 表示清空成功。"""
    path = _cache_path(namespace)
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except OSError:
            pass
    return False


def cache_clear_all() -> int:
    """清空今天的所有缓存。返回清空的文件数。"""
    _ensure_dir()
    today = _today_str()
    count = 0
    try:
        for fname in os.listdir(CACHE_DIR):
            if today in fname and fname.endswith('.json'):
                try:
                    os.remove(os.path.join(CACHE_DIR, fname))
                    count += 1
                except OSError:
                    pass
    except OSError:
        pass
    return count


def cache_clear_stale(days: int = 3) -> int:
    """清理 N 天前的过期缓存文件。返回清理的文件数。"""
    _ensure_dir()
    cutoff = date.today()
    from datetime import timedelta
    cutoff = cutoff - timedelta(days=days)
    count = 0
    try:
        for fname in os.listdir(CACHE_DIR):
            if not fname.endswith('.json'):
                continue
            path = os.path.join(CACHE_DIR, fname)
            try:
                mtime = date.fromtimestamp(os.path.getmtime(path))
                if mtime < cutoff:
                    os.remove(path)
                    count += 1
            except OSError:
                pass
    except OSError:
        pass
    return count


# ── 持久缓存（跨日保留，直到被覆盖/手动清除） ──
# 用于「复盘报告」这类按交易日保存、只在点刷新时才重新爬取的数据。
# 存放在独立子目录 data/cache/persist/，与按天失效的缓存互不干扰。

PERSIST_DIR = os.path.join(CACHE_DIR, 'persist')


def _cache_path_persistent(namespace: str) -> str:
    """持久缓存路径：data/cache/persist/{namespace}.json（不带日期后缀）"""
    os.makedirs(PERSIST_DIR, exist_ok=True)
    safe_name = namespace.replace('/', '_').replace('\\', '_')
    return os.path.join(PERSIST_DIR, f'{safe_name}.json')


def cache_get_persistent(namespace: str) -> dict | list | None:
    """读取持久缓存。不存在/损坏 → 返回 None。"""
    path = _cache_path_persistent(namespace)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('_payload', None)
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def cache_meta_persistent(namespace: str) -> dict:
    """读取持久缓存的元信息（如 _cached_at）。不存在 → {}。"""
    path = _cache_path_persistent(namespace)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if k != '_payload'}
    except (json.JSONDecodeError, OSError):
        return {}


def cache_set_persistent(namespace: str, payload) -> None:
    """写入持久缓存（跨日保留，直到被覆盖/清除）。"""
    path = _cache_path_persistent(namespace)
    data = {
        '_cached_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        '_namespace': namespace,
        '_payload': payload,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, cls=_NumpyEncoder)


def cache_clear_persistent(namespace: str) -> bool:
    """清空指定命名空间的持久缓存。返回 True 表示清空成功。"""
    path = _cache_path_persistent(namespace)
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except OSError:
            pass
    return False


# ── 缓存状态查询 ──

def cache_status() -> dict:
    """返回当前缓存状态：各命名空间的缓存是否存在、缓存时间"""
    _ensure_dir()
    today = _today_str()
    items = {}
    try:
        for fname in os.listdir(CACHE_DIR):
            if not fname.endswith('.json') or today not in fname:
                continue
            path = os.path.join(CACHE_DIR, fname)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                ns = data.get('_namespace', fname)
                items[ns] = {
                    'cached_at': data.get('_cached_at', '?'),
                    'size_kb': round(os.path.getsize(path) / 1024, 1),
                }
            except Exception:
                pass
    except OSError:
        pass
    return {'date': today, 'cached_namespaces': len(items), 'items': items}
