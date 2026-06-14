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
