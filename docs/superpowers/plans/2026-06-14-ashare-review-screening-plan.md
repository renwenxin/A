# A股复盘+选股系统 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于通达信本地数据+akshare构建A股复盘选股系统，实现1进2/机构票/龙头/突破形态/板块分歧/竞价抢筹六大筛选器

**Architecture:** 三层松耦合 — 数据层(通达信.day+akshare) → 选股引擎(6个独立筛选器) → Flask Web界面(纯HTML)

**Tech Stack:** Python 3, pandas, numpy, akshare, Flask, pyecharts, struct (解析.day)

---

## 文件结构

```
ashare_review/
├── data/
│   ├── __init__.py
│   ├── tdx_reader.py          # 通达信.day文件解析
│   ├── akshare_fetcher.py     # akshare数据获取+缓存
│   └── models.py              # 统一数据模型(dataclass)
├── screening/
│   ├── __init__.py
│   ├── base.py                # 筛选器基类
│   ├── one_two.py             # 1进2筛选
│   ├── institution.py         # 机构票筛选
│   ├── leader.py              # 龙头筛选
│   ├── breakout.py            # 突破形态识别
│   ├── sector_divergence.py   # 板块分歧介入
│   └── auction.py             # 竞价抢筹
├── analysis/
│   ├── __init__.py
│   ├── indicators.py          # 技术指标(MACD/均线/量价关系)
│   ├── pattern.py             # 形态识别(箱体/W底/N字)
│   └── volume.py              # 量价分析+复合炮识别
├── report/
│   ├── __init__.py
│   ├── daily.py               # 日度复盘
│   └── weekly.py              # 周度复盘
├── web/
│   ├── __init__.py
│   ├── app.py                 # Flask主应用
│   ├── templates/
│   │   ├── base.html          # 基础布局
│   │   ├── index.html         # 首页
│   │   ├── screening.html     # 选股面板
│   │   ├── review.html        # 复盘报告
│   │   └── stock_detail.html  # 个股详情
│   └── static/
│       └── style.css
├── utils/
│   ├── __init__.py
│   └── calendar.py            # 交易日历
├── tests/
│   ├── __init__.py
│   ├── test_tdx_reader.py
│   ├── test_indicator.py
│   ├── test_pattern.py
│   ├── test_volume.py
│   ├── test_one_two.py
│   ├── test_institution.py
│   ├── test_leader.py
│   ├── test_breakout.py
│   ├── test_sector_divergence.py
│   └── test_auction.py
├── requirements.txt
└── run.py                     # 启动入口
```

---

### Task 1: 项目脚手架 + 依赖

**Files:**
- Create: `ashare_review/__init__.py`, `ashare_review/data/__init__.py`, `ashare_review/screening/__init__.py`, `ashare_review/analysis/__init__.py`, `ashare_review/report/__init__.py`, `ashare_review/web/__init__.py`, `ashare_review/utils/__init__.py`, `ashare_review/tests/__init__.py`
- Create: `requirements.txt`

- [ ] **Step 1: 创建所有 __init__.py 和目录**

```bash
mkdir -p ashare_review/{data,screening,analysis,report,web/templates,web/static,utils,tests}
touch ashare_review/__init__.py ashare_review/data/__init__.py ashare_review/screening/__init__.py ashare_review/analysis/__init__.py ashare_review/report/__init__.py ashare_review/web/__init__.py ashare_review/utils/__init__.py ashare_review/tests/__init__.py
```

- [ ] **Step 2: 写入 requirements.txt**

```txt
akshare>=1.12.0
pandas>=2.0.0
numpy>=1.24.0
flask>=3.0.0
pyecharts>=2.0.0
pytest>=7.0.0
```

- [ ] **Step 3: 安装依赖**

```bash
pip install -r ashare_review/requirements.txt
```

Expected: 所有包安装成功

- [ ] **Step 4: 验证目录结构**

```bash
find ashare_review -type f | sort
```

---

### Task 2: 数据模型 (models.py)

**Files:**
- Create: `ashare_review/data/models.py`

- [ ] **Step 1: 写入数据模型**

```python
"""统一数据模型 — 屏蔽通达信和akshare的数据差异"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

@dataclass
class DailyBar:
    code: str
    name: str
    market: str          # 'sh' | 'sz' | 'bj'
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int          # 成交量(股)
    amount: float        # 成交额(元)

@dataclass
class StockInfo:
    code: str
    name: str
    market: str
    industry: str = ''
    concept_list: list = field(default_factory=list)
    float_market_cap: float = 0.0  # 流通市值(亿)
    close_price: float = 0.0
    limit_up_count_1y: int = 0   # 近一年涨停次数

@dataclass
class LimitUpInfo:
    code: str
    name: str
    limit_up_time: str       # 涨停时间 如 '09:35'
    seal_amount: float       # 封单额(万元)
    turnover: float          # 成交额(万元)
    float_market_cap: float  # 流通市值(亿)
    consecutive: int         # 连板数
    is_first: bool           # 是否首板
    is_seal: bool            # 是否封死
    board_type: str = ''     # '一字板'|'T字板'|'换手板'

@dataclass
class AuctionInfo:
    code: str
    name: str
    auction_volume: int     # 竞价成交量
    auction_amount: float   # 竞价成交额
    auction_price: float    # 竞价价格
    open_change_pct: float  # 开盘涨跌幅(%)
    preclose_volume: int    # 昨日爆量(最高量柱)

@dataclass
class LhbInfo:
    code: str
    name: str
    trade_date: date
    reason: str             # 上榜原因
    buy_amount: float       # 买入总额(万元)
    sell_amount: float      # 卖出总额(万元)
    net_amount: float       # 净买入(万元)
    seats: list = field(default_factory=list)

@dataclass
class ScreeningResult:
    code: str
    name: str
    strategy: str           # 策略名
    score: float            # 0-100
    reasons: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)
```

- [ ] **Step 2: 验证模型可导入**

```bash
python -c "from ashare_review.data.models import DailyBar, StockInfo, LimitUpInfo, ScreeningResult; print('OK')"
```

---

### Task 3: 通达信 .day 解析器

**Files:**
- Create: `ashare_review/data/tdx_reader.py`
- Create: `ashare_review/tests/test_tdx_reader.py`

- [ ] **Step 1: 编写测试**

```python
# tests/test_tdx_reader.py
import pytest, os, struct
from datetime import date
from ashare_review.data.tdx_reader import TdxReader, parse_day_file

def test_parse_single_record():
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
    path = r'D:\tdx\vipdoc\sz\lday\sz000001.day'
    if not os.path.exists(path):
        pytest.skip('通达信数据未安装')
    reader = TdxReader(tdx_root=r'D:\tdx')
    df = reader.read_daily('000001', 'sz')
    assert len(df) > 1000
    assert df['trade_date'].max() >= date(2026, 6, 1)

def test_reader_lists_all_stocks():
    reader = TdxReader(tdx_root=r'D:\tdx')
    stocks = reader.list_stocks()
    assert len(stocks) > 5000
```

- [ ] **Step 2: 运行测试看失败**

```bash
pytest tests/test_tdx_reader.py -v
```

- [ ] **Step 3: 实现 TdxReader**

