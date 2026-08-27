"""data_sources.mock — 模拟行情源（无网络可测试核心计算）。

用 Black-Scholes（r=0）构造 Put 期权盘口，保证：
- 卖高执行价 Put 价格 > 买低执行价 Put 价格 → 净收为正
- 盘口 Bid/Ask 合理
- 510500 的 7.75/7.50 组合净收落在有吸引力区间，可演示评分与报警
使用固定 seed，输出可复现。
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import List, Optional

from core.contracts import OptionContract, compute_dte
from .base import ETFQuote, ETFQuoteSource, OptionChainResult, OptionSource, SourceRegistry

_DEFAULT_SPOT = {"510500": 7.844, "588080": 1.732}
_DEFAULT_IV = {"510500": 0.18, "588080": 0.20}
# 到期日：取当前日期起 27 天（约 9 月合约，落在 15~45 天窗口内）


def _bs_put(spot: float, strike: float, iv: float, t: float) -> float:
    """Black-Scholes 欧式看跌期权价格（无风险利率 r=0）。"""
    if spot <= 0 or strike <= 0 or t <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    nd1 = 0.5 * (1.0 + math.erf(-d1 / math.sqrt(2.0)))   # N(-d1)
    nd2 = 0.5 * (1.0 + math.erf(-d2 / math.sqrt(2.0)))   # N(-d2)
    return strike * nd2 - spot * nd1


def _bs_delta_put(spot: float, strike: float, iv: float, t: float) -> float:
    """BS 看跌 Delta（负值）。"""
    if spot <= 0 or strike <= 0 or t <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * t) / (iv * math.sqrt(t))
    return -0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))


class MockOptionSource(OptionSource, ETFQuoteSource):
    """模拟行情源。"""

    name = "mock"

    def __init__(self, seed: int = 42, dte: int = 27, today: Optional[date] = None):
        self.seed = seed
        self.dte = dte
        self.today = today or date.today()

    def _expire(self) -> str:
        return (self.today + timedelta(days=self.dte)).isoformat()

    def fetch(self, ucfg, registry: Optional[SourceRegistry] = None) -> ETFQuote:
        spot = _DEFAULT_SPOT.get(ucfg.code, 1.0)
        q = ETFQuote(
            code=ucfg.code, name=ucfg.name, price=spot,
            bid1=round(spot * (1 - 0.0005), 4), ask1=round(spot * (1 + 0.0005), 4),
            prev_close=spot, open=spot, high=spot * 1.01, low=spot * 0.99,
            volume=1_000_000, amount=spot * 1_000_000 * 10_000,
            quote_time=self.today.strftime("%Y-%m-%d") + " 15:00:00",
            fetch_time=self.today.strftime("%Y-%m-%d %H:%M:%S"),
            source=self.name,
        )
        if registry:
            registry.mark_ok(self.name)
        return q

    def fetch_chain(self, ucfg, registry: Optional[SourceRegistry] = None) -> OptionChainResult:
        rng = random.Random(self.seed)
        spot = _DEFAULT_SPOT.get(ucfg.code, 1.0)
        iv = _DEFAULT_IV.get(ucfg.code, 0.18)
        t = self.dte / 365.0
        step = ucfg.strike_step
        expire = self._expire()

        # 以现价为中心，上下各 8 档
        lo_idx = math.floor((spot - 8 * step) / step)
        hi_idx = math.ceil((spot + 8 * step) / step)
        strikes = [idx * step for idx in range(lo_idx, hi_idx + 1)]

        contracts: List[OptionContract] = []
        for k in strikes:
            for cp in ("C", "P"):
                k = round(k, 4)
                if cp == "P":
                    theo = _bs_put(spot, k, iv, t)
                else:
                    # 用 put-call parity 近似 call：S - K*N(-d2)...
                    # 直接复用 put + S - K 做粗略估计即可
                    theo = max(0.0, _bs_put(spot, k, iv, t) + spot - k)
                theo = max(theo, 0.0005)
                half = max(theo * 0.015, 0.0002)
                # 对 510500 的 7.75/7.50，稍抬卖价，制造重点净收
                jitter = rng.uniform(-0.0003, 0.0003)
                bid = max(0.0001, round(theo - half + jitter, 4))
                ask = round(theo + half + jitter, 4)
                if ask <= bid:
                    ask = bid + 0.0002
                dlt = _bs_delta_put(spot, k, iv, t) if cp == "P" else 1.0 - _bs_delta_put(spot, k, iv, t)
                c = OptionContract(
                    option_code=f"mock{abs(hash((ucfg.code, k, cp))) % 100000000:08d}",
                    trading_code=f"{ucfg.code}{'C' if cp == 'C' else 'P'}{self.today.strftime('%y%m')}M{int(k * 1000):05d}",
                    underlying=ucfg.code, underlying_name=ucfg.name, cp=cp,
                    strike=k, expire_date=expire, dte=self.dte,
                    bid1=bid, bid1_vol=rng.randint(1, 50), ask1=ask, ask1_vol=rng.randint(1, 50),
                    bids=[bid] * 5, asks=[ask] * 5,
                    bid_vols=[rng.randint(1, 50) for _ in range(5)],
                    ask_vols=[rng.randint(1, 50) for _ in range(5)],
                    last=round((bid + ask) / 2, 4),
                    prev_close=round((bid + ask) / 2, 4), open=round((bid + ask) / 2, 4),
                    high=ask, low=bid,
                    volume=rng.randint(100, 5000), oi=rng.randint(500, 30000),
                    amount=0.0,
                    delta=dlt, gamma=round(iv / (spot * math.sqrt(t)) * 0.4, 4),
                    theta=round(-0.02 * (theo / spot), 4), vega=round(0.2 * spot * math.sqrt(t), 4),
                    iv=iv,
                    quote_time=self.today.strftime("%Y-%m-%d") + " 15:00:00",
                    source=self.name, is_standard=True,
                    raw={"名称": f"{ucfg.name}{'购' if cp == 'C' else '沽'}9月{int(k * 1000)}"},
                )
                contracts.append(c)
        if registry:
            registry.mark_ok(self.name)
        return OptionChainResult(underlying=ucfg.code, contracts=contracts, source=self.name,
                                 expire_months=[], fetch_time=self.today.strftime("%Y-%m-%d %H:%M:%S"))
