"""data_sources.ths — 同花顺实时行情适配器（用户要求：有同花顺数据优先同花顺）。

接口：https://d.10jqka.com.cn/v6/realhead/hs_510500/last.js （JSONP）
需带 Referer: http://stockpage.10jqka.com.cn/{code}/

字段码（items 字典）：
  10=最新价  24=买一价  25=买一量  30=卖一价  31=卖一量
  8=最高  9=最低  13=成交量  19=成交额  7=昨收  6=今开
  updateTime=最后行情时间  name=证券名称  stockStatus=交易状态
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

import requests

from .base import ETFQuote, ETFQuoteSource, SourceRegistry

# 经实测必须使用 https，http 返回 502
_URL_TPL = "https://d.10jqka.com.cn/v6/realhead/{hex}/last.js"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _strip_jsonp(text: str) -> dict:
    """从 JSONP 包裹中提取 JSON 对象（含外层 updateTime 等字段）。"""
    # JSONP 形如 callback({...}) 或 callback({...});
    m = re.search(r"\(\s*(\{.*\})\s*\)\s*;?\s*$", text, re.S)
    if not m:
        raise ValueError("同花顺返回格式异常")
    # 用 json.JSONDecoder 精确匹配到配平闭合括号的 JSON 对象
    try:
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(m.group(1))
        return obj
    except json.JSONDecodeError:
        raise ValueError("同花顺 JSON 解析失败")


class THSQuoteSource(ETFQuoteSource):
    """同花顺 ETF 标的价格源。"""

    name = "ths"

    def __init__(self, timeout: float = 5.0, retries: int = 2):
        self.timeout = timeout
        self.retries = retries
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _UA, "Accept": "*/*", "Referer": ""})

    def fetch(self, ucfg, registry: Optional[SourceRegistry] = None) -> ETFQuote:
        hex_name = ucfg.ths_hex or f"hs_{ucfg.code}"
        url = _URL_TPL.format(hex=hex_name)
        referer = f"http://stockpage.10jqka.com.cn/{ucfg.code}/"
        last_err = ""
        for attempt in range(self.retries + 1):
            try:
                r = self._session.get(
                    url, timeout=self.timeout,
                    headers={"Referer": referer, "User-Agent": _UA},
                )
                r.raise_for_status()
                payload = _strip_jsonp(r.text)
                items = payload.get("items") or {}
                price = _num(items.get("10"))
                if price is None or price <= 0:
                    raise ValueError(f"同花顺 {ucfg.code} 最新价为空")
                # 注意：name/updateTime/stockStatus 都位于 items 内部
                q = ETFQuote(
                    code=ucfg.code,
                    name=items.get("name") or ucfg.name,
                    price=price,
                    bid1=_num(items.get("24")) or price,
                    ask1=_num(items.get("30")) or price,
                    prev_close=_num(items.get("7")) or 0.0,
                    open=_num(items.get("6")) or 0.0,
                    high=_num(items.get("8")) or 0.0,
                    low=_num(items.get("9")) or 0.0,
                    volume=_num(items.get("13")) or 0,
                    amount=_num(items.get("19")) or 0.0,
                    quote_time=items.get("updateTime", ""),
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
        raise ConnectionError(f"同花顺行情失败: {last_err}")


def _num(v) -> Optional[float]:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