```python
"""通达信 .day 文件解析器"""
import os, struct
from datetime import date
from typing import List, Tuple
import pandas as pd
from .models import DailyBar

RECORD_SIZE = 32

def parse_day_file(filename: str, data: bytes) -> List[DailyBar]:
    code = filename[2:8]
    market = filename[:2]
    results = []
    for i in range(len(data) // RECORD_SIZE):
        offset = i * RECORD_SIZE
        dt, op, hi, lo, cl, amt, vol, _ = struct.unpack('IIIIIfII', data[offset:offset+RECORD_SIZE])
        dt_str = str(dt)
        results.append(DailyBar(
            code=code, name='', market=market,
            trade_date=date(int(dt_str[:4]), int(dt_str[4:6]), int(dt_str[6:8])),
            open=op/100.0, high=hi/100.0, low=lo/100.0, close=cl/100.0,
            volume=vol, amount=amt))
    return results

class TdxReader:
    def __init__(self, tdx_root: str = r'D:\tdx'):
        self.vipdoc = os.path.join(tdx_root, 'vipdoc')

    def _market_dir(self, market: str) -> str:
        m = market[:2].lower()
        return os.path.join(self.vipdoc, m, 'lday')

    def list_stocks(self) -> List[Tuple[str, str]]:
        stocks = []
        for mkt in ['sh', 'sz', 'bj']:
            d = self._market_dir(mkt)
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.endswith('.day'):
                        stocks.append((fn[2:8], mkt))
        return sorted(stocks)

    def read_daily(self, code: str, market: str) -> pd.DataFrame:
        fpath = os.path.join(self._market_dir(market), f'{market}{code}.day')
        if not os.path.exists(fpath):
            raise FileNotFoundError(f'数据文件不存在: {fpath}')
        with open(fpath, 'rb') as f:
            bars = parse_day_file(f'{market}{code}', f.read())
        if not bars:
            return pd.DataFrame()
        df = pd.DataFrame([{
            'trade_date': b.trade_date, 'open': b.open, 'high': b.high,
            'low': b.low, 'close': b.close, 'volume': b.volume, 'amount': b.amount
        } for b in bars])
        return df.sort_values('trade_date').reset_index(drop=True)

    def read_multi(self, codes: List[Tuple[str, str]]) -> pd.DataFrame:
        frames = []
        for code, mkt in codes:
            try:
                df = self.read_daily(code, mkt)
                if not df.empty:
                    df['code'], df['market'] = code, mkt
                    frames.append(df)
            except FileNotFoundError:
                pass
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/test_tdx_reader.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add ashare_review/data/models.py ashare_review/data/tdx_reader.py ashare_review/tests/test_tdx_reader.py
git commit -m "feat: data models and TDX .day reader"
```

---

### Task 4: 技术指标计算

**Files:**
- Create: `ashare_review/analysis/indicators.py`
- Create: `ashare_review/tests/test_indicator.py`

- [ ] **Step 1: 编写测试**

```python
# tests/test_indicator.py
import pandas as pd
import numpy as np
from ashare_review.analysis.indicators import calc_ma, calc_macd, calc_ma_converge

def make_df(prices):
    return pd.DataFrame({'close': prices, 'open': prices, 'high': prices, 'low': prices, 'volume': [100000]*len(prices)})

def test_calc_ma():
    df = make_df([10, 12, 11, 13, 14, 15, 16, 15, 14, 13])
    df = calc_ma(df, [5])
    assert 'ma5' in df.columns
    assert abs(df['ma5'].iloc[-1] - 14.6) < 0.01

def test_calc_macd():
    closes = [10.0] * 30 + [10.5]*5 + [11.0]*5  # 上涨趋势
    df = make_df(closes)
    df = calc_macd(df)
    assert 'macd_dif' in df.columns
    assert 'macd_dea' in df.columns
    assert 'macd_bar' in df.columns

def test_calc_ma_converge():
    closes = [10.0]*30 + [10.05]*10 + [10.02]*5  # 横盘
    df = make_df(closes)
    df = calc_ma(df, [60, 89])
    # 填充足够数据模拟60/89均线
    df['ma60'] = [10.0]*35 + [10.03]*10
    df['ma89'] = [10.0]*35 + [10.04]*10
    df = calc_ma_converge(df)
    assert 'ma60_89_converge' in df.columns
```

- [ ] **Step 2: 实现指标**

```python
"""技术指标计算"""
import pandas as pd
import numpy as np

def calc_ma(df: pd.DataFrame, periods: list) -> pd.DataFrame:
    """计算移动平均线"""
    for p in periods:
        df[f'ma{p}'] = df['close'].rolling(window=p).mean()
    return df

def calc_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.DataFrame:
    """MACD指标"""
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    df['macd_dif'] = ema_fast - ema_slow
    df['macd_dea'] = df['macd_dif'].ewm(span=signal, adjust=False).mean()
    df['macd_bar'] = 2 * (df['macd_dif'] - df['macd_dea'])
    return df

def calc_ma_converge(df: pd.DataFrame, short=60, long=89, threshold=0.03) -> pd.DataFrame:
    """检测60/89日均线是否粘合 (价差<3%)"""
    s = df[f'ma{short}']
    l = df[f'ma{long}']
    diff_pct = abs(s - l) / l
    df['ma60_89_converge'] = diff_pct < threshold
    df['ma60_89_slope_up'] = (s.diff(3) > 0) & (l.diff(3) > 0)
    return df

def calc_volume_ratio(df: pd.DataFrame, period=5) -> pd.DataFrame:
    """量比: 当日成交量 / 前N日均量"""
    df['vol_ma5'] = df['volume'].rolling(window=period).mean()
    df['volume_ratio'] = df['volume'] / df['vol_ma5']
    return df

def calc_daily_change(df: pd.DataFrame) -> pd.DataFrame:
    """涨跌幅"""
    df['change_pct'] = df['close'].pct_change() * 100
    return df

def calc_amplitude(df: pd.DataFrame) -> pd.DataFrame:
    """振幅"""
    df['amplitude'] = (df['high'] - df['low']) / df['close'].shift(1) * 100
    return df

def enrich_all(df: pd.DataFrame) -> pd.DataFrame:
    """一键补全所有指标"""
    df = calc_ma(df, [5, 10, 20, 60, 89, 250])
    df = calc_macd(df)
    df = calc_ma_converge(df)
    df = calc_volume_ratio(df)
    df = calc_daily_change(df)
    df = calc_amplitude(df)
    return df
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_indicator.py -v
```

- [ ] **Step 4: Commit**

```bash
git add ashare_review/analysis/indicators.py ashare_review/tests/test_indicator.py
git commit -m "feat: technical indicators (MA, MACD, volume ratio, convergence)"
```

---

### Task 5: 量价分析 + 复合炮识别

**Files:**
- Create: `ashare_review/analysis/volume.py`
- Create: `ashare_review/tests/test_volume.py`

- [ ] **Step 1: 编写测试**

```python
# tests/test_volume.py
import pandas as pd
import numpy as np
from ashare_review.analysis.volume import detect_volume_cannon, classify_volume_price

def test_detect_volume_cannon():
    dates = pd.date_range('2026-01-01', periods=20, freq='B')
    vol = [100]*5 + [300, 350, 400, 320, 200] + [100]*10  # 连续4根放量
    close = [10.0]*5 + [10.5, 11.0, 11.5, 12.0, 11.8] + [12.0]*10
    df = pd.DataFrame({
        'close': close, 'volume': [v*10000 for v in vol],
        'open': close, 'high': close, 'low': close
    }, index=dates)
    result = detect_volume_cannon(df)
    assert len(result) > 0
    assert result[0]['cannon_type'] == '复合炮'

def test_volume_price_classification():
    """量价关系四分类"""
    assert classify_volume_price(close_up=True, volume_up=True) == '放量上涨'
    assert classify_volume_price(close_up=True, volume_up=False) == '量价背离'
    assert classify_volume_price(close_up=False, volume_up=True) == '恐慌抛售'
    assert classify_volume_price(close_up=False, volume_up=False) == '无量阴跌'
```

