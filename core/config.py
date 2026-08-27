"""core.config — 配置加载与数据类。"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class ConfigError(Exception):
    pass


@dataclass
class UnderlyingCfg:
    """单个标的（期权品种）的静态配置。"""

    code: str                      # 510500
    name: str
    exchange: str = "sh"
    option_spot_prefix: str = ""   # 新浪标的代码 sh510500
    ths_hex: str = ""              # 同花顺 realhead 路径
    em_secid: str = ""
    strike_step: float = 0.25
    widths: List[float] = field(default_factory=lambda: [0.25, 0.50])
    focus_spread: List[str] = field(default_factory=list)
    option_digit_min: int = 4

    @classmethod
    def from_dict(cls, code: str, d: Dict[str, Any]) -> "UnderlyingCfg":
        base = dict(
            code=code, name=d.get("name", code),
            exchange=d.get("exchange", "sh"),
            option_spot_prefix=d.get("option_spot_prefix", f"sh{code}"),
            ths_hex=d.get("ths_hex", f"hs_{code}"),
            em_secid=d.get("em_secid", f"1.{code}"),
            strike_step=float(d.get("strike_step", 0.25)),
            widths=[float(w) for w in d.get("widths", [0.25, 0.50])],
            focus_spread=list(d.get("focus_spread", [])),
            option_digit_min=int(d.get("option_digit_min", 4)),
        )
        return cls(**base)


@dataclass
class Config:
    account: Dict[str, Any] = field(default_factory=dict)
    scan: Dict[str, Any] = field(default_factory=dict)
    underlyings: Dict[str, UnderlyingCfg] = field(default_factory=dict)
    data_sources: Dict[str, Any] = field(default_factory=dict)
    thresholds: Dict[str, Any] = field(default_factory=dict)
    alerts: Dict[str, Any] = field(default_factory=dict)
    storage: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    # ---- 便捷访问器 ----
    @property
    def capital(self) -> float:
        return float(self.account.get("capital", 500_000))

    @property
    def multiplier(self) -> int:
        return int(self.account.get("multiplier", 10_000))

    @property
    def single_batch_risk(self) -> float:
        """单批最大到期风险预算（元）。"""
        pct = float(self.account.get("single_batch_risk_pct", 0.02))
        return self.capital * pct

    @property
    def interval_seconds(self) -> float:
        return float(self.scan.get("interval_seconds", 5))

    @property
    def dte_min(self) -> int:
        return int(self.scan.get("dte_min", 15))

    @property
    def dte_max(self) -> int:
        return int(self.scan.get("dte_max", 45))

    @property
    def confirm_count(self) -> int:
        return int(self.scan.get("confirm_count", 3))

    @property
    def alert_cooldown_seconds(self) -> int:
        return int(self.scan.get("alert_cooldown_seconds", 600))

    @property
    def top_n(self) -> int:
        return int(self.scan.get("top_n", 10))

    @property
    def stale_after_seconds(self) -> int:
        return int(self.scan.get("stale_after_seconds", 60))

    @property
    def data_priority(self) -> List[str]:
        return list(self.data_sources.get("priority", ["ths", "sina", "eastmoney"]))

    @property
    def option_priority(self) -> List[str]:
        return list(self.data_sources.get("option_priority", ["sina"]))

    def underlying(self, code: str) -> UnderlyingCfg:
        return self.underlyings[code]

    def all_underlying_codes(self) -> List[str]:
        return list(self.underlyings.keys())


def load_config(path: Optional[Path] = None) -> Config:
    path = Path(path or DEFAULT_CONFIG_PATH)
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = Config(
        account=dict(raw.get("account", {})),
        scan=dict(raw.get("scan", {})),
        data_sources=dict(raw.get("data_sources", {})),
        thresholds=dict(raw.get("thresholds", {})),
        alerts=dict(raw.get("alerts", {})),
        storage=dict(raw.get("storage", {})),
        raw=copy.deepcopy(raw),
    )
    uds = raw.get("underlyings", {})
    cfg.underlyings = {
        str(code): UnderlyingCfg.from_dict(str(code), d) for code, d in uds.items()
    }
    return cfg
