"""data_sources.sina — 新浪免费期权行情适配器。

链路（均已实测验证，2026-08）：
  1) getStockName          → 到期月份列表 ['2026-09', '2026-10', ...]
  2) OP_UP_{code}{ym} / OP_DOWN_{code}{ym} → 该月全部认购/认沽合约代码（100xxxxx）
  3) CON_OP_{code} 批量     → 五档盘口 + 到期日/剩余天数扩展字段
  4) CON_SO_{code} 批量     → 交易代码 + Greeks(IV/Delta/Gamma/Theta/Vega)
  5) hq.sinajs.cn/list=sh510500 → 标的实时行情（备用，供降级链）

所有请求需带 Referer: https://stock.finance.sina.com.cn/ 与 UA。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

from core.contracts import OptionContract, compute_dte, parse_trading_code
from .base import ETFQuote, ETFQuoteSource, OptionChainResult, OptionSource, SourceRegistry

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/97.0.4692.71 Safari/537.36")
_HDRS = {
    "User-Agent": _UA,
    "Referer": "https://stock.finance.sina.com.cn/",
    "Accept": "*/*",
    "Host": "hq.sinajs.cn",
}
_QUOTE_HDRS = {
    "User-Agent": _UA,
    "Referer": "https://vip.stock.finance.sina.com.cn/",
    "Accept": "*/*",
    "Host": "hq.sinajs.cn",
}
_OPTION_API = "https://stock.finance.sina.com.cn/futures/api/openapi.php/StockOptionService"
_CHUNK = 50  # 新浪单次请求的合约数量上限（保守）

# 当前新浪 CON_OP 实际字段索引（0-based，实测 2026-08，51 字段）
#   0买量 1买价 2最新价 3卖价 4卖量 5持仓量 6涨幅 7行权价 8昨收 9开盘 10涨停 11跌停
#   12-20: 申卖价五..申卖价一(奇数索引=对应量) 22-30: 申买价一..申买价五(奇数索引=对应量)
#   32行情时间 34状态 36标的 37简称 38振幅 39最高 40最低 41成交量 42成交额
#   43合约类型(M/A) 44现货价 45认购/认沽 46到期日 47剩余天数 48手续费 49涨跌 50...(杠杆等)
_CON_OP_NAMES = [
    "买量", "买价", "最新价", "卖价", "卖量", "持仓量", "涨幅", "行权价",
    "昨收价", "开盘价", "涨停价", "跌停价",
]
# 12..21 = 申卖价五/量五 ... 申卖价一/量一
# 22..31 = 申买价一/量一 ... 申买价五/量五

# CON_SO 字段（0-based，14 字段）
_CON_SO_NAMES = [
    "期权合约简称", "成交量", "Delta", "Gamma", "Theta", "Vega", "隐含波动率",
    "最高价", "最低价", "交易代码", "行权价", "现价", "昨收", "合约类型",
]

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _to_float(v: str) -> Optional[float]:
    if v in ("", "-", "0.0000"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: str) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


class SinaOptionSource(OptionSource, ETFQuoteSource):
    """新浪期权 + 标的行情。"""

    name = "sina"

    def __init__(self, timeout: float = 8.0, retries: int = 2):
        self.timeout = timeout
        self.retries = retries
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # 基础请求
    # ------------------------------------------------------------------
    def _get(self, url: str, hdrs: dict) -> str:
        last_err = ""
        for _ in range(self.retries + 1):
            try:
                r = self._session.get(url, headers=hdrs, timeout=self.timeout)
                r.raise_for_status()
                return r.text
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
        raise ConnectionError(f"新浪请求失败 {url}: {last_err}")

    def _get_quotes(self, prefix: str, codes: List[str]) -> Dict[str, List[str]]:
        """批量拉取 CON_OP / CON_SO，返回 {code: [字段...]}。"""
        out: Dict[str, List[str]] = {}
        for i in range(0, len(codes), _CHUNK):
            chunk = codes[i:i + _CHUNK]
            url = "https://hq.sinajs.cn/list=" + ",".join(f"{prefix}_{c}" for c in chunk)
            text = self._get(url, _HDRS)
            # 每行 var hq_str_CON_OP_10012280="...";  —— 用正则按代码分块
            for code in chunk:
                m = re.search(rf'{prefix}_{re.escape(code)}="([^"]*)"', text)
                if m and m.group(1):
                    out[code] = m.group(1).split(",")
        return out

    # ------------------------------------------------------------------
    # 到期月份
    # ------------------------------------------------------------------
    def fetch_expire_months(self, ucfg) -> List[str]:
        url = f"{_OPTION_API}.getStockName"
        r = self._session.get(
            url, params={"exchange": "null", "cate": ucfg.code},
            timeout=self.timeout,
            headers={"User-Agent": _UA, "Referer": "https://stock.finance.sina.com.cn/"},
        )
        r.raise_for_status()
        months = r.json().get("result", {}).get("data", {}).get("contractMonth") or []
        out = []
        for m in months:
            # getStockName 返回 ['2026-09','2026-10',...] → 需要 2609 / 2610
            joined = "".join(str(m).split("-"))
            if len(joined) == 6:            # '202609' → '2609'
                joined = joined[2:]
            if joined not in out:
                out.append(joined)
        return out

    # ------------------------------------------------------------------
    # 合约代码
    # ------------------------------------------------------------------
    def fetch_contract_codes(self, ucfg, ym: str) -> Tuple[List[str], List[str]]:
        """返回 (认购代码, 认沽代码)。"""
        calls: List[str] = []
        puts: List[str] = []
        for kind, target in (("UP", calls), ("DOWN", puts)):
            url = f"https://hq.sinajs.cn/list=OP_{kind}_{ucfg.code}{ym}"
            text = self._get(url, _HDRS)
            codes = [x[7:] for x in text.replace('"', ",").split(",") if x.startswith("CON_OP_")]
            # 去重保序
            seen = set()
            for c in codes:
                if c not in seen:
                    seen.add(c)
                    target.append(c)
        return calls, puts

    # ------------------------------------------------------------------
    # 期权链完整抓取
    # ------------------------------------------------------------------
    def fetch_chain(self, ucfg, registry: Optional[SourceRegistry] = None) -> OptionChainResult:
        try:
            return self._fetch_chain(ucfg, registry)
        except Exception as e:  # noqa: BLE001
            if registry:
                registry.mark_fail(self.name, str(e))
            return OptionChainResult(underlying=ucfg.code, source=self.name, error=str(e))

    def _fetch_chain(self, ucfg, registry) -> OptionChainResult:
        res = OptionChainResult(underlying=ucfg.code, source=self.name)
        months = self.fetch_expire_months(ucfg)
        res.expire_months = months
        if not months:
            raise ConnectionError(f"{ucfg.code} 无到期月份")

        # 选第一个在未来 15~45 天内到期的月份（按当月第4个周三估算）
        from core.contracts import dte_to_expire_date
        target_ym = None
        for ym in months:
            exp = dte_to_expire_date(ym)
            if exp is None:
                continue
            dte = compute_dte(exp.isoformat())
            if dte is not None and 0 <= dte <= 90:
                target_ym = ym
                break
        if target_ym is None:
            target_ym = months[0]

        calls, puts = self.fetch_contract_codes(ucfg, target_ym)
        all_codes = calls + puts
        if not all_codes:
            raise ConnectionError(f"{ucfg.code} {target_ym} 无合约代码")

        # 盘口
        quotes = self._get_quotes("CON_OP", all_codes)
        # Greeks + 交易代码
        so = self._get_quotes("CON_SO", all_codes)

        contracts: List[OptionContract] = []
        for code in all_codes:
            fields = quotes.get(code)
            if not fields:
                continue
            c = self._build_contract(ucfg, code, fields, so.get(code, []))
            if c is not None:
                contracts.append(c)

        res.contracts = contracts
        if registry:
            registry.mark_ok(self.name)
        return res

    # ------------------------------------------------------------------
    # 单合约解析
    # ------------------------------------------------------------------
    def _build_contract(self, ucfg, code: str, fields: List[str], so_fields: List[str]) -> Optional[OptionContract]:
        """按当前新浪 CON_OP 实际索引（0-based）解析。"""
        if len(fields) < 44:
            return None

        def num(i: int) -> float:
            if i < len(fields) and fields[i] not in ("", "-"):
                try:
                    return float(fields[i])
                except ValueError:
                    return 0.0
            return 0.0

        def val(i: int) -> str:
            return fields[i] if i < len(fields) else ""

        def ivol(i: int) -> int:
            return int(num(i))

        strike = num(7)
        # 盘口五档：卖1价=20 卖1量=21 买1价=22 买1量=23（卖5价=12..卖2价=18 递增）
        # 结构：12卖5价 13卖5量 14卖4价 15卖4量 16卖3价 17卖3量 18卖2价 19卖2量 20卖1价 21卖1量
        #       22买1价 23买1量 24买2价 25买2量 26买3价 27买3量 28买4价 29买4量 30买5价 31买5量
        asks = [num(20), num(18), num(16), num(14), num(12)]
        asks_vol = [ivol(21), ivol(19), ivol(17), ivol(15), ivol(13)]
        bids = [num(22), num(24), num(26), num(28), num(30)]
        bids_vol = [ivol(23), ivol(25), ivol(27), ivol(29), ivol(31)]

        # 交易代码与 Greeks（CON_SO 实测 17 字段，2026-08）
        #   0简称 1-3空 4成交量 5Delta 6Gamma 7Theta 8Vega 9IV 10最高 11最低
        #   12交易代码 13行权价 14现价 15昨收 16合约类型(M/A)
        trading_code = ""
        greeks = {}
        if len(so_fields) >= 14:
            trading_code = so_fields[12] if so_fields[12] not in ("", "-") else ""
            greeks = {
                "Delta": _to_float(so_fields[5]),
                "Gamma": _to_float(so_fields[6]),
                "Theta": _to_float(so_fields[7]),
                "Vega": _to_float(so_fields[8]),
                "IV": _to_float(so_fields[9]),
            }

        # 合约类型：优先 CON_SO index16（M/A），其次 CON_OP index43，其次交易代码
        type_code = ""
        cp_raw = val(45)
        if len(so_fields) > 16 and so_fields[16] in ("M", "A"):
            type_code = so_fields[16]
        if not type_code:
            type_code = val(43)
        is_standard = type_code == "M"
        cp = "C" if cp_raw == "C" else ("P" if cp_raw == "P" else "")
        # 兼容旧格式：从交易代码回退
        if not cp or not is_standard:
            parsed = parse_trading_code(trading_code) if trading_code else None
            if parsed:
                if not cp:
                    cp = parsed["cp"]
                if parsed["type"] == "M":
                    is_standard = True
                if strike == 0:
                    strike = parsed["strike"]

        # 到期日 / 剩余天数：CON_OP 索引 46 / 47（实测返回）
        expire_date = val(46).strip()
        dte = None
        if expire_date and _DATE_RE.match(expire_date):
            dte = compute_dte(expire_date)
        elif num(47) > 0:
            dte = int(num(47))

        name = val(37) or (so_fields[0] if so_fields and so_fields[0] not in ("", "-") else "")
        c = OptionContract(
            option_code=code,
            trading_code=trading_code,
            underlying=ucfg.code,
            underlying_name=ucfg.name,
            cp=cp,
            strike=strike,
            expire_date=expire_date,
            dte=dte,
            bid1=num(1),
            bid1_vol=ivol(0),
            ask1=num(3),
            ask1_vol=ivol(4),
            bids=bids,
            asks=asks,
            bid_vols=bids_vol,
            ask_vols=asks_vol,
            last=num(2),
            prev_close=num(8),
            open=num(9),
            high=num(39),
            low=num(40),
            volume=ivol(41),
            oi=ivol(5),
            amount=num(42),
            delta=greeks.get("Delta"),
            gamma=greeks.get("Gamma"),
            theta=greeks.get("Theta"),
            vega=greeks.get("Vega"),
            iv=greeks.get("IV"),
            quote_time=val(32),
            source=self.name,
            is_standard=is_standard,
            raw={"名称": name, "行情时间": val(32)},
        )
        return c

    # ------------------------------------------------------------------
    # 标的实时行情（备用源）
    # ------------------------------------------------------------------
    def fetch(self, ucfg, registry: Optional[SourceRegistry] = None) -> ETFQuote:
        prefix = ucfg.option_spot_prefix or f"sh{ucfg.code}"
        url = f"https://hq.sinajs.cn/list={prefix}"
        text = self._get(url, _QUOTE_HDRS)
        m = re.search(r'"(.*)"', text, re.S)
        if not m:
            raise ConnectionError(f"新浪 {prefix} 返回异常")
        parts = m.group(1).split(",")
        if len(parts) < 32:
            raise ConnectionError(f"新浪 {prefix} 字段不足: {len(parts)}")
        try:
            price = float(parts[3])
        except ValueError:
            price = 0.0
        if price <= 0:
            raise ConnectionError(f"新浪 {prefix} 最新价为空")
        q = ETFQuote(
            code=ucfg.code,
            name=parts[0],
            price=price,
            bid1=float(parts[6]) if parts[6] else price,
            ask1=float(parts[7]) if parts[7] else price,
            prev_close=float(parts[2]) if parts[2] else 0.0,
            open=float(parts[1]) if parts[1] else 0.0,
            high=float(parts[4]) if parts[4] else 0.0,
            low=float(parts[5]) if parts[5] else 0.0,
            volume=int(float(parts[8])) if parts[8] else 0,
            amount=float(parts[9]) if parts[9] else 0.0,
            quote_time=f"{parts[30]} {parts[31]}",
            fetch_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            source=self.name,
        )
        if registry:
            registry.mark_ok(self.name)
        return q