- [ ] **Step 2: 实现**

```python
"""量价分析 + 成交量复合炮识别"""
import pandas as pd
import numpy as np
from typing import List, Dict

def classify_volume_price(close_up: bool, volume_up: bool) -> str:
    if close_up and volume_up:
        return '放量上涨'
    elif close_up and not volume_up:
        return '量价背离'
    elif not close_up and volume_up:
        return '恐慌抛售'
    elif not close_up and not volume_up:
        return '无量阴跌'

def detect_shrink_consolidation(df: pd.DataFrame, window: int = 10, shrink_ratio: float = 1/3) -> bool:
    """检测缩量横盘：当前成交量缩到前期高量的1/3以下"""
    if len(df) < window * 2:
        return False
    recent_vol = df['volume'].iloc[-window:].mean()
    prior_vol_max = df['volume'].iloc[-window*2:-window].max()
    return prior_vol_max > 0 and recent_vol / prior_vol_max < shrink_ratio

def detect_volume_cannon(df: pd.DataFrame, vol_ma_period: int = 20, burst_multiplier: float = 1.5) -> List[Dict]:
    """识别成交量复合炮: 连续3根及以上放量柱(>1.5倍均量)"""
    if len(df) < vol_ma_period + 5:
        return []
    df = df.copy()
    df['vol_ma'] = df['volume'].rolling(window=vol_ma_period).mean()
    df['is_burst'] = df['volume'] > df['vol_ma'] * burst_multiplier
    # 找连续放量段
    results = []
    i = len(df) - 1
    while i >= 0:
        if df['is_burst'].iloc[i]:
            start = i
            while start > 0 and df['is_burst'].iloc[start-1]:
                start -= 1
            count = i - start + 1
            if count >= 3:
                cannon_type = '复合炮' if count >= 4 else '炮'
                results.append({
                    'start_idx': start, 'end_idx': i,
                    'count': count, 'cannon_type': cannon_type,
                    'start_date': str(df.index[start]),
                    'end_date': str(df.index[i]),
                    'max_volume': int(df['volume'].iloc[start:i+1].max()),
                })
            i = start - 1
        else:
            i -= 1
    return results

def detect_volume_breakout(df: pd.DataFrame, lookback: int = 60) -> bool:
    """当日是否为近期最大量(倍量突破)"""
    if len(df) < lookback:
        return False
    today_vol = df['volume'].iloc[-1]
    prior_max = df['volume'].iloc[-lookback:-1].max()
    return today_vol > prior_max

def volume_price_label(df: pd.DataFrame) -> pd.DataFrame:
    """给每行赋予量价标签"""
    df = df.copy()
    df['close_up'] = df['close'].diff() > 0
    df['volume_up'] = df['volume'].diff() > 0
    df['vp_label'] = df.apply(lambda r: classify_volume_price(r['close_up'], r['volume_up']), axis=1)
    return df
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_volume.py -v
```

- [ ] **Step 4: Commit**

```bash
git add ashare_review/analysis/volume.py ashare_review/tests/test_volume.py
git commit -m "feat: volume-price analysis and compound cannon detection"
```

---

### Task 6: 形态识别 (箱体/W底/N字)

**Files:**
- Create: `ashare_review/analysis/pattern.py`
- Create: `ashare_review/tests/test_pattern.py`

- [ ] **Step 1: 编写测试**

```python
# tests/test_pattern.py
import pandas as pd
import numpy as np
from ashare_review.analysis.pattern import detect_box_breakout, detect_w_bottom, detect_n_pattern

def make_ohlc(closes, vols=None):
    if vols is None:
        vols = [100000]*len(closes)
    return pd.DataFrame({
        'close': closes, 'open': closes, 'high': closes, 'low': closes,
        'volume': vols, 'trade_date': pd.date_range('2026-01-01', periods=len(closes), freq='B')
    })

def test_detect_box_breakout():
    # 40天横盘在10附近 + 最后放量突破
    closes = [10.0]*10 + [10.2, 9.8, 10.1, 10.0]*7 + [10.3, 10.5, 11.5, 12.0]
    vols = [100000]*38 + [300000, 500000, 800000, 1200000]
    df = make_ohlc(closes, vols)
    df['ma60'] = df['close'].rolling(60, min_periods=1).mean()
    df['ma89'] = df['close'].rolling(89, min_periods=1).mean()
    result = detect_box_breakout(df)
    assert result is not None
    assert result['pattern'] == '箱体突破'

def test_detect_n_pattern():
    # 涨一波→缩量回调→重新放量
    closes = [10]*5 + [12, 13, 14] + [13, 12, 11.5, 11.8]*2 + [12, 13, 14, 15]
    df = make_ohlc(closes)
    result = detect_n_pattern(df)
    assert result is not None
```

- [ ] **Step 2: 实现**

```python
"""形态识别: 箱体突破 / W底 / N字回调"""
import pandas as pd
import numpy as np
from typing import Optional, Dict

def detect_box_breakout(df: pd.DataFrame, box_period: int = 40, break_pct: float = 0.03) -> Optional[Dict]:
    """识别底部箱体突破: 横盘震荡后放量突破箱体上沿"""
    if len(df) < box_period + 3:
        return None
    box_slice = df.iloc[-box_period-3:-3]
    recent = df.iloc[-3:]
    box_high = box_slice['close'].max()
    box_low = box_slice['close'].min()
    box_range = box_high - box_low
    if box_range / box_low < 0.15:  # 箱体振幅 < 15%
        return None
    breakout_close = recent['close'].iloc[-1]
    if breakout_close > box_high * (1 + break_pct):
        return {
            'pattern': '箱体突破',
            'box_high': box_high, 'box_low': box_low,
            'box_period': box_period,
            'breakout_price': breakout_close,
            'breakout_pct': (breakout_close - box_high) / box_high * 100
        }
    return None

def detect_w_bottom(df: pd.DataFrame, lookback: int = 60, tolerance: float = 0.03) -> Optional[Dict]:
    """识别W底: 两个低点接近，中间反弹，放量突破颈线"""
    if len(df) < lookback:
        return None
    seg = df.iloc[-lookback:]
    lows = seg['close'].rolling(20).min().dropna()
    if len(lows) < 40:
        return None
    # 找两个低点
    min_idx = lows.idxmin()
    min_val = lows.min()
    left_lows = lows[lows.index < min_idx - pd.Timedelta(days=10)]
    if left_lows.empty:
        return None
    left_min_val = left_lows.min()
    if abs(left_min_val - min_val) / min_val < tolerance:
        neck = seg.loc[left_lows.idxmin():min_idx, 'close'].max()
        if seg['close'].iloc[-1] > neck:
            return {'pattern': 'W底', 'left_low': left_min_val, 'right_low': min_val, 'neck': neck}
    return None

def detect_n_pattern(df: pd.DataFrame, lookback: int = 40) -> Optional[Dict]:
    """识别N字结构: 涨→缩量回调→重新放量拉升"""
    if len(df) < lookback:
        return None
    seg = df.iloc[-lookback:]
    close = seg['close'].values
    vol = seg['volume'].values
    # 找近20日高点后回调>3%，然后突破该高点
    recent_high_idx = close[-20:].argmax() + (len(close) - 20)
    if recent_high_idx >= len(close) - 5:
        return None
    pullback_low = close[recent_high_idx:].min()
    pullback_pct = (close[recent_high_idx] - pullback_low) / close[recent_high_idx]
    if pullback_pct < 0.03:
        return None
    if close[-1] > close[recent_high_idx]:
        vol_before = vol[:recent_high_idx].mean()
        vol_after = vol[recent_high_idx:].mean()
        if vol_after < vol_before * 0.7:
            return {'pattern': 'N字结构', 'high': close[recent_high_idx], 'pullback_low': pullback_low}
    return None
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_pattern.py -v
```

