"""周度复盘"""
from ..utils.calendar import TradingCalendar

class WeeklyReport:
    def __init__(self):
        self.cal = TradingCalendar()

    def generate(self, weekly_notes: str = '', daily_reports: list = None) -> dict:
        return {
            'week_start': self.cal.prev_trading_day(offset=5).isoformat(),
            'week_end': self.cal.prev_trading_day().isoformat(),
            'notes': weekly_notes,
            'daily_summaries': daily_reports or [],
            'framework': {
                '宏观驱动力': '',
                '核心受益方向': [],
                '短线情绪': '',
                '题材聚焦': [],
                '仓位建议': '',
                '风险提示': ''
            }
        }
