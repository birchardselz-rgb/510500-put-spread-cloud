"""core.contracts — 期权合约识别与过滤。

职责：
- 解析新浪期权合约（标准 M 合约 / A 类调整合约）
- 识别到期日、剩余天数（DTE），按 dte_min ~ dte_max 过滤
- 从交易代码解析 标的 / 认购认沽 / 到期年月 / 行权价 / 合约类型
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

# 新浪 CON_SO 交易代码，如 510500P2609M06250 / 510500C2609A07000
# 格式: <标的6位><C|P><年2位><月2位><M|A><行权价*1000>
TRADING_CODE_RE = re.compile(
    r"^(?P<underlying>\d{6})"
    r"(?P<cp>[CP])"
    r"(?P<ym>\d{4})"
    r"(?P<type>[MA])"
    r"(?P<strike>\d+)$"
)


@dataclass
class OptionContract:
    """单个期权合约的实时快照（数据模型，跨数据源统一）。"""

    option_code: str              # 新浪 CON_OP 数字代码，如 10012280
    trading_code: str = ""        # 交易代码，如 510500P2609M06250
    underlying: str = ""          # 标的代码，如 510500
    underlying_name: str = ""
    cp: str = ""                  # 'C' 认购 / 'P' 认沽
    strike: float = 0.0
    expire_date: Optional[str] = None   # YYYY-MM-DD
    dte: Optional[int] = None           # 剩余天数

    # 盘口（元）
    bid1: float = 0.0
    bid1_vol: int = 0
    ask1: float = 0.0
    ask1_vol: int = 0
    bids: List[float] = field(default_factory=list)   # 买五档价格
    asks: List[float] = field(default_factory=list)   # 卖五档价格
    bid_vols: List[int] = field(default_factory=list)
    ask_vols: List[int] = field(default_factory=list)

    last: float = 0.0
    prev_close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: int = 0
    oi: int = 0                   # 持仓量
    amount: float = 0.0

    # Greeks（免费源可获得时填充）
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    iv: Optional[float] = None

    quote_time: Optional[str] = None   # 行情时间
    source: str = ""
    is_standard: bool = True
    raw: Dict = field(default_factory=dict)

    # ---------------- 属性 ----------------
    @property
    def spread_width(self) -> float:
        """单腿 Bid/Ask 宽度（滑点）。"""
        return max(0.0, self.ask1 - self.bid1)

    @property
    def mid(self) -> float:
        if self.bid1 > 0 and self.ask1 > 0:
            return (self.bid1 + self.ask1) / 2.0
        return self.last

    @property
    def tradable(self) -> bool:
        """判断盘口是否可用于可成交净价计算。"""
        return self.bid1 > 0 and self.ask1 > 0 and self.ask1 >= self.bid1

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Opt {self.trading_code or self.option_code} "
            f"K={self.strike} {self.cp} B={self.bid1} A={self.ask1} dte={self.dte}>"
        )


# ---------------------------------------------------------------------------
# 交易代码解析
# ---------------------------------------------------------------------------
def parse_trading_code(code: str) -> Optional[Dict]:
    """解析新浪期权交易代码。

    返回 dict（underlying/cp/ym/type/strike）或 None（无法解析）。
    标准 M 合约 type='M'；除息后的 A 类调整合约 type='A'。
    """
    if not code:
        return None
    m = TRADING_CODE_RE.match(code.strip())
    if not m:
        return None
    g = m.groupdict()
    try:
        strike = float(g["strike"]) / 1000.0
    except ValueError:
        return None
    return {
        "underlying": g["underlying"],
        "cp": g["cp"],
        "ym": g["ym"],            # 如 2609
        "type": g["type"],        # M / A
        "strike": strike,
    }


def is_standard_contract(contract: OptionContract) -> bool:
    """标准 M 合约判定：交易代码含 M，且非 A 类调整合约。"""
    if contract.trading_code:
        return parse_trading_code(contract.trading_code) is not None and "M" in contract.trading_code.upper()
    # 无交易代码时按简称判断：调整合约简称带 'A'（如 500ETF沽9月A6250）
    name = contract.raw.get("名称") or ""
    return "A" not in name


# ---------------------------------------------------------------------------
# 到期日 / 剩余天数
# ---------------------------------------------------------------------------
def dte_to_expire_date(ym: str, today: Optional[date] = None) -> Optional[date]:
    """由交易代码的到期年月推算当月第 4 个周三（上交所期权到期日）。

    注意：真正精确的到期日以交易所公告/数据源为准；此处仅作缺失时的近似。
    ym 格式为 YYMM，如 '2609' = 2026年9月。
    """
    today = today or date.today()
    ym = str(ym).strip()
    if len(ym) < 4:
        return None
    try:
        year = 2000 + int(ym[:2])
        month = int(ym[2:4])
    except (ValueError, IndexError):
        return None
    if not (1 <= month <= 12):
        return None
    # 当月所有周三，取第 4 个
    import calendar
    wednesdays = [
        day
        for day in range(1, calendar.monthrange(year, month)[1] + 1)
        if date(year, month, day).weekday() == 2  # Wednesday
    ]
    if len(wednesdays) < 4:
        return None
    return date(year, month, wednesdays[3])


def compute_dte(expire_date: Optional[str], today: Optional[date] = None) -> Optional[int]:
    """计算剩余自然日（DTE）。到期日 YYYY-MM-DD。"""
    if not expire_date:
        return None
    try:
        exp = datetime.strptime(expire_date, "%Y-%m-%d").date()
    except ValueError:
        try:
            exp = datetime.strptime(expire_date, "%Y%m%d").date()
        except ValueError:
            return None
    today = today or date.today()
    return (exp - today).days


def in_dte_window(dte: Optional[int], dte_min: int, dte_max: int) -> bool:
    """DTE 是否在 [dte_min, dte_max] 窗口内。dte 缺失时视为不在窗口。"""
    if dte is None:
        return False
    return dte_min <= dte <= dte_max


def filter_contracts(
    contracts: List[OptionContract],
    dte_min: int,
    dte_max: int,
    today: Optional[date] = None,
    require_standard: bool = True,
) -> List[OptionContract]:
    """过滤：仅保留标准 M 合约 + DTE 在窗口内 + 盘口有效。"""
    out = []
    for c in contracts:
        if require_standard and not is_standard_contract(c):
            continue
        # DTE 优先用盘口扩展字段，缺失则按到期日推算
        dte = c.dte
        if dte is None and c.expire_date:
            dte = compute_dte(c.expire_date, today)
        c.dte = dte
        if not in_dte_window(dte, dte_min, dte_max):
            continue
        out.append(c)
    return out


def group_by_strike(contracts: List[OptionContract]) -> Dict[float, List[OptionContract]]:
    """按行权价分组（含认购与认沽）。"""
    g: Dict[float, List[OptionContract]] = {}
    for c in contracts:
        g.setdefault(c.strike, []).append(c)
    return g