- [ ] **Step 4: Commit**

```bash
git add ashare_review/analysis/pattern.py ashare_review/tests/test_pattern.py
git commit -m "feat: pattern recognition (box breakout, W-bottom, N-wave)"
```

---

### Task 7: akshare 数据获取 + 缓存

**Files:**
- Create: `ashare_review/data/akshare_fetcher.py`

- [ ] **Step 1: 实现 akshare Fetcher**

```python
"""akshare数据获取 + SQLite缓存"""
import akshare as ak
import pandas as pd
import sqlite3
import json
from datetime import datetime, date, timedelta
from typing import List, Optional
from .models import LimitUpInfo, AuctionInfo, LhbInfo, StockInfo

class AkshareFetcher:
    def __init__(self, cache_db: str = 'ashare_review/cache.db'):
        self.cache_db = cache_db
        self._init_cache()

    def _init_cache(self):
        conn = sqlite3.connect(self.cache_db)
        conn.execute('''CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY, data TEXT, updated TIMESTAMP)''')
        conn.commit()
        conn.close()

    def _cache_get(self, key: str, ttl_minutes: int = 5) -> Optional[str]:
        conn = sqlite3.connect(self.cache_db)
        row = conn.execute('SELECT data, updated FROM cache WHERE key=?', (key,)).fetchone()
        conn.close()
        if row and (datetime.now() - datetime.fromisoformat(row[1])).seconds < ttl_minutes * 60:
            return row[0]
        return None

    def _cache_set(self, key: str, data: str):
        conn = sqlite3.connect(self.cache_db)
        conn.execute('INSERT OR REPLACE INTO cache VALUES (?, ?, ?)',
                     (key, data, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_spot_df(self) -> pd.DataFrame:
        """实时行情快照"""
        cache_key = 'spot_all'
        cached = self._cache_get(cache_key, ttl_minutes=2)
        if cached:
            return pd.read_json(cached)
        df = ak.stock_zh_a_spot_em()
        self._cache_set(cache_key, df.to_json())
        return df

    def get_limit_up_pool(self, trade_date: Optional[str] = None) -> List[LimitUpInfo]:
        """当日涨停板列表"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        cache_key = f'zt_pool_{trade_date}'
        cached = self._cache_get(cache_key, ttl_minutes=3)
        if cached:
            return [LimitUpInfo(**d) for d in json.loads(cached)]
        try:
            df = ak.stock_zt_pool_em(date=trade_date)
        except Exception:
            df = ak.stock_zt_pool_strong_em(date=trade_date)
        results = []
        for _, row in df.iterrows():
            results.append(LimitUpInfo(
                code=str(row.get('代码', '')).zfill(6),
                name=str(row.get('名称', '')),
                limit_up_time=str(row.get('涨停时间', row.get('首次封板时间', ''))),
                seal_amount=float(row.get('封单额', row.get('封单资金', 0))) / 10000,
                turnover=float(row.get('成交额', 0)) / 10000,
                float_market_cap=float(row.get('流通市值', 0)) / 1e8,
                consecutive=int(row.get('连板数', 1)),
                is_first=row.get('连板数', 2) == 1,
                is_seal='是' in str(row.get('是否炸板', row.get('封板情况', '是'))),
                is_broken='炸板' in str(row.get('涨停统计', row.get('封板情况', ''))),
                board_type=str(row.get('涨停类型', row.get('板型', '')))
            ))
        self._cache_set(cache_key, json.dumps([{
            'code': r.code, 'name': r.name, 'limit_up_time': r.limit_up_time,
            'seal_amount': r.seal_amount, 'turnover': r.turnover,
            'float_market_cap': r.float_market_cap, 'consecutive': r.consecutive,
            'is_first': r.is_first, 'is_seal': r.is_seal, 'is_broken': r.is_broken,
            'board_type': r.board_type
        } for r in results]))
        return results

    def get_auction_data(self) -> List[AuctionInfo]:
        """集合竞价数据"""
        cache_key = f'auction_{datetime.now().strftime("%Y%m%d")}'
        cached = self._cache_get(cache_key, ttl_minutes=30)  # 竞价数据当天不变
        if cached:
            return [AuctionInfo(**d) for d in json.loads(cached)]
        try:
            df = ak.stock_zh_a_auction_em()
        except Exception:
            return []
        results = []
        for _, row in df.iterrows():
            results.append(AuctionInfo(
                code=str(row.get('代码', '')).zfill(6),
                name=str(row.get('名称', '')),
                auction_volume=int(row.get('竞价成交量', 0)),
                auction_amount=float(row.get('竞价成交额', 0)),
                auction_price=float(row.get('竞价价格', 0)),
                open_change_pct=float(row.get('开盘涨幅', row.get('开盘涨跌幅', 0))),
                preclose_volume=0
            ))
        self._cache_set(cache_key, json.dumps([{
            'code': r.code, 'name': r.name, 'auction_volume': r.auction_volume,
            'auction_amount': r.auction_amount, 'auction_price': r.auction_price,
            'open_change_pct': r.open_change_pct, 'preclose_volume': r.preclose_volume
        } for r in results]))
        return results

    def get_lhb(self, trade_date: Optional[str] = None) -> List[LhbInfo]:
        """龙虎榜"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        cache_key = f'lhb_{trade_date}'
        cached = self._cache_get(cache_key, ttl_minutes=120)
        if cached:
            return [LhbInfo(**d) for d in json.loads(cached)]
        try:
            df = ak.stock_lhb_detail_em(date=trade_date)
        except Exception:
            return []
        results = []
        for _, row in df.iterrows():
            results.append(LhbInfo(
                code=str(row.get('代码', '')).zfill(6),
                name=str(row.get('名称', '')),
                trade_date=date.today(),
                reason=str(row.get('上榜原因', '')),
                buy_amount=float(row.get('买入总计', 0)) / 10000,
                sell_amount=float(row.get('卖出总计', 0)) / 10000,
                net_amount=float(row.get('净买额', 0)) / 10000,
                seats=[]
            ))
        self._cache_set(cache_key, json.dumps([{
            'code': r.code, 'name': r.name, 'trade_date': str(r.trade_date),
            'reason': r.reason, 'buy_amount': r.buy_amount,
            'sell_amount': r.sell_amount, 'net_amount': r.net_amount, 'seats': r.seats
        } for r in results]))
        return results

    def get_concept_boards(self) -> pd.DataFrame:
        """概念板块行情"""
        cache_key = 'concept_boards'
        cached = self._cache_get(cache_key, ttl_minutes=5)
        if cached:
            return pd.read_json(cached)
        df = ak.stock_board_concept_name_em()
        self._cache_set(cache_key, df.to_json())
        return df

    def get_industry_boards(self) -> pd.DataFrame:
        """行业板块行情"""
        cache_key = 'industry_boards'
        cached = self._cache_get(cache_key, ttl_minutes=5)
        if cached:
            return pd.read_json(cached)
        df = ak.stock_board_industry_name_em()
        self._cache_set(cache_key, df.to_json())
        return df
```

