import pytest, os, struct
from datetime import date
from ashare_review.data.tdx_reader import TdxReader, parse_day_file


def test_parse_single_record():
    """解析一条32字节记录"""
    record = struct.pack('IIIIIfII', 20260612, 1100, 1125, 1088, 1124, 2263042816.0, 203235552, 0)
    result = parse_day_file('sh000001', record)
    assert len(result) == 1
    assert result[0].code == '000001'
    assert result[0].market == 'sh'
    assert result[0].trade_date == date(2026, 6, 12)
    assert result[0].open == 11.00
    assert result[0].close == 11.24
    assert result[0].volume == 203235552


def test_reader_reads_real_file():
    """能读取本地实际.day文件"""
    path = r'D:\tdx\vipdoc\sz\lday\sz000001.day'
    if not os.path.exists(path):
        pytest.skip('通达信数据未安装')
    reader = TdxReader(tdx_root=r'D:\tdx')
    df = reader.read_daily('000001', 'sz')
    assert len(df) > 1000
    assert df['trade_date'].max() >= date(2026, 6, 1)


def test_reader_lists_all_stocks():
    """能列出所有股票"""
    reader = TdxReader(tdx_root=r'D:\tdx')
    stocks = reader.list_stocks()
    assert len(stocks) > 5000


def _write_day(root, market, code, recs):
    d = os.path.join(str(root), 'vipdoc', market, 'lday')
    os.makedirs(d, exist_ok=True)
    buf = b''.join(struct.pack('IIIIIfII', *r) for r in recs)
    with open(os.path.join(d, f'{market}{code}.day'), 'wb') as f:
        f.write(buf)


def test_market_breadth_open_counts(tmp_path):
    """开盘涨跌家数：高开=open>昨收，低开=open<昨收"""
    from ashare_review.data.tdx_reader import TdxReader
    # 记录格式：date(I) open(I) high(I) low(I) close(I) amount(f) volume(I) reserved(I)
    y = (20260819, 1000, 1010, 990, 1000, 1e8, 1000000, 0)
    # 今日：高开 10.30 收 10.30
    _write_day(tmp_path, 'sh', '600000', [y, (20260820, 1030, 1040, 1020, 1030, 1e8, 1000000, 0)])
    # 今日：低开 9.80 收 9.50
    _write_day(tmp_path, 'sh', '600001', [y, (20260820, 980, 990, 940, 950, 1e8, 1000000, 0)])
    # 今日：平开 10.00 收 10.05
    _write_day(tmp_path, 'sh', '600002', [y, (20260820, 1000, 1010, 995, 1005, 1e8, 1000000, 0)])
    t = TdxReader(tdx_root=str(tmp_path))
    b = t.get_market_breadth(trade_date=None)
    assert b['open_up_count'] == 1
    assert b['open_down_count'] == 1
    assert b['open_flat_count'] == 1
    # 收盘口径独立：600002 收 10.05>10.00 → up
    assert b['up_count'] == 2
    assert b['down_count'] == 1
    assert b['scanned'] == 3


def test_market_breadth_historical_date(tmp_path):
    """指定历史日期：返回该日开盘/收盘口径，不回退最新交易日"""
    from ashare_review.data.tdx_reader import TdxReader
    # 3 天：08-19 收10.00
    _write_day(tmp_path, 'sh', '600000', [
        (20260819, 1000, 1010, 990, 1000, 1e8, 1000000, 0),
        (20260820, 1050, 1060, 1040, 1060, 1e8, 1000000, 0),  # 高开10.50 收10.60
        (20260821, 1030, 1040, 1020, 1040, 1e8, 1000000, 0),  # 低开10.30 收10.40
    ])
    _write_day(tmp_path, 'sh', '600001', [
        (20260819, 1000, 1010, 990, 1000, 1e8, 1000000, 0),
        (20260820, 980, 990, 940, 950, 1e8, 1000000, 0),      # 低开9.80 收9.50
        (20260821, 1020, 1030, 1000, 1020, 1e8, 1000000, 0),  # 高开10.20 收10.20
    ])
    t = TdxReader(tdx_root=str(tmp_path))
    # 08-20：600000 高开(10.5>10) / 600001 低开(9.8<10)
    b20 = t.get_market_breadth(trade_date=date(2026, 8, 20))
    assert b20['scanned'] == 2
    assert b20['open_up_count'] == 1 and b20['open_down_count'] == 1
    # 08-21：600000 低开(10.3<10.6) / 600001 高开(10.2>9.5)
    b21 = t.get_market_breadth(trade_date=date(2026, 8, 21))
    assert b21['open_up_count'] == 1 and b21['open_down_count'] == 1
    # 最新(None)：尾部 08-21
    bn = t.get_market_breadth(trade_date=None)
    assert bn['open_up_count'] == 1 and bn['open_down_count'] == 1
