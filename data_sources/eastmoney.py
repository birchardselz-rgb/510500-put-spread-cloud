"""data_sources.eastmoney — 东方财富备用行情源（ETF 标的价格）。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import requests

from .base import ETFQuote, ETFQuoteSource, SourceRegistry

_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170,f171"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


class EastMoneyQuoteSource(ETFQuoteSource):
    """东财 ETF 标的价格源（备用）。价格字段按 1000 缩放。"""

    name = "eastmoney"

    def __init__(self, timeout: float = 5.0, retries: int = 1):
        self.timeout = timeout
        self.retries = retries
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _UA})

    def fetch(self, ucfg, registry: Optional[SourceRegistry] = None) -> ETFQuote:
        last_err = ""
        for _ in range(self.retries + 1):
            try:
                r = self._session.get(
                    _URL,
                    params={"secid": ucfg.em_secid, "fields": _FIELDS, "ut": "fa5fd1943c7b386f172d6893dbfba10b"},
                    timeout=self.timeout,
                )
                r.raise_for_status()
                data = (r.json() or {}).get("data") or {}
                price = _scale(data.get("f43"))
                if not price:
                    raise ValueError(f"东财 {ucfg.code} 最新价为空")
                q = ETFQuote(
                    code=str(data.get("f57", ucfg.code)),
                    name=str(data.get("f58", ucfg.name)),
                    price=price,
                    bid1=price,
                    ask1=price,
                    prev_close=_scale(data.get("f60")) or 0.0,
                    open=_scale(data.get("f46")) or 0.0,
                    high=_scale(data.get("f44")) or 0.0,
                    low=_scale(data.get("f45")) or 0.0,
                    volume=int(data.get("f47") or 0) * 100,   # 手 → 份
                    amount=float(data.get("f48") or 0),
                    quote_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    fetch_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    source=self.name,
                )
                if registry:
                    registry.mark_ok(self.name)
                return q
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                if registry:
                    registry.mark_fail(self.name, last_err)
        raise ConnectionError(f"东财行情失败: {last_err}")


def _scale(v) -> Optional[float]:
    """东财价格字段通常 ×1000。"""
    if v is None:
        return None
    try:
        return float(v) / 1000.0
    except (TypeError, ValueError):
        return None