- [ ] **Step 2: 验证 akhare 可用**

```bash
python -c "from ashare_review.data.akshare_fetcher import AkshareFetcher; f = AkshareFetcher(); print('akshare fetcher OK')"
```

- [ ] **Step 3: Commit**

```bash
git add ashare_review/data/akshare_fetcher.py
git commit -m "feat: akshare data fetcher with SQLite cache"
```

---

### Task 8: 筛选器基类 + 交易日历

**Files:**
- Create: `ashare_review/screening/base.py`
- Create: `ashare_review/utils/calendar.py`

- [ ] **Step 1: 交易日历**

```python
"""交易日历"""
import pandas as pd
from datetime import date, timedelta
from typing import List

class TradingCalendar:
    def __init__(self):
        self._cache = None

    def _load(self):
        if self._cache is None:
            try:
                import akshare as ak
                df = ak.tool_trade_date_hist_sina()
                self._cache = set(
                    pd.to_datetime(df['trade_date']).dt.date.tolist())
            except Exception:
                self._cache = set()

    def is_trading_day(self, d: date = None) -> bool:
        if d is None:
            d = date.today()
        self._load()
        return d in self._cache

    def prev_trading_day(self, d: date = None, offset: int = 1) -> date:
        if d is None:
            d = date.today()
        self._load()
        count = 0
        while count < offset:
            d = d - timedelta(days=1)
            if d.weekday() < 5:
                count += 1
        return d
```

- [ ] **Step 2: 筛选器基类**

```python
"""筛选器基类"""
from abc import ABC, abstractmethod
from typing import List, Optional
from ..data.models import ScreeningResult
from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher

class BaseScreener(ABC):
    def __init__(self, tdx: TdxReader = None, ak_fetcher: AkshareFetcher = None):
        self.tdx = tdx or TdxReader()
        self.ak = ak_fetcher or AkshareFetcher()

    @abstractmethod
    def screen(self, **kwargs) -> List[ScreeningResult]:
        """执行筛选，返回排序后的结果列表"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """筛选策略名称"""
        pass

    def _score(self, conditions_met: int, total_conditions: int) -> float:
        """根据条件命中比例计算得分"""
        return round(conditions_met / total_conditions * 100, 1)
```

- [ ] **Step 3: Commit**

```bash
git add ashare_review/screening/base.py ashare_review/utils/calendar.py
git commit -m "feat: screener base class and trading calendar"
```

---

### Task 9: 1进2筛选器

**Files:**
- Create: `ashare_review/screening/one_two.py`
- Create: `ashare_review/tests/test_one_two.py`

- [ ] **Step 1: 实现 1进2筛选器**

```python
"""1进2筛选器"""
import pandas as pd
from datetime import datetime
from typing import List
from .base import BaseScreener
from ..data.models import ScreeningResult, LimitUpInfo
from ..analysis.indicators import enrich_all

class OneTwoScreener(BaseScreener):
    name = '1进2'

    def screen(self, night_mode: bool = True) -> List[ScreeningResult]:
        """night_mode=True: 盘后预选; False: 次日竞价确认"""
        limit_ups = self.ak.get_limit_up_pool()
        auctions = {a.code: a for a in self.ak.get_auction_data()} if not night_mode else {}
        results = []
        for lu in limit_ups:
            if not lu.is_first:
                continue
            if lu.board_type == '一字板':
                continue
            score, reasons = self._evaluate_first_board(lu, auctions.get(lu.code))
            if score > 0:
                results.append(ScreeningResult(
                    code=lu.code, name=lu.name, strategy=self.name,
                    score=score, reasons=reasons,
                    detail={'limit_up_time': lu.limit_up_time, 'seal_amount': lu.seal_amount}
                ))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:30]

    def _evaluate_first_board(self, lu: LimitUpInfo, auction=None) -> tuple:
        score = 0
        reasons = []
        # 1) 流通市值 10-100亿
        if 10 <= lu.float_market_cap <= 100:
            score += 15
            reasons.append(f'流通市值{lu.float_market_cap:.0f}亿，合适')
        elif lu.float_market_cap < 200:
            score += 5
        # 2) 涨停时间越早越好
        time_str = str(lu.limit_up_time).replace(':', '')[:4]
        try:
            t = int(time_str)
            if t <= 1000:
                score += 20
                reasons.append('10点前涨停')
            elif t <= 1100:
                score += 10
                reasons.append('11点前涨停')
            elif t <= 1400:
                score += 5
        except ValueError:
            pass
        # 3) 封成比 > 0.5
        if lu.turnover > 0:
            seal_ratio = lu.seal_amount / (lu.turnover / 10000)
            if seal_ratio > 0.5:
                score += 20
                reasons.append(f'封成比{seal_ratio:.2f}>0.5')
            elif seal_ratio > 0.3:
                score += 10
        # 4) 封单额/流通市值 > 0.015
        if lu.float_market_cap > 0:
            seal_strength = lu.seal_amount / (lu.float_market_cap * 10000)
            if seal_strength > 0.015:
                score += 15
                reasons.append(f'封单强度{seal_strength:.3f}>0.015')
        # 5) 封死且未炸板
        if lu.is_seal and not lu.is_broken:
            score += 10
            reasons.append('封死未炸板')
        # 6) 竞价确认 (次日模式)
        if auction:
            if auction.open_change_pct >= 3:
                score += 15
                reasons.append(f'竞价高开{auction.open_change_pct:.1f}%')
            if auction.auction_volume > auction.preclose_volume * 0.5:
                score += 10
                reasons.append('竞价量>昨日爆量50%')
        return score, reasons
```

- [ ] **Step 2: 验证导入**

```bash
python -c "from ashare_review.screening.one_two import OneTwoScreener; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add ashare_review/screening/one_two.py
git commit -m "feat: 1-into-2 board screener"
```

---

### Task 10: 机构票 + 龙头筛选器

**Files:**
- Create: `ashare_review/screening/institution.py`
- Create: `ashare_review/screening/leader.py`

- [ ] **Step 1: 机构票筛选器**

```python
"""机构票筛选器"""
import pandas as pd
from typing import List
from .base import BaseScreener
from ..data.models import ScreeningResult

class InstitutionScreener(BaseScreener):
    name = '机构票'

    def screen(self) -> List[ScreeningResult]:
        spot_df = self.ak.get_spot_df()
        results = []
        for _, row in spot_df.iterrows():
            code = str(row.get('代码', '')).zfill(6)
            name = str(row.get('名称', ''))
            change_pct = float(row.get('涨跌幅', 0))
            float_mcap = float(row.get('流通市值', 0)) / 1e8
            score = 0
            reasons = []
            # 流通市值>20亿 (机构票通常偏大)
            if float_mcap > 20:
                score += 10
            # 日内涨幅>8% (底部反弹信号)
            if change_pct > 8:
                score += 30
                reasons.append(f'日内涨{change_pct:.1f}%')
            elif change_pct > 5:
                score += 15
                reasons.append(f'日内涨{change_pct:.1f}%')
            # 非ST
            if 'ST' not in name and '*ST' not in name:
                score += 5
            if score >= 20:
                results.append(ScreeningResult(
                    code=code, name=name, strategy=self.name,
                    score=score, reasons=reasons,
                    detail={'change_pct': change_pct, 'float_market_cap': float_mcap}
                ))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:50]
```

