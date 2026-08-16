"""策略验证台 — 编排层（跑回测 + 后台任务）"""
import json
import os
import subprocess
import threading
import time
import uuid
from typing import Dict, List, Optional

from ..utils.calendar import TradingCalendar
from .adapters.registry import get_adapter
from .metrics import compute_metrics
from .store import BenchStore

DB_PATH = os.environ.get(
    'BENCH_DB',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'data', 'strategy_bench.db'))

JOBS: Dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _git_sha() -> str:
    try:
        r = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True,
                           text=True, timeout=5, cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        return r.stdout.strip()[:40] if r.returncode == 0 else ''
    except Exception:
        return ''


def run_backtest(strategy_id: str, params: dict, tdx=None, ak=None,
                 db_path: Optional[str] = None) -> int:
    """同步跑一次回测并落库，返回 snapshot_id；失败返回 0。"""
    db_path = db_path or DB_PATH
    adapter = get_adapter(strategy_id)
    if adapter is None:
        return 0
    try:
        trades = adapter.run(params or {}, tdx=tdx, ak=ak) or []
    except Exception:
        return 0
    if not trades:
        return 0
    metrics = compute_metrics(trades, calendar=TradingCalendar())
    from .metrics import build_equity_curve
    curve = build_equity_curve(trades)
    store = BenchStore(db_path)
    return store.upsert_snapshot(strategy_id, params or {}, _git_sha(),
                                 metrics, curve, len(trades))


def start_job(strategy_id: str, params: dict, tdx=None, ak=None) -> str:
    """启动后台回测任务，返回 job_id。"""
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        JOBS[job_id] = {'status': 'running', 'progress': '排队中', 'snapshot_id': None,
                        'error': None, 'started_at': time.time()}

    def _worker():
        try:
            JOBS[job_id]['progress'] = '回测运行中…'
            snap_id = run_backtest(strategy_id, params, tdx=tdx, ak=ak)
            with _JOBS_LOCK:
                JOBS[job_id]['status'] = 'done' if snap_id else 'error'
                JOBS[job_id]['snapshot_id'] = snap_id
                JOBS[job_id]['error'] = None if snap_id else '无有效交易或回测失败'
        except Exception as e:
            with _JOBS_LOCK:
                JOBS[job_id]['status'] = 'error'
                JOBS[job_id]['error'] = str(e)

    threading.Thread(target=_worker, daemon=True).start()
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    with _JOBS_LOCK:
        return dict(JOBS.get(job_id) or {}) or None
