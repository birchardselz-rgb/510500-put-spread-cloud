"""data_sources.manager — 数据源管理器（同花顺优先 + 逐级降级）。

流程：
1) ETF 标的价格：按 config.data_sources.priority 依次尝试 ths → sina → eastmoney。
2) 期权链：按 config.data_sources.option_priority（默认 [sina]）尝试。
3) 降级链全部失败时，若 mock 启用且处于无网络环境，可切换 mock。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from core.config import Config
from .base import ETFQuote, OptionChainResult, OptionSource, SourceRegistry
from .eastmoney import EastMoneyQuoteSource
from .mock import MockOptionSource
from .sina import SinaOptionSource
from .ths import THSQuoteSource


class DataSourceManager:
    def __init__(self, cfg: Config, registry: Optional[SourceRegistry] = None):
        self.cfg = cfg
        self.registry = registry or SourceRegistry()
        ds = cfg.data_sources

        ths_cfg = ds.get("ths", {})
        sina_cfg = ds.get("sina", {})
        em_cfg = ds.get("eastmoney", {})

        self._ths = THSQuoteSource(timeout=float(ths_cfg.get("timeout", 5)),
                                   retries=int(ths_cfg.get("retries", 2)))
        self._sina = SinaOptionSource(timeout=float(sina_cfg.get("timeout", 8)),
                                      retries=int(sina_cfg.get("retries", 2)))
        self._em = EastMoneyQuoteSource(timeout=float(em_cfg.get("timeout", 5)),
                                        retries=int(em_cfg.get("retries", 1)))
        self._mock = MockOptionSource()

        self._quote_sources = {
            "ths": self._ths,
            "sina": self._sina,
            "eastmoney": self._em,
            "mock": self._mock,
        }
        self._option_sources = {
            "sina": self._sina,
            "mock": self._mock,
        }

        self._mock_override = False

    # ------------------------------------------------------------------
    # ETF 标的价格（同花顺优先）
    # ------------------------------------------------------------------
    def fetch_spot(self, ucfg, allow_mock: bool = True) -> ETFQuote:
        order = self.cfg.data_priority
        if self._mock_override:
            order = ["mock"] + [o for o in order if o != "mock"]
        errors: List[str] = []
        for name in order:
            src = self._quote_sources.get(name)
            if src is None:
                continue
            if name == "mock" and not allow_mock:
                continue
            try:
                return src.fetch(ucfg, self.registry)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
        # 全部失败 → 尝试 mock（无网络演示）
        if allow_mock:
            try:
                self._mock_override = True
                return self._mock.fetch(ucfg, self.registry)
            except Exception as e:  # noqa: BLE001
                errors.append(f"mock: {e}")
        raise ConnectionError("所有 ETF 行情源均失败: " + "; ".join(errors))

    # ------------------------------------------------------------------
    # 期权链（默认新浪）
    # ------------------------------------------------------------------
    def fetch_option_chain(self, ucfg, allow_mock: bool = True) -> OptionChainResult:
        order = self.cfg.option_priority
        if self._mock_override:
            order = ["mock"] + [o for o in order if o != "mock"]
        errors: List[str] = []
        for name in order:
            src = self._option_sources.get(name)
            if src is None:
                continue
            if name == "mock" and not allow_mock:
                continue
            res = src.fetch_chain(ucfg, self.registry)
            if res.contracts and not res.error:
                return res
            if res.error:
                errors.append(f"{name}: {res.error}")
            elif not res.contracts:
                errors.append(f"{name}: 合约列表为空")
        if allow_mock:
            try:
                self._mock_override = True
                return self._mock.fetch_chain(ucfg, self.registry)
            except Exception as e:  # noqa: BLE001
                errors.append(f"mock: {e}")
        raise ConnectionError("所有期权源均失败: " + "; ".join(errors))

    # ------------------------------------------------------------------
    # 便捷：一次拉取某标的的 spot + 期权链
    # ------------------------------------------------------------------
    def fetch_underlying(self, code: str, allow_mock: bool = True) -> Tuple[ETFQuote, OptionChainResult]:
        ucfg = self.cfg.underlying(code)
        spot = self.fetch_spot(ucfg, allow_mock=allow_mock)
        chain = self.fetch_option_chain(ucfg, allow_mock=allow_mock)
        return spot, chain

    def status_summary(self) -> str:
        return self.registry.summary()