- [ ] **Step 2: 龙头筛选器**

```python
"""龙头筛选器"""
from typing import List
from .base import BaseScreener
from ..data.models import ScreeningResult, LimitUpInfo
from ..analysis.pattern import detect_n_pattern
from ..analysis.indicators import enrich_all
import pandas as pd

class LeaderScreener(BaseScreener):
    name = '龙头'

    def screen(self) -> List[ScreeningResult]:
        limit_ups = self.ak.get_limit_up_pool()
        # 只看连板 >= 2
        leaders = [lu for lu in limit_ups if lu.consecutive >= 2]
        # 拉日线检查N字结构
        results = []
        for lu in leaders:
            score = 30  # 连板基础分
            reasons = [f'{lu.consecutive}连板']
            try:
                market = 'sh' if lu.code.startswith('6') else 'sz'
                if lu.code.startswith('8') or lu.code.startswith('4'):
                    market = 'bj'
                df = self.tdx.read_daily(lu.code, market)
                if not df.empty:
                    df = enrich_all(df)
                    n_pattern = detect_n_pattern(df)
                    if n_pattern:
                        score += 25
                        reasons.append('N字结构')
                    # 换手龙 vs 一字龙
                    if lu.board_type != '一字板':
                        score += 20
                        reasons.append('换手板上位')
                    # 涨停时间
                    try:
                        t = int(str(lu.limit_up_time).replace(':', '')[:4])
                        if t <= 1000:
                            score += 15
                            reasons.append('早盘封板')
                    except ValueError:
                        pass
            except Exception:
                pass
            results.append(ScreeningResult(
                code=lu.code, name=lu.name, strategy=self.name,
                score=score, reasons=reasons,
                detail={'consecutive': lu.consecutive, 'board_type': lu.board_type}
            ))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:20]
```

- [ ] **Step 3: Commit**

```bash
git add ashare_review/screening/institution.py ashare_review/screening/leader.py
git commit -m "feat: institution stock and leader stock screeners"
```

---

### Task 11: 形态突破 + 板块分歧 + 竞价筛选器

**Files:**
- Create: `ashare_review/screening/breakout.py`
- Create: `ashare_review/screening/sector_divergence.py`
- Create: `ashare_review/screening/auction.py`

- [ ] **Step 1: 形态突破筛选器**

```python
"""突破形态筛选器"""
from typing import List
from .base import BaseScreener
from ..data.models import ScreeningResult
from ..analysis.indicators import enrich_all
from ..analysis.pattern import detect_box_breakout, detect_w_bottom, detect_n_pattern
from ..analysis.volume import detect_volume_breakout, detect_volume_cannon

class BreakoutScreener(BaseScreener):
    name = '突破形态'

    def screen(self, sample_size: int = 200) -> List[ScreeningResult]:
        stocks = self.tdx.list_stocks()[:sample_size]
        results = []
        for code, market in stocks:
            try:
                df = self.tdx.read_daily(code, market)
                if len(df) < 60:
                    continue
                df = enrich_all(df)
                score, reasons = 0, []
                box = detect_box_breakout(df)
                if box:
                    score += 30
                    reasons.append(f'箱体突破({box["box_period"]}天)')
                wb = detect_w_bottom(df)
                if wb:
                    score += 25
                    reasons.append('W底突破')
                n_pat = detect_n_pattern(df)
                if n_pat:
                    score += 20
                    reasons.append('N字结构')
                if detect_volume_breakout(df):
                    score += 15
                    reasons.append('放量突破')
                cannons = detect_volume_cannon(df)
                if cannons:
                    cannon = cannons[0]
                    score += 15
                    reasons.append(f'成交量{cannon["cannon_type"]}({cannon["count"]}连)')
                if score >= 30:
                    results.append(ScreeningResult(
                        code=code, name='', strategy=self.name,
                        score=min(score, 100), reasons=reasons,
                        detail={'close': float(df['close'].iloc[-1])}
                    ))
            except Exception:
                pass
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:30]
```

- [ ] **Step 2: 板块分歧介入筛选器**

```python
"""板块分歧介入筛选器"""
from typing import List
from .base import BaseScreener
from ..data.models import ScreeningResult
from collections import Counter

class SectorDivergenceScreener(BaseScreener):
    name = '板块分歧介入'

    def screen(self) -> List[ScreeningResult]:
        limit_ups = self.ak.get_limit_up_pool()
        # 统计板块涨停数
        boards = self.ak.get_concept_boards()
        board_names = set(boards.get('板块名称', [])) if not boards.empty else set()
        # 简化：按涨停板连板数找热点板块
        hot_codes = set()
        for lu in limit_ups:
            if lu.consecutive >= 2:
                hot_codes.add(lu.code)
        # 找板块涨停潮（一个板块有5+涨停）
        sector_counts = Counter()
        sector_stocks = {}
        for lu in limit_ups:
            sector_counts[lu.board_type or '未知'] += 1
            if lu.board_type not in sector_stocks:
                sector_stocks[lu.board_type] = []
            sector_stocks[lu.board_type].append(lu.code)
        results = []
        for sector, count in sector_counts.items():
            if count >= 5:  # 涨停潮
                stocks = sector_stocks.get(sector, [])
                for code in stocks[:10]:
                    results.append(ScreeningResult(
                        code=code, name='', strategy=self.name,
                        score=60, reasons=[f'{sector}涨停潮({count}只)', '关注分歧日低吸'],
                        detail={'sector': sector, 'total_limit_up': count}
                    ))
        return results[:20]
```

- [ ] **Step 3: 竞价抢筹筛选器**

```python
"""竞价抢筹筛选器"""
from typing import List
from .base import BaseScreener
from ..data.models import ScreeningResult, AuctionInfo
from ..data.tdx_reader import TdxReader

class AuctionScreener(BaseScreener):
    name = '竞价抢筹'

    def screen(self) -> List[ScreeningResult]:
        auctions = self.ak.get_auction_data()
        results = []
        for a in auctions:
            if a.auction_volume == 0:
                continue
            score, reasons = 0, []
            # 竞价量需要和昨日爆量对比（简化为昨日成交量）
            try:
                market = 'sh' if a.code.startswith('6') else 'sz'
                df = self.tdx.read_daily(a.code, market)
                if not df.empty:
                    yesterday_vol = df['volume'].iloc[-1]
                    yesterday_max = df['volume'].iloc[-20:].max()
                    a.preclose_volume = yesterday_max
                    # 竞价量 > 昨日爆量 50%
                    if yesterday_max > 0:
                        ratio = a.auction_volume / yesterday_max
                        if ratio >= 0.5:
                            score += 35
                            reasons.append(f'竞价量/昨日爆量={ratio:.2f}')
                        elif ratio >= 0.3:
                            score += 15
            except Exception:
                pass
            # 高开
            if a.open_change_pct >= 3:
                score += 25
                reasons.append(f'高开{a.open_change_pct:.1f}%')
            elif a.open_change_pct >= 0:
                score += 10
            # 竞价额
            if a.auction_amount > 500:
                score += 15
                reasons.append(f'竞价额{a.auction_amount:.0f}万')
            if score >= 25:
                results.append(ScreeningResult(
                    code=a.code, name=a.name, strategy=self.name,
                    score=min(score, 100), reasons=reasons,
                    detail={'open_change_pct': a.open_change_pct, 'auction_volume': a.auction_volume}
                ))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:20]
```

