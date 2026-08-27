"""core.scoring — Put 信用价差 0~10 分综合评分。

评分维度（需求第六节）：
净收、安全垫、收益/风险、Bid/Ask 流动性、卖出 Put Delta、成交量/持仓量、IV。
评分 < 5 跳过；5~6.99 观察；7~8.99 优质机会；>= 9 强机会。

特别约束：不能因为收益/风险比高就直接排名第一；盈亏平衡点接近或高于现价
（安全垫不足）必须明显扣分。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import Config, UnderlyingCfg
from .spreads import PutSpread


@dataclass
class ScoreDetail:
    total: float = 0.0
    credit: float = 0.0
    cushion: float = 0.0
    rr: float = 0.0
    liquidity: float = 0.0
    delta: float = 0.0
    volume_oi: float = 0.0
    iv: float = 0.0
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total": round(self.total, 2),
            "credit": round(self.credit, 2),
            "cushion": round(self.cushion, 2),
            "rr": round(self.rr, 2),
            "liquidity": round(self.liquidity, 2),
            "delta": round(self.delta, 2),
            "volume_oi": round(self.volume_oi, 2),
            "iv": round(self.iv, 2),
            "notes": self.notes,
        }


# 各维度满分
_MAX_CREDIT = 2.5
_MAX_CUSHION = 2.5
_MAX_RR = 1.5
_MAX_LIQUIDITY = 1.5
_MAX_DELTA = 1.0
_MAX_VOLOI = 1.0
_MAX_IV = 1.0          # 基础项满分 9.0，IV 最多再 +1.0，总分 <= 10


def _lin(x: float, x_lo: float, x_hi: float, s_lo: float, s_hi: float) -> float:
    """x 在 [x_lo, x_hi] 线性映射到 [s_lo, s_hi]，越界截断。"""
    if x_hi == x_lo:
        return s_lo
    t = (x - x_lo) / (x_hi - x_lo)
    t = max(0.0, min(1.0, t))
    return s_lo + t * (s_hi - s_lo)


def _status(score: float) -> str:
    if score < 5:
        return "跳过"
    if score < 7:
        return "观察"
    if score < 9:
        return "优质机会"
    return "强机会"


def score_spread(sp: PutSpread, th: Dict, cfg: UnderlyingCfg) -> ScoreDetail:
    """对单个价差打分。th 为 config.thresholds 字典。"""
    d = ScoreDetail()

    credit_att = float(th.get("credit_attractive", 0.085))
    credit_good = float(th.get("credit_good", 0.095))
    credit_strong = float(th.get("credit_strong", 0.105))
    cushion_min = float(th.get("cushion_min", 0.02))
    rr_min = float(th.get("rr_min", 0.50))
    delta_lo = float(th.get("delta_pref_lo", 0.15))
    delta_hi = float(th.get("delta_pref_hi", 0.35))
    iv_lo = float(th.get("iv_pref_lo", 0.15))
    iv_hi = float(th.get("iv_pref_hi", 0.35))

    # 1) 净收
    if sp.credit >= credit_strong:
        d.credit = _MAX_CREDIT
    elif sp.credit >= credit_good:
        d.credit = _lin(sp.credit, credit_good, credit_strong, 2.0, _MAX_CREDIT)
    elif sp.credit >= credit_att:
        d.credit = _lin(sp.credit, credit_att, credit_good, 1.3, 2.0)
    else:
        d.credit = _lin(sp.credit, 0.0, credit_att, 0.3, 1.3)

    # 2) 安全垫 —— 需求：安全垫不足必须明显扣分
    cap_observe = False
    if sp.cushion <= 0:
        d.cushion = 0.0
        cap_observe = True
        d.notes.append("安全垫<=0，盈亏平衡点不低于现价")
    elif sp.cushion < cushion_min:
        d.cushion = _lin(sp.cushion, 0.0, cushion_min, 0.0, 1.5)
        d.notes.append(f"安全垫不足({sp.cushion:.2%}<{cushion_min:.0%})")
    else:
        # 达到目标后继续加分，满分 2.5
        d.cushion = _lin(sp.cushion, cushion_min, cushion_min * 2.5, 1.5, _MAX_CUSHION)

    # 3) 收益/风险
    if sp.rr >= rr_min:
        d.rr = _lin(sp.rr, rr_min, rr_min * 2.0, 1.0, _MAX_RR)
    else:
        d.rr = _lin(sp.rr, 0.0, rr_min, 0.0, 1.0)

    # 4) 流动性（Bid/Ask 宽度）：相对卖价比例越小越优
    sell_rel = sp.sell_spread / sp.sell_contract.ask1 if sp.sell_contract and sp.sell_contract.ask1 > 0 else 0.0
    buy_rel = sp.buy_spread / sp.buy_contract.ask1 if sp.buy_contract and sp.buy_contract.ask1 > 0 else 0.0
    rel = sell_rel + buy_rel
    if rel <= 0.10:
        d.liquidity = _MAX_LIQUIDITY
    elif rel <= 0.5:
        d.liquidity = _lin(rel, 0.10, 0.5, _MAX_LIQUIDITY, 0.5)
    else:
        d.liquidity = _lin(rel, 0.5, 1.0, 0.5, 0.0)
        d.notes.append(f"盘口过宽(相对滑点{rel:.0%})")

    # 5) 卖出 Put Delta（绝对值优选 0.15~0.35）
    delta = sp.sell_contract.delta if sp.sell_contract else None
    if delta is None:
        d.delta = 0.4  # 无 Greeks 时给中性偏保守分
    else:
        ad = abs(delta)
        if delta_lo <= ad <= delta_hi:
            d.delta = _MAX_DELTA
        elif ad < delta_lo:
            d.delta = _lin(ad, 0.0, delta_lo, 0.2, _MAX_DELTA)
        else:
            d.delta = _lin(ad, delta_hi, 0.8, _MAX_DELTA, 0.0)

    # 6) 成交量 / 持仓量
    vol = sp.sell_contract.volume + sp.buy_contract.volume if sp.sell_contract and sp.buy_contract else 0
    oi = sp.sell_contract.oi + sp.buy_contract.oi if sp.sell_contract and sp.buy_contract else 0
    min_vol = float(th.get("min_volume", 0))
    min_oi = float(th.get("min_oi", 0))
    score_vol = 0.0
    if vol >= max(min_vol, 100):
        score_vol = 0.5
    elif vol >= max(min_vol, 10):
        score_vol = 0.3
    score_oi = 0.0
    if oi >= max(min_oi, 1000):
        score_oi = 0.5
    elif oi >= max(min_oi, 100):
        score_oi = 0.3
    d.volume_oi = score_vol + score_oi
    if vol < max(min_vol, 10):
        d.notes.append(f"成交量低({vol})")

    # 7) IV（可取得时，在优选区间加分）
    iv = sp.sell_contract.iv if sp.sell_contract else None
    if iv is not None:
        if iv_lo <= iv <= iv_hi:
            d.iv = _MAX_IV
        elif iv < iv_lo:
            d.iv = _lin(iv, 0.05, iv_lo, 0.2, _MAX_IV)
        else:
            d.iv = _lin(iv, iv_hi, 0.8, _MAX_IV, 0.0)

    d.total = min(10.0, d.credit + d.cushion + d.rr + d.liquidity + d.delta + d.volume_oi + d.iv)
    # 安全垫<=0（盈亏平衡点不低于现价）时封顶在"观察"档，杜绝虚假高收益排名
    if cap_observe:
        d.total = min(d.total, 6.99)
    return d


def apply_score(sp: PutSpread, cfg: Config) -> ScoreDetail:
    """评分并写回价差对象。返回评分明细。"""
    det = score_spread(sp, cfg.thresholds, cfg.underlying(sp.underlying))
    sp.score = round(det.total, 2)
    sp.status = _status(sp.score)
    sp.meta["score_detail"] = det.as_dict()
    return det


def rank_spreads(spreads: List[PutSpread], top_n: int = 10) -> List[PutSpread]:
    """按评分降序排序，评分 < 5 的跳过，返回 Top N。"""
    valid = [s for s in spreads if s.score >= 5]
    valid.sort(key=lambda s: s.score, reverse=True)
    return valid[:top_n]
