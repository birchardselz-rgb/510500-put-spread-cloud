"""cloud_scanner.py — 云端扫描编排（无本地DB，行情直接调接口）。

复用原项目的 data_sources（同花顺优先 + 新浪期权盘口 + 东财备用），
去掉 SQLite 依赖：扫描结果存入内存字典，供 Streamlit 看板展示。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

from core.config import load_config, UnderlyingCfg
from core.contracts import filter_contracts, is_standard_contract
from core.scoring import apply_score, rank_spreads
from core.spreads import fill_account_risk_from_capital, generate_spreads, suggested_contract_count
from data_sources.manager import DataSourceManager

logger = logging.getLogger("cloud_scanner")


class CloudScanner:
    """云端扫描器：不写本地 DB，结果存内存，供看板读取。"""

    def __init__(self, config_path: Optional[str] = None):
        # 云端 config 由环境变量可覆盖（如数据源开关）
        self.cfg = load_config(config_path) if config_path else load_config()
        self.manager = DataSourceManager(self.cfg)
        self._lock = threading.Lock()
        # 内存结果：{underlying: scan_result_dict}
        self.results: Dict[str, dict] = {}
        self.last_scan_ts: Optional[str] = None
        self.status: Dict[str, str] = {}

    # ------------------------------------------------------------------
    def scan_all(self, codes: Optional[List[str]] = None) -> Dict[str, dict]:
        """扫描全部标的，返回并缓存结果（含 _scan_ts 时间戳供看板显示）。"""
        codes = codes or self.cfg.all_underlying_codes()
        out: Dict[str, dict] = {}
        for code in codes:
            try:
                r = self._scan_one(code)
                out[code] = r
            except Exception as e:  # noqa: BLE001
                logger.error("扫描 %s 失败: %s", code, e)
                out[code] = {"error": str(e), "code": code}
        out["_scan_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self.results.update(out)
            self.last_scan_ts = out["_scan_ts"]
        return out

    def _scan_one(self, code: str) -> dict:
        ucfg = self.cfg.underlying(code)
        spot, chain = self.manager.fetch_underlying(code, allow_mock=False)
        contracts = filter_contracts(
            chain.contracts,
            dte_min=self.cfg.dte_min,
            dte_max=self.cfg.dte_max,
        )
        if not contracts:
            contracts = [c for c in chain.contracts if is_standard_contract(c)]
        spreads = generate_spreads(contracts, ucfg, spot=spot.price,
                                   multiplier=self.cfg.multiplier)
        for sp in spreads:
            fill_account_risk_from_capital(sp, self.cfg.capital)
            sp.suggested_contracts = min(
                suggested_contract_count(sp, self.cfg.single_batch_risk),
                self.cfg.account.get("suggested_contracts_max", 10),
            )
            apply_score(sp, self.cfg)
        ranked = rank_spreads(spreads, top_n=self.cfg.top_n)

        return {
            "code": code,
            "name": ucfg.name,
            "spot": spot.price,
            "spot_source": spot.source,
            "option_source": chain.source,
            "quote_time": spot.quote_time,
            "contracts": len(contracts),
            "spread_count": len(spreads),
            "ranked": ranked,          # List[PutSpread] 已评分排序
            "all": spreads,
            "error": "",
            "status_summary": self.manager.status_summary(),
            "fetch_time": datetime.now().strftime("%H:%M:%S"),
        }

    def get(self, code: str) -> dict:
        with self._lock:
            return self.results.get(code, {})


def build_config_for_cloud() -> None:
    """云端部署时调整 config：确保同花顺优先、超时合理、无本地路径依赖。"""
    import os
    # config.yaml 在云端根目录
    pass
