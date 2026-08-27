"""app.py — 510500/588080 Put 价差云端实时看板（Streamlit Community Cloud）

特点：
- 电脑关机也能访问（云端运行，自己拉行情）
- 手机 + 网页自适应（同一 URL，响应式）
- 云端自动扫描：每 interval_seconds（默认30s）自动拉取同花顺/新浪行情
- 同花顺优先（标的价）+ 新浪（期权盘口/Greeks），自动降级
- 无本地数据库，扫描结果存内存
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cloud_scanner import CloudScanner  # noqa: E402
from core.config import load_config  # noqa: E402

st.set_page_config(page_title="510500/588080 Put 价差实时雷达", layout="wide",
                   initial_sidebar_state="collapsed")


# ----------------------------------------------------------------------
# 全局扫描器（单例）+ 后台自动扫描线程
# ----------------------------------------------------------------------
@st.cache_resource
def _get_scanner() -> CloudScanner:
    return CloudScanner()


def _bg_scan(scanner: CloudScanner):
    """后台线程持续自动扫描（间隔由 config 控制）。"""
    cfg = load_config()
    interval = cfg.interval_seconds
    while True:
        try:
            scanner.scan_all()
        except Exception as e:  # noqa: BLE001
            st.write(f"扫描异常: {e}")
        time.sleep(interval)


# 启动后台自动扫描线程（仅一次）
scanner = _get_scanner()
if "bg_thread_started" not in st.session_state:
    st.session_state["bg_thread_started"] = True
    t = threading.Thread(target=_bg_scan, args=(scanner,), daemon=True)
    t.start()
    # 首次立即扫描一次，避免空白
    scanner.scan_all()


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def fmt(x, nd=4):
    if x is None or x != x:
        return "-"
    return f"{x:.{nd}f}"


def pct(x):
    if x is None or x != x:
        return "-"
    return f"{x:.2%}"


def money(x):
    if x is None or x != x:
        return "-"
    return f"¥{x:,.0f}"


def status_badge(s: str) -> str:
    return {"强机会": "🔴 强机会", "优质机会": "🟠 优质", "观察": "🟡 观察", "跳过": "⚪ 跳过"}.get(s, s)


# ----------------------------------------------------------------------
# 页面
# ----------------------------------------------------------------------
st.title("🔍 510500 / 588080 Put 价差实时雷达")
st.caption("云端免费版 · 电脑关机也能看 · 同花顺优先 + 新浪期权 · 自动刷新")

# 顶部信息条
col_a, col_b, col_c = st.columns(3)
with col_a:
    st.metric("最后扫描", scanner.last_scan_ts or "首次扫描中…")
with col_b:
    st.metric("标的", f"{len(scanner.cfg.all_underlying_codes())} 个")
with col_c:
    st.metric("扫描间隔", f"{scanner.cfg.interval_seconds}s")

# 双标的概览
st.subheader("📊 双标的概览")
ov_cols = st.columns(len(scanner.cfg.all_underlying_codes()))
for i, code in enumerate(scanner.cfg.all_underlying_codes()):
    r = scanner.get(code)
    with ov_cols[i]:
        if not r:
            st.write(f"{code} 数据加载中…")
            continue
        st.metric(f"{code} {r.get('name', '')}", fmt(r.get("spot")),
                  help=f"数据源: {r.get('spot_source')} / {r.get('option_source')}")
        if r.get("ranked"):
            t = r["ranked"][0]
            st.markdown(f"**#1 {t.display_name}** {status_badge(t.status)}  "
                        f"净收 `{fmt(t.credit)}` 安全垫 `{pct(t.cushion)}` 评分 `{fmt(t.score,2)}`")
        st.caption(f"源 {r.get('spot_source','-')} / {r.get('option_source','-')} · 合约 {r.get('contracts')} · 价差 {r.get('spread_count')}")

# 排行
st.subheader("🏆 Put 信用价差排行")
underlying = st.radio("标的", scanner.cfg.all_underlying_codes(),
                      format_func=lambda c: f"{c} - {scanner.cfg.underlying(c).name}",
                      horizontal=True)
r = scanner.get(underlying)
if not r or not r.get("ranked"):
    st.info("数据加载中，请稍候（首次需拉取行情，约数秒）…")
    st.stop()

# 过滤
cols = st.columns(4)
min_score = cols[0].slider("最低评分", 5.0, 10.0, 5.0, 0.5)
min_cush = cols[1].slider("最低安全垫 %", 0.0, 10.0, 0.0, 0.5) / 100.0
width_f = cols[2].selectbox("价差宽度", ["全部"] + [str(w) for w in scanner.cfg.underlying(underlying).widths])
top_n = int(cols[3].selectbox("Top N", [5, 10, 15, 20], index=1))

top = [s for s in r["ranked"] if s.score >= min_score and s.cushion >= min_cush
       and (width_f == "全部" or abs(s.width - float(width_f)) < 1e-9)][:top_n]

if not top:
    st.info("当前无满足条件的候选。")
else:
    rows = []
    for i, sp in enumerate(top, 1):
        rows.append({
            "排名": i,
            "组合": sp.display_name + (" ★" if sp.is_focus else ""),
            "状态": status_badge(sp.status),
            "可成交净收": fmt(sp.credit),
            "最大盈利": money(sp.max_profit),
            "最大亏损": money(sp.max_loss),
            "BE": fmt(sp.breakeven),
            "安全垫": pct(sp.cushion),
            "收益/风险": pct(sp.rr),
            "IV": pct(sp.sell_contract.iv if sp.sell_contract else None),
            "Delta": fmt(sp.sell_contract.delta if sp.sell_contract else None, 2),
            "评分": f"{sp.score:.2f}",
            "建议手数": sp.suggested_contracts,
        })
    st.dataframe(rows, use_container_width=True, hide_index=True,
                 column_config={"组合": st.column_config.TextColumn("组合")})

# 数据源状态 + 陈旧提示
st.caption(f"数据源状态: {r.get('status_summary', '-')} | 行情时间: {r.get('quote_time', '-')} | 抓取: {r.get('fetch_time', '-')}")
if r.get("quote_time"):
    try:
        q = datetime.strptime(r["quote_time"], "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - q).total_seconds() > scanner.cfg.stale_after_seconds:
            st.warning("⚠️ 行情陈旧（收盘或行情源未更新），最新盘口以交易时段为准。")
    except ValueError:
        pass

st.divider()
st.caption("数据仅供研究参考，不构成投资建议 · 只扫描与提醒，不自动下单 · 免费版休眠时首访唤醒约 30 秒")
