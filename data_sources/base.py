"""data_sources.base — 数据源抽象、统一数据模型与状态管理。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from core.contracts import OptionContract


class SourceHealth(str, Enum):
    OK = "正常"
    DEGRADED = "降级"
    DOWN = "断开"


@dataclass
class SourceStatus:
    """单个数据源的健康状态，用于看板显示。"""

    name: str = ""
    health: SourceHealth = SourceHealth.DOWN
    last_ok_ts: Optional[float] = None
    error: str = ""
    latency_ms: Optional[float] = None
    enabled: bool = True

    @property
    def last_ok_str(self) -> str:
        if not self.last_ok_ts:
            return "-"
        t = time.localtime(self.last_ok_ts)
        return time.strftime("%H:%M:%S", t)

    def ok(self) -> None:
        self.health = SourceHealth.OK
        self.last_ok_ts = time.time()
        self.error = ""
        self.latency_ms = int((time.time() - self.last_ok_ts) * 1000) if self.last_ok_ts else None

    def degrade(self, err: str) -> None:
        self.health = SourceHealth.DEGRADED
        self.error = str(err)[:200]

    def down(self, err: str) -> None:
        self.health = SourceHealth.DOWN
        self.error = str(err)[:200]


@dataclass
class ETFQuote:
    """ETF 标的实时行情（统一字段）。"""

    code: str = ""
    name: str = ""
    price: float = 0.0          # 最新价
    bid1: float = 0.0
    ask1: float = 0.0
    prev_close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: int = 0
    amount: float = 0.0
    quote_time: str = ""        # 行情时间
    fetch_time: str = ""        # 拉取时间
    source: str = ""

    @property
    def valid(self) -> bool:
        return self.price > 0


@dataclass
class OptionChainResult:
    """一次期权链抓取的完整结果。"""

    underlying: str = ""
    contracts: List[OptionContract] = field(default_factory=list)
    expire_months: List[str] = field(default_factory=list)
    source: str = ""
    fetch_time: str = ""
    error: str = ""


class SourceRegistry:
    """数据源状态注册表（看板显示 + 断线恢复判断）。"""

    def __init__(self, enabled: Optional[Dict[str, bool]] = None):
        enabled = enabled or {}
        self._sources: Dict[str, SourceStatus] = {}
        for name in ("ths", "sina", "eastmoney", "akshare", "mock", "qmt"):
            self._sources[name] = SourceStatus(name=name, enabled=enabled.get(name, True))

    def status(self, name: str) -> SourceStatus:
        return self._sources.get(name, SourceStatus(name=name))

    def mark_ok(self, name: str) -> SourceStatus:
        st = self.status(name)
        st.ok()
        return st

    def mark_fail(self, name: str, err: str) -> SourceStatus:
        st = self.status(name)
        st.down(str(err))
        return st

    def mark_degraded(self, name: str, err: str) -> SourceStatus:
        st = self.status(name)
        st.degrade(str(err))
        return st

    def all_status(self) -> List[SourceStatus]:
        return list(self._sources.values())

    def summary(self) -> str:
        parts = []
        for st in self.all_status():
            if not st.enabled:
                continue
            parts.append(f"{st.name}={st.health.value}")
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# 数据源基类
# ---------------------------------------------------------------------------
class ETFQuoteSource:
    """ETF 标的价格源接口。"""

    name = "base"

    def fetch(self, ucfg) -> ETFQuote:
        raise NotImplementedError


class OptionSource:
    """期权链源接口。"""

    name = "base"

    def fetch_chain(self, ucfg, registry: SourceRegistry) -> OptionChainResult:
        raise NotImplementedError