- [ ] **Step 4: Commit**

```bash
git add ashare_review/screening/breakout.py ashare_review/screening/sector_divergence.py ashare_review/screening/auction.py
git commit -m "feat: breakout pattern, sector divergence, and auction screeners"
```

---

### Task 12: 复盘报告生成

**Files:**
- Create: `ashare_review/report/daily.py`
- Create: `ashare_review/report/weekly.py`

- [ ] **Step 1: 日度复盘**

```python
"""日度复盘报告"""
from datetime import date, datetime
from typing import Dict, List
from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher
from collections import Counter

class DailyReport:
    def __init__(self, tdx: TdxReader = None, ak_fetcher: AkshareFetcher = None):
        self.tdx = tdx or TdxReader()
        self.ak = ak_fetcher or AkshareFetcher()

    def generate(self) -> Dict:
        limit_ups = self.ak.get_limit_up_pool()
        boards = self.ak.get_concept_boards()
        lhb = self.ak.get_lhb()
        spot = self.ak.get_spot_df()
        # 涨停统计
        total_zt = len(limit_ups)
        sealed = sum(1 for lu in limit_ups if lu.is_seal)
        broken = sum(1 for lu in limit_ups if lu.is_broken)
        first_boards = [lu for lu in limit_ups if lu.is_first]
        multi_boards = [lu for lu in limit_ups if lu.consecutive >= 2]
        # 涨停时间分布
        time_dist = {'早盘(<10:30)': 0, '上午(10:30-11:30)': 0, '下午': 0}
        for lu in limit_ups:
            try:
                t = int(str(lu.limit_up_time).replace(':', '')[:4])
                if t <= 1030:
                    time_dist['早盘(<10:30)'] += 1
                elif t <= 1130:
                    time_dist['上午(10:30-11:30)'] += 1
                else:
                    time_dist['下午'] += 1
            except (ValueError, TypeError):
                time_dist['下午'] += 1
        # 连板高度
        max_consecutive = max((lu.consecutive for lu in limit_ups), default=0)
        # 板块涨停数排名
        sector_zt = Counter(lu.board_type for lu in limit_ups if lu.board_type)
        top_sectors = sector_zt.most_common(10)
        # 龙虎榜净买前十
        lhb_sorted = sorted(lhb, key=lambda x: x.net_amount, reverse=True)[:10]
        return {
            'date': date.today().isoformat(),
            'total_limit_ups': total_zt,
            'sealed': sealed,
            'broken': broken,
            'seal_rate': f'{sealed/max(total_zt,1)*100:.1f}%',
            'first_boards': len(first_boards),
            'multi_boards': len(multi_boards),
            'max_consecutive': max_consecutive,
            'time_distribution': time_dist,
            'top_sectors': [(s, c) for s, c in top_sectors],
            'top_lhb': [{
                'code': l.code, 'name': l.name, 'reason': l.reason,
                'net_amount': l.net_amount
            } for l in lhb_sorted],
            'multi_board_list': [{
                'code': lu.code, 'name': lu.name,
                'consecutive': lu.consecutive, 'board_type': lu.board_type,
                'limit_up_time': lu.limit_up_time
            } for lu in sorted(multi_boards, key=lambda x: x.consecutive, reverse=True)]
        }
```

- [ ] **Step 2: 周度复盘（沿用日度+汇总）**

```python
"""周度复盘"""
from ..utils.calendar import TradingCalendar

class WeeklyReport:
    def __init__(self):
        self.cal = TradingCalendar()

    def generate(self, weekly_notes: str = '', daily_reports: list = None) -> dict:
        """基于周度笔记和日度报告汇总"""
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
```

- [ ] **Step 3: Commit**

```bash
git add ashare_review/report/daily.py ashare_review/report/weekly.py
git commit -m "feat: daily and weekly report generators"
```

---

### Task 13: Flask Web 界面

**Files:**
- Create: `ashare_review/web/app.py`
- Create: `ashare_review/web/templates/base.html`
- Create: `ashare_review/web/templates/index.html`
- Create: `ashare_review/web/templates/screening.html`
- Create: `ashare_review/web/templates/review.html`
- Create: `ashare_review/web/templates/stock_detail.html`
- Create: `ashare_review/web/static/style.css`

- [ ] **Step 1: Flask 应用主文件**

```python
"""Flask Web 应用"""
from flask import Flask, render_template, jsonify, request
from ..screening.one_two import OneTwoScreener
from ..screening.institution import InstitutionScreener
from ..screening.leader import LeaderScreener
from ..screening.breakout import BreakoutScreener
from ..screening.sector_divergence import SectorDivergenceScreener
from ..screening.auction import AuctionScreener
from ..report.daily import DailyReport
from ..report.weekly import WeeklyReport
from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher

app = Flask(__name__)
tdx = TdxReader()
ak_fetcher = AkshareFetcher()

SCREENERS = {
    'one_two': OneTwoScreener(tdx, ak_fetcher),
    'institution': InstitutionScreener(tdx, ak_fetcher),
    'leader': LeaderScreener(tdx, ak_fetcher),
    'breakout': BreakoutScreener(tdx, ak_fetcher),
    'sector_divergence': SectorDivergenceScreener(tdx, ak_fetcher),
    'auction': AuctionScreener(tdx, ak_fetcher),
}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/screening')
def screening():
    return render_template('screening.html', screeners=list(SCREENERS.keys()))

@app.route('/api/screen/<strategy>')
def api_screen(strategy):
    if strategy not in SCREENERS:
        return jsonify({'error': 'Unknown strategy'}), 404
    screener = SCREENERS[strategy]
    results = screener.screen()
    return jsonify([{
        'code': r.code, 'name': r.name, 'score': r.score,
        'reasons': r.reasons, 'detail': r.detail
    } for r in results])

@app.route('/api/screen/all')
def api_screen_all():
    all_results = {}
    for name, screener in SCREENERS.items():
        try:
            results = screener.screen()
            all_results[name] = [{
                'code': r.code, 'name': r.name, 'score': r.score,
                'reasons': r.reasons, 'detail': r.detail
            } for r in results[:10]]
        except Exception as e:
            all_results[name] = {'error': str(e)}
    return jsonify(all_results)

@app.route('/review')
def review():
    report = DailyReport(tdx, ak_fetcher).generate()
    return render_template('review.html', report=report)

@app.route('/stock/<code>')
def stock_detail(code):
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith('8') or code.startswith('4'):
        market = 'bj'
    try:
        df = tdx.read_daily(code, market)
        from ..analysis.indicators import enrich_all
        df = enrich_all(df)
        latest = df.iloc[-1].to_dict() if not df.empty else {}
        return render_template('stock_detail.html', code=code, latest=latest)
    except Exception:
        return render_template('stock_detail.html', code=code, error='数据加载失败')

def run(host='127.0.0.1', port=5000, debug=True):
    app.run(host=host, port=port, debug=debug)
```

- [ ] **Step 2: base.html 布局模板**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>A股复盘选股系统</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <nav>
        <a href="/">首页</a>
        <a href="/screening">选股面板</a>
        <a href="/review">复盘报告</a>
    </nav>
    <main>{% block content %}{% endblock %}</main>
