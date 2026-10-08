"""Streamlit dashboard (alternative to dashboard.py).

Two data sources:
  - local bot: the database written by `python run.py run` (action card, positions, trades);
  - live on-chain: reads the Papertrade contracts and Hyperliquid prices directly. This is the mode
    used on Streamlit Cloud, where the bot database does not exist.

  pip install -r requirements-streamlit.txt
  streamlit run streamlit_dashboard.py                         # database set in config.yaml
  streamlit run streamlit_dashboard.py -- --db data/sim.db     # data from a simulation
  streamlit run streamlit_dashboard.py -- --config config.local.yaml

Reads the bot database read-only and refreshes on its own.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yaml

from dashboard import state   # same database reader as the local dashboard

ROME = ZoneInfo("Europe/Rome")
COLORS = {"INSOLVENT": "#9b2c2c", "BOOTSTRAP": "#b7791f", "DECAY": "#2b6cb0", "SWEEP": "#276749"}
MEANING = {
    "INSOLVENT": "The LP doesn't cover the queue: profits wait, losses mint 100 PAPER per $1.",
    "BOOTSTRAP": "LP below $2M: max emission, PAPER accumulation phase.",
    "DECAY": "LP above $2M: emission slowly declining, value PAPER by its APR.",
    "SWEEP": "LP above $5M: every extra dollar of LP gain goes to stakers.",
}


def args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--refresh", type=int, default=5, help="seconds between refreshes")
    return ap.parse_args(sys.argv[1:])


def money(x):
    return "n/a" if x is None or pd.isna(x) else f"${x:,.2f}"


a = args()
cfg = yaml.safe_load(Path(a.config).read_text())
db = a.db or cfg["db_path"]
st.set_page_config(page_title="Papertrade bot", layout="wide")

has_db = Path(db).exists()
with st.sidebar:
    source = st.radio("Data source", ["Live on-chain", "Local bot"], index=1 if has_db else 0,
                      help="Live: Papertrade contracts + Hyperliquid prices. Local bot: your bot's database.")
    wallet = st.text_input("Wallet to follow (optional)", placeholder="0x…",
                           help="Live mode only: shows balance, queue, PAPER and rewards for this address.")
    st.caption("Nothing is stored: the wallet stays in this session only.")


@st.cache_data(ttl=15, show_spinner=False)
def fetch_live(wallet: str):
    from ptbot.live import live_state
    return live_state(cfg, wallet.strip() or None)


@st.fragment(run_every=max(a.refresh, 15))
def live_page():
    try:
        d = fetch_live(wallet or "")
    except Exception as e:
        st.error(f"On-chain read failed: {e}")
        return
    hist = st.session_state.setdefault("hist", [])
    if not hist or hist[-1]["ts"] != d["ts"]:
        hist.append({"ts": d["ts"], "Effective LP": d["eff_lp"], "Queue": d["queue"]})
        del hist[:-500]
    color = COLORS.get(d["regime"], "#555")
    st.markdown(
        f"<div style='border-left:10px solid {color};padding:8px 16px;margin-bottom:8px'>"
        f"<div style='font-size:2.6rem;font-weight:800;color:{color};line-height:1'>{d['regime']}</div>"
        f"<div style='opacity:.75'>{MEANING.get(d['regime'], '')}</div>"
        f"<div style='opacity:.75'>{d['phase']} · trading {'PAUSED' if d['trading_paused'] else 'active'} · "
        f"live on-chain · {datetime.fromtimestamp(d['ts'], ROME):%d/%m %H:%M:%S} (Rome)</div></div>",
        unsafe_allow_html=True)

    c = st.columns(4)
    c[0].metric("Effective LP", money(d["eff_lp"]), help="trackedLpUsd + sideBucket − queue")
    c[1].metric("Queue", money(d["queue"]))
    c[2].metric("Emission", f"{d['emission']:.1f} PAPER/$")
    c[3].metric("Positions opened so far", f"{d['positions_opened']:,}")
    c = st.columns(4)
    c[0].metric("PAPER supply", f"{d['paper_supply']:,.0f}")
    c[1].metric("PAPER staked", f"{d['paper_staked']:,.0f}")
    c[2].metric("User deposits", money(d["deposits"]), help="Exchange totalBalances")
    c[3].metric("Locked margin", money(d["locked_margin"]))

    if d["wallet"]:
        w = d["wallet"]
        st.subheader(f"Wallet {w['address'][:6]}…{w['address'][-4:]}")
        c = st.columns(4)
        c[0].metric("Available balance", money(w["balance"]))
        c[1].metric("Queued", money(w["queued"]))
        c[2].metric("PAPER (not staked)", f"{w['PAPER']:,.0f}")
        c[3].metric("Claimable rewards", money(w["rewards"]))

    st.subheader("Markets")
    m = pd.DataFrame(d["markets"])
    if not m.empty:
        st.dataframe(m, hide_index=True, width="stretch", column_config={
            "price": st.column_config.NumberColumn(format="%.2f"),
            "OI long": st.column_config.NumberColumn(format="%.4f"),
            "OI short": st.column_config.NumberColumn(format="%.4f"),
            "OI long $": st.column_config.NumberColumn(format="$%.0f"),
            "OI short $": st.column_config.NumberColumn(format="$%.0f"),
            "long cap $": st.column_config.NumberColumn(format="$%.0f"),
            "short cap $": st.column_config.NumberColumn(format="$%.0f"),
            "liq. buffer bps": st.column_config.NumberColumn(format="%.2f")})

    st.subheader("Effective LP and queue (since you opened the page)")
    if len(hist) > 1:
        h = pd.DataFrame(hist)
        h["time"] = pd.to_datetime(h["ts"], unit="s", utc=True).dt.tz_convert(ROME)
        st.line_chart(h.set_index("time")[["Effective LP", "Queue"]])
    else:
        st.caption("The chart fills while the page stays open (one point every 15 seconds).")
    st.caption(f"Treasury (USDC in the contract, deposits included): {money(d['treasury'])} · "
               f"accumulated staker fees: {money(d['staker_fees'])} · "
               "values reconstructed from unverified contracts: they may contain errors.")


@st.fragment(run_every=a.refresh)
def page():
    d = state(db)
    if d.get("error"):
        st.info(d["error"])
        return
    L = d["last"]
    color = COLORS.get(L["regime"], "#555")
    st.markdown(
        f"<div style='border-left:10px solid {color};padding:8px 16px;margin-bottom:8px'>"
        f"<div style='font-size:2.6rem;font-weight:800;color:{color};line-height:1'>{L['regime']}</div>"
        f"<div style='opacity:.75'>{MEANING.get(L['regime'], '')} · updated {L['time']} (Rome)</div></div>",
        unsafe_allow_html=True)

    c = st.columns(4)
    c[0].metric("Effective LP", money(L["eff_lp"]))
    c[1].metric("Queue", money(L["queue"]), f"{int(L['queue_len'] or 0)} positions", delta_color="off")
    c[2].metric("Emission", f"{L['emission']:.1f} PAPER/$")
    c[3].metric("Staking APR", "n/a" if L["apr"] is None else f"{L['apr']:.0%}")
    c = st.columns(4)
    c[0].metric("PAPER price", "n/a" if L["paper_price"] is None else f"${L['paper_price']:.6f}")
    c[1].metric("Your PAPER", f"{(L['my_paper'] or 0) + (L['my_staked'] or 0):,.0f}",
                f"{L['my_staked'] or 0:,.0f} staked", delta_color="off")
    c[2].metric("Rewards", money(L["my_rewards"]))
    c[3].metric("Queued for you", money(L["my_queued"]))

    st.subheader("Action card")
    st.code(L["card"], language=None)

    st.subheader("Effective LP and queue")
    h = pd.DataFrame(d["hist"])
    if len(h) > 1:
        h["time"] = pd.to_datetime(h["ts"], unit="s", utc=True).dt.tz_convert(ROME)
        st.line_chart(h.set_index("time")[["eff_lp", "queue"]].rename(
            columns={"eff_lp": "Effective LP", "queue": "Queue"}))
    else:
        st.caption("Not enough data for the chart yet.")

    left, right = st.columns(2)
    with left:
        st.subheader("Open positions")
        op = pd.DataFrame(d["open"])
        if op.empty:
            st.caption("No open positions.")
        else:
            st.dataframe(op[["id", "strategy", "asset", "side", "margin", "leverage", "entry", "opened"]],
                         hide_index=True, width="stretch")
    with right:
        st.subheader("Results by strategy")
        sr = pd.DataFrame(d["strategies"])
        if sr.empty:
            st.caption("No closed trades.")
        else:
            sr = sr.rename(columns={"strategy": "Strategy", "n": "Trades", "wins": "Won", "pnl": "PnL $",
                                    "paper": "PAPER", "cost_per_paper": "$/PAPER", "queued": "Queued $"})
            st.dataframe(sr, hide_index=True, width="stretch",
                         column_config={"PnL $": st.column_config.NumberColumn(format="%.2f"),
                                        "$/PAPER": st.column_config.NumberColumn(format="%.4f"),
                                        "PAPER": st.column_config.NumberColumn(format="%d")})

    st.subheader("Latest closed trades")
    tr = pd.DataFrame(d["trades"])
    if tr.empty:
        st.caption("No closed trades.")
    else:
        st.dataframe(tr[["id", "closed", "strategy", "asset", "side", "leverage", "pnl", "paper_minted",
                         "haircut", "close_reason"]], hide_index=True, width="stretch",
                     column_config={"haircut": st.column_config.NumberColumn("haircut", format="%.2f")})

    st.subheader("Log")
    ev = pd.DataFrame(d["events"])
    if not ev.empty:
        st.dataframe(ev[["time", "level", "msg"]], hide_index=True, width="stretch")
    st.caption(f"Page updated at {datetime.now(ROME):%H:%M:%S} (Rome) · database: {db}")


if source == "Live on-chain":
    live_page()
else:
    page()
