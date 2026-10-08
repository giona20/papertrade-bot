"""Dashboard Streamlit (alternativa a dashboard.py).

  pip install -r requirements-streamlit.txt
  streamlit run streamlit_dashboard.py                         # database in config.yaml
  streamlit run streamlit_dashboard.py -- --db data/sim.db     # dati di una simulazione
  streamlit run streamlit_dashboard.py -- --config config.local.yaml

Legge lo stesso database del bot, in sola lettura, e si aggiorna da sola.
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

from dashboard import state   # stessa lettura del database della dashboard locale

ROME = ZoneInfo("Europe/Rome")
COLORS = {"INSOLVENTE": "#9b2c2c", "BOOTSTRAP": "#b7791f", "DECADIMENTO": "#2b6cb0", "SWEEP": "#276749"}
MEANING = {
    "INSOLVENTE": "L'LP non copre la coda: i profitti aspettano, le perdite coniano 100 PAPER per $1.",
    "BOOTSTRAP": "LP sotto $2M: emissione massima, fase di accumulo PAPER.",
    "DECADIMENTO": "LP oltre $2M: emissione in lento calo, valuta PAPER in base all'APR.",
    "SWEEP": "LP sopra $5M: ogni dollaro di guadagno extra va agli staker.",
}


def args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--refresh", type=int, default=5, help="secondi tra un aggiornamento e l'altro")
    return ap.parse_args(sys.argv[1:])


def money(x):
    return "n/d" if x is None or pd.isna(x) else f"${x:,.2f}"


a = args()
db = a.db or yaml.safe_load(Path(a.config).read_text())["db_path"]
st.set_page_config(page_title="Papertrade bot", layout="wide")


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
        f"<div style='opacity:.75'>{MEANING.get(L['regime'], '')} · aggiornato {L['time']} (Roma)</div></div>",
        unsafe_allow_html=True)

    c = st.columns(4)
    c[0].metric("LP effettiva", money(L["eff_lp"]))
    c[1].metric("Coda", money(L["queue"]), f"{int(L['queue_len'] or 0)} posizioni", delta_color="off")
    c[2].metric("Emissione", f"{L['emission']:.1f} PAPER/$")
    c[3].metric("APR staking", "n/d" if L["apr"] is None else f"{L['apr']:.0%}")
    c = st.columns(4)
    c[0].metric("Prezzo PAPER", "n/d" if L["paper_price"] is None else f"${L['paper_price']:.6f}")
    c[1].metric("PAPER tuoi", f"{(L['my_paper'] or 0) + (L['my_staked'] or 0):,.0f}",
                f"{L['my_staked'] or 0:,.0f} in staking", delta_color="off")
    c[2].metric("Ricompense", money(L["my_rewards"]))
    c[3].metric("In coda per te", money(L["my_queued"]))

    st.subheader("Scheda azioni")
    st.code(L["card"], language=None)

    st.subheader("LP effettiva e coda")
    h = pd.DataFrame(d["hist"])
    if len(h) > 1:
        h["ora"] = pd.to_datetime(h["ts"], unit="s", utc=True).dt.tz_convert(ROME)
        st.line_chart(h.set_index("ora")[["eff_lp", "queue"]].rename(
            columns={"eff_lp": "LP effettiva", "queue": "Coda"}))
    else:
        st.caption("Servono più dati per il grafico.")

    left, right = st.columns(2)
    with left:
        st.subheader("Posizioni aperte")
        op = pd.DataFrame(d["open"])
        if op.empty:
            st.caption("Nessuna posizione aperta.")
        else:
            st.dataframe(op[["id", "strategy", "asset", "side", "margin", "leverage", "entry", "opened"]],
                         hide_index=True, width="stretch")
    with right:
        st.subheader("Risultati per strategia")
        sr = pd.DataFrame(d["strategies"])
        if sr.empty:
            st.caption("Nessun trade chiuso.")
        else:
            sr = sr.rename(columns={"strategy": "Strategia", "n": "Trade", "wins": "Vinti", "pnl": "PnL $",
                                    "paper": "PAPER", "cost_per_paper": "$/PAPER", "queued": "In coda $"})
            st.dataframe(sr, hide_index=True, width="stretch",
                         column_config={"PnL $": st.column_config.NumberColumn(format="%.2f"),
                                        "$/PAPER": st.column_config.NumberColumn(format="%.4f"),
                                        "PAPER": st.column_config.NumberColumn(format="%d")})

    st.subheader("Ultimi trade chiusi")
    tr = pd.DataFrame(d["trades"])
    if tr.empty:
        st.caption("Nessun trade chiuso.")
    else:
        st.dataframe(tr[["id", "closed", "strategy", "asset", "side", "leverage", "pnl", "paper_minted",
                         "haircut", "close_reason"]], hide_index=True, width="stretch",
                     column_config={"haircut": st.column_config.NumberColumn("trattenuta", format="%.2f")})

    st.subheader("Registro")
    ev = pd.DataFrame(d["events"])
    if not ev.empty:
        st.dataframe(ev[["time", "level", "msg"]], hide_index=True, width="stretch")
    st.caption(f"Pagina aggiornata alle {datetime.now(ROME):%H:%M:%S} (Roma) · database: {db}")


page()