</body>
</html>
```

- [ ] **Step 3: index.html**

```html
{% extends "base.html" %}
{% block content %}
<h1>A股复盘+选股系统</h1>
<div class="card-grid">
    <a href="/screening" class="card">
        <h2>选股面板</h2>
        <p>六策略筛选：1进2 / 机构票 / 龙头 / 突破形态 / 板块分歧 / 竞价抢筹</p>
    </a>
    <a href="/review" class="card">
        <h2>复盘报告</h2>
        <p>当日涨停复盘 / 板块强度 / 龙虎榜速览</p>
    </a>
</div>
{% endblock %}
```

- [ ] **Step 4: screening.html**

```html
{% extends "base.html" %}
{% block content %}
<h1>选股面板</h1>
<div class="screener-buttons">
    <button onclick="screen('one_two')">1进2筛选</button>
    <button onclick="screen('institution')">机构票筛选</button>
    <button onclick="screen('leader')">龙头筛选</button>
    <button onclick="screen('breakout')">突破形态</button>
    <button onclick="screen('sector_divergence')">板块分歧介入</button>
    <button onclick="screen('auction')">竞价抢筹</button>
    <button onclick="screenAll()">一键筛选全部</button>
</div>
<div id="results"></div>
<script>
async function screen(strategy) {
    document.getElementById('results').innerHTML = '<p>筛选运行中...</p>';
    const resp = await fetch(`/api/screen/${strategy}`);
    const data = await resp.json();
    renderTable(data);
}
async function screenAll() {
    document.getElementById('results').innerHTML = '<p>筛选运行中...</p>';
    const resp = await fetch('/api/screen/all');
    const allData = await resp.json();
    let html = '';
    for (const [strategy, results] of Object.entries(allData)) {
        if (results.error) {
            html += `<h2>${strategy}</h2><p class="error">${results.error}</p>`;
            continue;
        }
        html += `<h2>${strategy} (${results.length} 个结果)</h2>`;
        html += buildTable(results);
    }
    document.getElementById('results').innerHTML = html;
}
function buildTable(data) {
    if (!data.length) return '<p>无匹配结果</p>';
    let html = '<table><tr><th>代码</th><th>名称</th><th>得分</th><th>理由</th></tr>';
    for (const r of data) {
        html += `<tr><td><a href="/stock/${r.code}">${r.code}</a></td>
            <td>${r.name}</td><td>${r.score}</td><td>${r.reasons.join('; ')}</td></tr>`;
    }
    return html + '</table>';
}
</script>
{% endblock %}
```

- [ ] **Step 5: review.html**

```html
{% extends "base.html" %}
{% block content %}
<h1>复盘报告 — {{ report.date }}</h1>
<div class="stats">
    <div class="stat-card"><strong>{{ report.total_limit_ups }}</strong><br>涨停总数</div>
    <div class="stat-card"><strong>{{ report.sealed }}</strong><br>封死</div>
    <div class="stat-card"><strong>{{ report.broken }}</strong><br>炸板</div>
    <div class="stat-card"><strong>{{ report.seal_rate }}</strong><br>封板率</div>
    <div class="stat-card"><strong>{{ report.max_consecutive }}</strong><br>最高连板</div>
</div>
<h2>时间分布</h2>
<ul>{% for label, count in report.time_distribution.items() %}
    <li>{{ label }}: {{ count }}只</li>{% endfor %}</ul>
<h2>热点板块</h2>
<ol>{% for sector, count in report.top_sectors %}
    <li>{{ sector }}: {{ count }}只涨停</li>{% endfor %}</ol>
<h2>连板梯队</h2>
<table><tr><th>代码</th><th>名称</th><th>连板</th><th>涨停时间</th></tr>
{% for lu in report.multi_board_list %}
<tr><td><a href="/stock/{{ lu.code }}">{{ lu.code }}</a></td>
    <td>{{ lu.name }}</td><td>{{ lu.consecutive }}板</td><td>{{ lu.limit_up_time }}</td></tr>
{% endfor %}</table>
<h2>龙虎榜 Top 10</h2>
<table><tr><th>代码</th><th>名称</th><th>原因</th><th>净买额(万)</th></tr>
{% for l in report.top_lhb %}
<tr><td>{{ l.code }}</td><td>{{ l.name }}</td><td>{{ l.reason }}</td><td>{{ l.net_amount|round(1) }}</td></tr>
{% endfor %}</table>
{% endblock %}
```

- [ ] **Step 6: stock_detail.html + style.css**

```html
{% extends "base.html" %}
{% block content %}
<h1>个股详情 — {{ code }}</h1>
{% if error %}<p class="error">{{ error }}</p>
{% else %}
<table><tr><th>最新价</th><td>{{ latest.get('close', '-') }}</td></tr></table>
{% endif %}
<a href="/screening">← 返回选股</a>
{% endblock %}
```

```css
body { font-family: 'Microsoft YaHei', sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
nav { background: #1a1a2e; padding: 15px; border-radius: 8px; margin-bottom: 20px; }
nav a { color: #eee; margin-right: 20px; text-decoration: none; font-weight: bold; }
nav a:hover { color: #e94560; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
.card { background: white; padding: 30px; border-radius: 8px; text-decoration: none; color: #333; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
.card h2 { margin-top: 0; color: #e94560; }
.stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 15px; margin: 20px 0; }
.stat-card { background: white; padding: 20px; text-align: center; border-radius: 8px; font-size: 1.2em; }
.stat-card strong { font-size: 2em; color: #e94560; display: block; }
.screener-buttons { margin: 20px 0; }
.screener-buttons button { margin: 5px; padding: 10px 20px; border: none; background: #e94560; color: white; border-radius: 5px; cursor: pointer; font-size: 1em; }
.screener-buttons button:hover { background: #c73e54; }
table { width: 100%; background: white; border-collapse: collapse; margin: 15px 0; border-radius: 8px; overflow: hidden; }
th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
th { background: #1a1a2e; color: white; }
.error { color: red; }
```

- [ ] **Step 7: 提交**

```bash
git add ashare_review/web/ ashare_review/templates/ ashare_review/static/
git commit -m "feat: Flask web interface with screening and review pages"
```

---

### Task 14: 启动入口 + 集成测试

**Files:**
- Create: `run.py`

- [ ] **Step 1: run.py**

```python
"""启动入口"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ashare_review.web.app import run

if __name__ == '__main__':
    print("A股复盘选股系统启动中...")
    print("浏览器打开 http://127.0.0.1:5000")
    run(debug=True)
```

- [ ] **Step 2: 启动测试**

```bash
python run.py
```

Expected: 终端输出 "A股复盘选股系统启动中..."，浏览器打开 http://127.0.0.1:5000 可以看到首页

- [ ] **Step 3: 验证数据层 + 选股链**

```bash
python -c "
from ashare_review.data.tdx_reader import TdxReader
from ashare_review.data.akshare_fetcher import AkshareFetcher
from ashare_review.screening.one_two import OneTwoScreener
from ashare_review.report.daily import DailyReport

reader = TdxReader()
print(f'Stock count: {len(reader.list_stocks())}')

ak = AkshareFetcher()
try:
    spot = ak.get_spot_df()
    print(f'Spot rows: {len(spot)}')
except Exception as e:
    print(f'Spot (may need network): {e}')

try:
    report = DailyReport(reader, ak).generate()
    print(f'Report date: {report[\"date\"]}')
except Exception as e:
    print(f'Report error: {e}')

print('Integration OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add run.py
git commit -m "feat: entry point and integration verification"
```



