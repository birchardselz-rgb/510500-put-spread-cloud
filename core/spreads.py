"""core.spreads — Put 信用价差生成与收益风险计算。

关键原则（来自需求）：
- 不得使用"最新价-最新价"作为真实交易机会。
- 可成交净收 = 卖出腿 Bid1 - 买入腿 Ask1；净收 <= 0 则过滤。
- mid 理论净收仅用于比较，不用于报警成交标准。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .config import UnderlyingCfg
from .contracts import OptionContract


@dataclass
class PutSpread:
    """一个 Put 信用价差候选组合。"""

    underlying: str = ""
    underlying_name: str = ""
    sell_contract: Optional[OptionContract] = None   # 卖高执行价 Put
    buy_contract: Optional[OptionContract] = None    # 买低执行价 Put
    width: float = 0.0                                # 执行价宽度
    spot: float = 0.0                                 # 标的现价

    # ---- 核心计算（需求第四节）----
    credit: float = 0.0            # 可成交净收 = sell.Bid1 - buy.Ask1
    mid_credit: float = 0.0        # mid 理论净收（仅比较）
    max_profit: float = 0.0        # credit * multiplier
    max_loss: float = 0.0          # (width - credit) * multiplier
    breakeven: float = 0.0         # sell_strike - credit
    cushion: float = 0.0           # (spot - be) / spot
    rr: float = 0.0                # 最大盈利 / 最大亏损
    slippage: float = 0.0          # 两腿 Bid/Ask 宽度之和
    sell_spread: float = 0.0       # 卖出腿滑点
    buy_spread: float = 0.0        # 买入腿滑点

    score: float = 0.0
    status: str = "跳过"            # 跳过 / 观察 / 优质机会 / 强机会
    is_focus: bool = False          # 是否重点监控组合

    # 账户风险（按账户资金）
    risk_1: float = 0.0
    risk_3: float = 0.0
    risk_5: float = 0.0
    risk_10: float = 0.0
    risk_pct_1: float = 0.0
    risk_pct_3: float = 0.0
    risk_pct_5: float = 0.0
    risk_pct_10: float = 0.0
    suggested_contracts: int = 0   # 建议手数（不代表自动下单）

    source: str = ""
    quote_time: str = ""
    stale: bool = False

    # 便于看板展示的辅助字段
    meta: Dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """组合唯一键，用于报警连续计数与去重。"""
        return f"{self.underlying}|{self.sell_contract.strike:.4g}/{self.buy_contract.strike:.4g}P"

    @property
    def display_name(self) -> str:
        return f"{self.sell_contract.strike:.3g}/{self.buy_contract.strike:.3g}P"

    def snapshot_row(self) -> dict:
        """转存数据库用的平铺字典。"""
        return {
            "underlying": self.underlying,
            "underlying_name": self.underlying_name,
            "spread_key": self.key,
            "display_name": self.display_name,
            "sell_code": self.sell_contract.option_code if self.sell_contract else "",
            "buy_code": self.buy_contract.option_code if self.buy_contract else "",
            "sell_strike": self.sell_contract.strike if self.sell_contract else 0.0,
            "buy_strike": self.buy_contract.strike if self.buy_contract else 0.0,
            "width": self.width,
            "spot": self.spot,
            "credit": self.credit,
            "mid_credit": self.mid_credit,
            "max_profit": self.max_profit,
            "max_loss": self.max_loss,
            "breakeven": self.breakeven,
            "cushion": self.cushion,
            "rr": self.rr,
            "slippage": self.slippage,
            "score": self.score,
            "status": self.status,
            "iv": self.sell_contract.iv if self.sell_contract else None,
            "delta": self.sell_contract.delta if self.sell_contract else None,
            "suggested_contracts": self.suggested_contracts,
            "sell_bid1": self.sell_contract.bid1 if self.sell_contract else 0.0,
            "sell_ask1": self.sell_contract.ask1 if self.sell_contract else 0.0,
            "buy_bid1": self.buy_contract.bid1 if self.buy_contract else 0.0,
            "buy_ask1": self.buy_contract.ask1 if self.buy_contract else 0.0,
            "sell_oi": self.sell_contract.oi if self.sell_contract else 0,
            "buy_oi": self.buy_contract.oi if self.buy_contract else 0,
            "dte": self.sell_contract.dte if self.sell_contract else None,
            "source": self.source,
            "quote_time": self.quote_time,
            "stale": self.stale,
        }


# ---------------------------------------------------------------------------
# 价差生成
# ---------------------------------------------------------------------------
def _sorted_put_strikes(puts: List[OptionContract]) -> List[float]:
    """去重排序的认沽行权价列表（升序）。"""
    return sorted({p.strike for p in puts})


def generate_spreads(
    contracts: List[OptionContract],
    cfg: UnderlyingCfg,
    spot: float,
    multiplier: int = 10_000,
    today=None,
) -> List[PutSpread]:
    """遍历当前合约链自动生成 Put 信用价差。

    只生成"相邻档位"组合：对每个行权价（卖出腿），向下找宽度最接近
    widths[i] 的买入腿。宽度取距目标宽度最近的已存在行权价差。
    """
    puts = [c for c in contracts if c.cp == "P"]
    strikes = _sorted_put_strikes(puts)
    by_strike: Dict[float, OptionContract] = {}
    for c in puts:
        # 同一行权价多份合约（标准/调整）时优先保留盘口更优者
        prev = by_strike.get(c.strike)
        if prev is None or (c.tradable and not prev.tradable):
            by_strike[c.strike] = c

    spreads: List[PutSpread] = []
    for i, sell_k in enumerate(strikes):
        for target_w in cfg.widths:
            # 向下找最接近 target_w 的更低执行价
            best_k, best_diff = None, None
            for j in range(i - 1, -1, -1):
                w = sell_k - strikes[j]
                if target_w and abs(w - target_w) < 1e-9:
                    best_k, best_diff = strikes[j], 0.0
                    break
                diff = abs(w - target_w)
                if best_diff is None or diff < best_diff:
                    best_k, best_diff = strikes[j], diff
            if best_k is None:
                continue
            w = sell_k - best_k
            # 只有两档行权价存在且可交易才构造
            sell_c = by_strike.get(sell_k)
            buy_c = by_strike.get(best_k)
            if sell_c is None or buy_c is None:
                continue
            sp = compute_spread(sell_c, buy_c, w, spot, cfg, multiplier)
            if sp is not None:
                spreads.append(sp)
    return spreads


# ---------------------------------------------------------------------------
# 核心计算
# ---------------------------------------------------------------------------
def compute_spread(
    sell_put: OptionContract,
    buy_put: OptionContract,
    width: float,
    spot: float,
    cfg: UnderlyingCfg,
    multiplier: int = 10_000,
) -> Optional[PutSpread]:
    """计算单个 Put 信用价差的所有指标。不可交易/净收<=0 返回 None。"""
    if not sell_put.tradable or not buy_put.tradable:
        return None

    credit = sell_put.bid1 - buy_put.ask1
    if credit <= 0:
        return None

    max_profit = credit * multiplier
    max_loss = (width - credit) * multiplier
    if max_loss <= 0:
        return None
    breakeven = sell_put.strike - credit
    cushion = (spot - breakeven) / spot if spot > 0 else 0.0
    rr = max_profit / max_loss if max_loss > 0 else 0.0

    sell_spread = sell_put.spread_width
    buy_spread = buy_put.spread_width

    sp = PutSpread(
        underlying=cfg.code,
        underlying_name=cfg.name,
        sell_contract=sell_put,
        buy_contract=buy_put,
        width=width,
        spot=spot,
        credit=credit,
        mid_credit=sell_put.mid - buy_put.mid,
        max_profit=max_profit,
        max_loss=max_loss,
        breakeven=breakeven,
        cushion=cushion,
        rr=rr,
        slippage=sell_spread + buy_spread,
        sell_spread=sell_spread,
        buy_spread=buy_spread,
        source=sell_put.source or buy_put.source,
        quote_time=sell_put.quote_time or buy_put.quote_time,
        stale=sell_put.dte is None,  # 占位，调用方会修正
    )
    _fill_account_risk(sp, multiplier)
    return sp


def _fill_account_risk(sp: PutSpread, multiplier: int) -> None:
    """按账户资金填充 1/3/5/10 组最大亏损及占比（由调用方提供资金）。"""
    # 注意：账户资金由 config 提供，这里只放占位值，最终在 spread 组装阶段用
    # capital 重新计算（见 fill_account_risk_from_capital）。
    sp.risk_1 = sp.max_loss
    sp.risk_3 = sp.max_loss * 3
    sp.risk_5 = sp.max_loss * 5
    sp.risk_10 = sp.max_loss * 10


def fill_account_risk_from_capital(sp: PutSpread, capital: float) -> None:
    """按账户资金填充风险占比与建议手数。"""
    sp.risk_pct_1 = sp.risk_1 / capital if capital > 0 else 0.0
    sp.risk_pct_3 = sp.risk_3 / capital if capital > 0 else 0.0
    sp.risk_pct_5 = sp.risk_5 / capital if capital > 0 else 0.0
    sp.risk_pct_10 = sp.risk_10 / capital if capital > 0 else 0.0


def suggested_contract_count(sp: PutSpread, risk_budget: float) -> int:
    """按单批最大风险预算计算建议手数（仅参考，不自动下单）。"""
    if sp.max_loss <= 0:
        return 0
    return max(0, int(risk_budget // sp.max_loss))
