"""
模拟持仓管理器 — 独立于各战法的统一持仓管理

三个板块:
- 备选标的 (watch): 手动添加关注的标的，可记录买入
- 模拟持仓 (holding): 当前持有的标的，支持 加仓/减仓/清仓
- 已完成交易 (finished): 每笔已平仓交易记录（含减仓部分），可编辑

买卖逻辑（贴近真实交易）:
- 买入: 未持有 → 新建持仓；已持有 → 加仓，按加权平均重算持仓成本
- 卖出: 指定部分股数 → 减仓，剩余继续持有，卖出部分记为已完成交易
- 卖出: 不指定股数或 ≥ 持仓 → 清仓，整笔移入已完成交易

数据存储: data/sim_portfolio_state.json
"""
import json
import os
from datetime import date, datetime
from typing import Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
STATE_FILE = os.path.join(DATA_DIR, 'sim_portfolio_state.json')
NAME_MAP_FILE = os.path.join(DATA_DIR, 'stock_name_map.json')


def _today_str() -> str:
    return date.today().strftime('%Y-%m-%d')


class SimPortfolio:
    """统一模拟持仓管理器"""

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self._state = self._load()

    def _load(self) -> dict:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    s = json.load(f)
                s.setdefault('watch', {})
                s.setdefault('holding', {})
                s.setdefault('finished', [])
                s.setdefault('last_update', '')
                s.setdefault('seq', 0)
                # 迁移: 旧版 finished 以 code 为键(同代码只有一笔) → 列表，每笔带唯一 trade_id
                if isinstance(s['finished'], dict):
                    fin_list = []
                    for code, rec in s['finished'].items():
                        s['seq'] += 1
                        rec.setdefault('code', code)
                        rec['trade_id'] = f"T{s['seq']:04d}"
                        fin_list.append(rec)
                    s['finished'] = fin_list
                elif not isinstance(s['finished'], list):
                    s['finished'] = []
                return s
            except Exception:
                pass
        return {'watch': {}, 'holding': {}, 'finished': [], 'last_update': '', 'seq': 0}

    def _save(self):
        self._state['last_update'] = _today_str()
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

    def _new_trade_id(self) -> str:
        """生成自增唯一交易编号。"""
        self._state['seq'] = self._state.get('seq', 0) + 1
        return f"T{self._state['seq']:04d}"

    # ── 名称查找 ──

    def _get_name(self, code: str) -> str:
        """从名称缓存中查找股票名称"""
        code = str(code).zfill(6)
        if os.path.exists(NAME_MAP_FILE):
            try:
                with open(NAME_MAP_FILE, 'r', encoding='utf-8') as f:
                    name_map = json.load(f)
                return name_map.get(code, code)
            except Exception:
                pass
        return code

    def search_stocks(self, keyword: str, limit: int = 20) -> List[dict]:
        """搜索股票（按代码或名称）"""
        if not os.path.exists(NAME_MAP_FILE):
            return []
        try:
            with open(NAME_MAP_FILE, 'r', encoding='utf-8') as f:
                name_map = json.load(f)
        except Exception:
            return []

        results = []
        keyword_lower = keyword.lower().strip()
        for code, name in name_map.items():
            if len(results) >= limit:
                break
            if keyword_lower in code or keyword_lower in str(name).lower():
                results.append({
                    'code': code,
                    'name': str(name),
                })
        return results

    # ── 备选标的操作 ──

    def add_to_watch(self, code: str, notes: str = '') -> dict:
        """添加标的到备选池"""
        code = str(code).zfill(6)
        if code in self._state['watch']:
            return {'success': False, 'error': f'{code} 已在备选池中'}
        if code in self._state['holding']:
            return {'success': False, 'error': f'{code} 已在持仓中'}

        name = self._get_name(code)
        self._state['watch'][code] = {
            'code': code,
            'name': name,
            'added_date': _today_str(),
            'notes': notes,
        }
        self._save()
        return {'success': True, 'code': code, 'name': name}

    def remove_from_watch(self, code: str) -> bool:
        """从备选池移除"""
        code = str(code).zfill(6)
        if code in self._state['watch']:
            del self._state['watch'][code]
            self._save()
            return True
        return False

    # ── 买入/加仓 ──

    def record_buy(self, code: str, buy_price: float, buy_date: str = None,
                   shares: int = 0) -> dict:
        """记录买入 → 从 watch/holding 移入 holding。

        已持有同一代码时自动加仓，按加权平均重算持仓成本。
        """
        code = str(code).zfill(6)
        bd = buy_date or _today_str()
        name = self._get_name(code)
        shares = int(shares)
        if shares <= 0:
            shares = 100

        holding = self._state['holding']

        # 已持有 → 加仓，加权平均成本
        if code in holding:
            pos = holding[code]
            old_shares = pos.get('shares', 0)
            old_cost = pos.get('buy_price', 0) * old_shares
            new_cost = float(buy_price) * shares
            total = old_shares + shares
            avg = (old_cost + new_cost) / total if total > 0 else float(buy_price)
            pos['shares'] = total
            pos['buy_price'] = round(avg, 4)  # 持仓成本(加权均价)
            pos.setdefault('name', name)
            self._save()
            return {'success': True, 'code': code, 'name': pos.get('name', name),
                    'is_add': True, 'shares': shares, 'total_shares': total,
                    'avg_cost': pos['buy_price']}

        # 如果已在 watch 中，继承名称
        if code in self._state['watch']:
            name = self._state['watch'][code].get('name', name)
            del self._state['watch'][code]

        self._state['holding'][code] = {
            'code': code,
            'name': name,
            'buy_date': bd,
            'buy_price': round(float(buy_price), 4),  # 持仓成本(加权均价)
            'shares': shares,
        }
        self._save()
        return {'success': True, 'code': code, 'name': name,
                'is_add': False, 'shares': shares, 'total_shares': shares,
                'avg_cost': round(float(buy_price), 4)}

    # ── 卖出/减仓/清仓 ──

    def record_sell(self, code: str, sell_price: float, sell_date: str = None,
                    shares: int = 0) -> dict:
        """记录卖出 → 从 holding 移入 finished。

        shares<=0 或 >= 持仓 → 清仓；否则 → 减仓(剩余继续持有)。
        """
        code = str(code).zfill(6)
        holding = self._state['holding']
        if code not in holding:
            return {'success': False, 'error': f'{code} 不在持仓中'}

        pos = holding[code]
        sd = sell_date or _today_str()
        bp = pos.get('buy_price', 0)  # 持仓均价
        sp = round(float(sell_price), 2)
        cur_shares = pos.get('shares', 0)

        sell_shares = int(shares)
        if sell_shares <= 0 or sell_shares >= cur_shares:
            # 清仓：全部卖出
            sell_shares = cur_shares
            holding.pop(code)
        else:
            # 减仓：卖出部分，剩余继续持有
            pos['shares'] = cur_shares - sell_shares

        gross_ret = (sp - bp) / bp * 100 if bp > 0 else 0
        # 简单费率 0.15% (买+卖合计约0.3%)
        net_ret = gross_ret - 0.3 if bp > 0 else 0

        trade = {
            'trade_id': self._new_trade_id(),
            'code': code,
            'name': pos.get('name', self._get_name(code)),
            'buy_date': pos.get('buy_date', ''),
            'buy_price': bp,
            'sell_date': sd,
            'sell_price': sp,
            'shares': sell_shares,
            'gross_ret': round(gross_ret, 2),
            'net_ret': round(net_ret, 2),
            'is_win': net_ret > 0,
            'sig_type': pos.get('sig_type', ''),
        }
        self._state['finished'].append(trade)
        self._save()

        remaining = holding[code]['shares'] if code in holding else 0
        return {'success': True, 'code': code, 'trade': trade,
                'is_partial': remaining > 0, 'remaining_shares': remaining}

    # ── 已完成交易编辑 ──

    def _find_finished(self, trade_id: str) -> Optional[dict]:
        for t in self._state['finished']:
            if t.get('trade_id') == trade_id:
                return t
        return None

    def update_finished(self, trade_id: str, updates: dict) -> bool:
        """更新已完成交易字段"""
        fin = self._find_finished(trade_id)
        if fin is None:
            return False

        for key in ('buy_price', 'sell_price', 'net_ret', 'gross_ret',
                    'is_win', 'buy_date', 'sell_date', 'shares', 'sig_type'):
            if key in updates:
                val = updates[key]
                if key in ('buy_price', 'sell_price', 'net_ret', 'gross_ret'):
                    fin[key] = float(val) if val is not None else 0
                elif key == 'is_win':
                    fin[key] = bool(val)
                elif key == 'shares':
                    fin[key] = int(val) if val is not None else 0
                else:
                    fin[key] = str(val) if val is not None else ''

        # 重算收益
        bp = fin.get('buy_price', 0)
        sp = fin.get('sell_price', 0)
        if bp > 0 and sp > 0:
            gr = (sp - bp) / bp * 100
            fin['gross_ret'] = round(gr, 2)
            fin['net_ret'] = round(gr - 0.3, 2)
            fin['is_win'] = fin['net_ret'] > 0

        self._save()
        return True

    def delete_finished(self, trade_id: str) -> bool:
        """删除已完成交易"""
        fins = self._state['finished']
        for i, t in enumerate(fins):
            if t.get('trade_id') == trade_id:
                fins.pop(i)
                self._save()
                return True
        return False

    # ── 状态查询 ──

    def get_full_state(self) -> dict:
        """获取完整状态"""
        watch_list = sorted(
            self._state['watch'].values(),
            key=lambda x: x.get('added_date', ''),
            reverse=True,
        )
        holding_list = sorted(
            self._state['holding'].values(),
            key=lambda x: x.get('buy_date', ''),
            reverse=True,
        )
        finished_list = sorted(
            self._state['finished'],
            key=lambda x: x.get('sell_date', ''),
            reverse=True,
        )

        # 统计
        total_trades = len(finished_list)
        wins = sum(1 for t in finished_list if t.get('is_win'))
        losses = total_trades - wins
        total_ret = round(sum(t.get('net_ret', 0) for t in finished_list), 2)
        avg_ret = round(total_ret / max(total_trades, 1), 2)

        return {
            'date': _today_str(),
            'last_update': self._state.get('last_update', ''),
            'watch': watch_list,
            'holding': holding_list,
            'finished': finished_list,
            'stats': {
                'watch_count': len(watch_list),
                'holding_count': len(holding_list),
                'total_trades': total_trades,
                'wins': wins,
                'losses': losses,
                'win_rate': round(wins / max(total_trades, 1) * 100, 1),
                'total_return': total_ret,
                'avg_return': avg_ret,
            },
        }
