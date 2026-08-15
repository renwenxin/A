"""
回测数据管理器 — JSON持久化 + CRUD

数据文件: ashare_review/data/backtest_records.json
"""
import json, os, sys, uuid
from datetime import datetime, date
from typing import Dict, List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
RECORDS_FILE = os.path.join(DATA_DIR, 'backtest_records.json')

# 可安全被 float 序列化的 numpy 类型
FLOAT_TYPES = (float, int, np.floating, np.integer)


def _safe_val(v, default=0):
    """安全转为 Python native 数值"""
    if v is None:
        return default
    if isinstance(v, FLOAT_TYPES):
        f = float(v)
        return None if np.isnan(f) or np.isinf(f) else round(f, 2)
    return v


def _make_trade_id() -> str:
    return uuid.uuid4().hex[:12]


class BacktestDashboard:
    """回测数据看板数据层"""

    def __init__(self):
        self._data = self._load()

    # ── 持久化 ──

    def _load(self) -> dict:
        if os.path.exists(RECORDS_FILE):
            try:
                with open(RECORDS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {'trades': [], 'metadata': {
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'last_backtest': None,
            'params': {},
        }}

    def _save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(RECORDS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2, default=str)

    # ── 运行回测 ──

    def run_backtest(self, days: int = 60, hold: int = 10, top_n: int = 3,
                     min_score: int = 0, min_price: float = 0, max_price: float = 0,
                     mode: str = 'append') -> dict:
        """
        运行回测并持久化。
        mode: 'replace' = 清空旧数据, 'append' = 追加（默认）
        """
        from ashare_review.analysis.backtest import BacktestSummary
        bt = BacktestSummary()
        result = bt.run(
            lookback_days=days, hold_days=hold, top_n=top_n,
            min_score=min_score, min_price=min_price, max_price=max_price,
        )

        trades = result.get('all_trades', [])
        if not trades:
            return {'error': '回测未产生任何交易记录', 'stats': self.get_stats()}

        # 转换为内部格式
        new_trades = []
        for t in trades:
            new_trades.append({
                'id': _make_trade_id(),
                'code': str(t.get('code', '')).zfill(6),
                'name': str(t.get('name', '')),
                'entry_date': str(t.get('entry_date', '')),
                'entry_price': _safe_val(t.get('entry_price')),
                'stop_loss': _safe_val(t.get('stop_loss')),
                'target': _safe_val(t.get('target')),
                'exit_date': str(t.get('exit_date', '')),
                'exit_price': _safe_val(t.get('exit_price')),
                'result': t.get('result', 'timeout'),
                'return_pct': _safe_val(t.get('return_pct')),
                'days_held': int(t.get('days_held', 0)),
                'match_count': int(t.get('match_count', 0)),
                'score': int(t.get('score', 0)),
                'notes': '',
            })

        if mode == 'replace':
            self._data['trades'] = new_trades
        else:
            # 追加：用 (code + entry_date) 去重
            existing = {(t['code'], t['entry_date']) for t in self._data['trades']}
            for t in new_trades:
                if (t['code'], t['entry_date']) not in existing:
                    self._data['trades'].append(t)
                    existing.add((t['code'], t['entry_date']))

        self._data['metadata']['last_backtest'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self._data['metadata']['params'] = {
            'days': days, 'hold': hold, 'top_n': top_n,
            'min_score': min_score, 'min_price': min_price, 'max_price': max_price,
            'mode': mode,
        }
        self._save()

        return {
            'success': True,
            'new_trades': len(new_trades),
            'total_trades': len(self._data['trades']),
            'stats': self.get_stats(),
            'trades': self._data['trades'],
        }

    # ── CRUD ──

    def get_all(self) -> List[dict]:
        """返回所有交易记录（按入场日期倒序）"""
        return sorted(self._data['trades'], key=lambda x: x.get('entry_date', ''), reverse=True)

    def get_stats(self) -> dict:
        """重新计算统计摘要"""
        trades = self._data['trades']
        if not trades:
            return {
                'total': 0, 'wins': 0, 'losses': 0, 'timeouts': 0,
                'win_rate': 0, 'avg_return': 0, 'avg_win': 0, 'avg_loss': 0,
                'profit_factor': 0, 'max_win': 0, 'max_loss': 0,
                'by_match': [],
            }

        wins = [t for t in trades if t['result'] == 'win']
        losses = [t for t in trades if t['result'] == 'loss']
        timeouts = [t for t in trades if t['result'] == 'timeout']

        total = len(trades)
        win_count = len(wins)
        loss_count = len(losses)

        rets = [t['return_pct'] for t in trades if t['return_pct'] != 0]
        win_rets = [t['return_pct'] for t in wins]
        loss_rets = [t['return_pct'] for t in losses]

        win_rate = round(win_count / max(win_count + loss_count, 1) * 100, 1)
        avg_return = round(float(np.mean(rets)), 2) if rets else 0
        avg_win = round(float(np.mean(win_rets)), 2) if win_rets else 0
        avg_loss = round(float(np.mean(loss_rets)), 2) if loss_rets else 0
        total_profit = sum(win_rets) if win_rets else 0
        total_loss = sum(abs(r) for r in loss_rets) if loss_rets else 0.01
        profit_factor = round(total_profit / total_loss, 2)
        max_win = round(max(rets), 2) if rets else 0
        max_loss = round(min(rets), 2) if rets else 0
        total_days = len(set(t['entry_date'] for t in trades))

        # 按匹配数分组
        from collections import defaultdict
        by_match = defaultdict(list)
        for t in trades:
            by_match[t.get('match_count', 0)].append(t)

        by_match_list = []
        for mc in sorted(by_match.keys()):
            ts = by_match[mc]
            w = len([t for t in ts if t['result'] == 'win'])
            l = len([t for t in ts if t['result'] == 'loss'])
            rets_m = [t['return_pct'] for t in ts]
            by_match_list.append({
                'match_count': mc,
                'trades': len(ts),
                'wins': w,
                'losses': l,
                'win_rate': round(w / max(w + l, 1) * 100, 1),
                'avg_return': round(float(np.mean(rets_m)), 2) if rets_m else 0,
            })

        return {
            'total': total,
            'wins': win_count,
            'losses': loss_count,
            'timeouts': len(timeouts),
            'total_days': total_days,
            'win_rate': win_rate,
            'avg_return': avg_return,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_win': max_win,
            'max_loss': max_loss,
            'by_match': by_match_list,
            'metadata': self._data.get('metadata', {}),
        }

    def add_trade(self, trade: dict) -> dict:
        """手动添加一笔交易"""
        t = {
            'id': _make_trade_id(),
            'code': str(trade.get('code', '')).zfill(6),
            'name': str(trade.get('name', '')),
            'entry_date': str(trade.get('entry_date', '')),
            'entry_price': _safe_val(trade.get('entry_price')),
            'stop_loss': _safe_val(trade.get('stop_loss')),
            'target': _safe_val(trade.get('target')),
            'exit_date': str(trade.get('exit_date', '')),
            'exit_price': _safe_val(trade.get('exit_price')),
            'result': trade.get('result', 'timeout'),
            'return_pct': _safe_val(trade.get('return_pct')),
            'days_held': int(trade.get('days_held', 0)),
            'match_count': int(trade.get('match_count', 0)),
            'score': int(trade.get('score', 0)),
            'notes': str(trade.get('notes', '')),
        }
        self._data['trades'].append(t)
        self._save()
        return t

    def update_trade(self, trade_id: str, updates: dict) -> Optional[dict]:
        """更新单笔交易字段，返回更新后的记录"""
        for i, t in enumerate(self._data['trades']):
            if t.get('id') == trade_id:
                for key in ('entry_price', 'exit_price', 'stop_loss', 'target',
                           'return_pct', 'days_held', 'match_count', 'score',
                           'code', 'name', 'entry_date', 'exit_date',
                           'result', 'notes'):
                    if key in updates:
                        if key in ('return_pct', 'entry_price', 'exit_price',
                                  'stop_loss', 'target'):
                            self._data['trades'][i][key] = _safe_val(updates[key])
                        elif key in ('days_held', 'match_count', 'score'):
                            self._data['trades'][i][key] = int(updates[key])
                        else:
                            self._data['trades'][i][key] = str(updates[key])
                self._save()
                return self._data['trades'][i]
        return None

    def delete_trade(self, trade_id: str) -> bool:
        """删除单笔交易"""
        before = len(self._data['trades'])
        self._data['trades'] = [t for t in self._data['trades'] if t.get('id') != trade_id]
        if len(self._data['trades']) < before:
            self._save()
            return True
        return False

    def clear_all(self):
        """清空全部交易记录"""
        self._data['trades'] = []
        self._data['metadata'] = {
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'last_backtest': None,
            'params': {},
        }
        self._save()


# ── 全局单例 ──
_dashboard_instance = None


def get_dashboard() -> BacktestDashboard:
    global _dashboard_instance
    if _dashboard_instance is None:
        _dashboard_instance = BacktestDashboard()
    return _dashboard_instance
