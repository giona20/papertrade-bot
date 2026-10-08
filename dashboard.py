"""Dashboard locale senza dipendenze esterne.

  python dashboard.py                      # legge il database in config.yaml
  python dashboard.py --db data/sim.db     # dati di una simulazione
  python dashboard.py --port 8765

Poi apri http://localhost:8765 nel browser. Si aggiorna da sola ogni 5 secondi.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROME = ZoneInfo("Europe/Rome")


def hhmm(ts):
    return datetime.fromtimestamp(ts, ROME).strftime("%d/%m %H:%M:%S") if ts else ""


def rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def state(db_path: str) -> dict:
    if not Path(db_path).exists():
        return {"error": f"Database non trovato: {db_path}. Avvia il bot o una simulazione."}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        snaps = rows(con, "SELECT * FROM snapshots ORDER BY ts DESC LIMIT 1")
        if not snaps:
            return {"error": "Il bot non ha ancora registrato uno stato. Riprova tra un minuto."}
        last = snaps[0]
        hist = rows(con, "SELECT ts, eff_lp, queue, emission FROM snapshots ORDER BY ts")
        step = max(1, len(hist) // 400)              # al massimo ~400 punti nel grafico
        hist = hist[::step] + ([hist[-1]] if hist and hist[-1] is not hist[::step][-1] else [])
        open_pos = rows(con, "SELECT id, strategy, asset, side, margin, leverage, entry, opened_ts, grp, reason "
                             "FROM trades WHERE status='open' ORDER BY id DESC")
        by_strat = rows(con, "SELECT strategy, COUNT(*) n, SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins, "
                             "SUM(pnl) pnl, SUM(paper_minted) paper, SUM(queued_usd) queued "
                             "FROM trades WHERE status='closed' GROUP BY strategy ORDER BY strategy")
        for r in by_strat:
            r["cost_per_paper"] = (-r["pnl"] / r["paper"]) if r["paper"] else None
        trades = rows(con, "SELECT id, strategy, asset, side, margin, leverage, entry, exit, pnl, paper_minted, "
                           "haircut, close_reason, closed_ts FROM trades WHERE status='closed' "
                           "ORDER BY closed_ts DESC LIMIT 30")
        events = rows(con, "SELECT ts, level, msg FROM events WHERE level!='INFO' OR msg NOT LIKE 'Scartato%' "
                           "ORDER BY ts DESC LIMIT 40")
        for t in open_pos:
            t["opened"] = hhmm(t.pop("opened_ts"))
        for t in trades:
            t["closed"] = hhmm(t.pop("closed_ts"))
        for e in events:
            e["time"] = hhmm(e.pop("ts"))
        last["time"] = hhmm(last["ts"])
        return {"last": last, "hist": hist, "open": open_pos, "strategies": by_strat, "trades": trades,
                "events": events}
    finally:
        con.close()


PAGE = r"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Papertrade bot</title>
<style>
:root{--bg:#f6f4ef;--panel:#fff;--ink:#1d1c1a;--mute:#6f6b63;--line:#e3dfd6;
--insolvente:#9b2c2c;--bootstrap:#b7791f;--decadimento:#2b6cb0;--sweep:#276749;--pos:#276749;--neg:#9b2c2c}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--panel:#1d1c1a;--ink:#ecebe7;--mute:#9a968d;--line:#2e2c29;
--insolvente:#e05d5d;--bootstrap:#e2a33b;--decadimento:#5b9be0;--sweep:#4fb37a;--pos:#4fb37a;--neg:#e05d5d}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px}
.hero{border-left:10px solid var(--c);padding:10px 18px;margin-bottom:16px}
.hero .r{font-size:44px;font-weight:800;letter-spacing:-.02em;color:var(--c);line-height:1}
.hero .m{margin-top:6px;color:var(--mute)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:16px}
.k{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.k .l{color:var(--mute);font-size:12px}.k .v{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:16px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 10px}
pre{white-space:pre-wrap;margin:0;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:820px){.two{grid-template-columns:1fr}}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mute);font-weight:600;font-size:12px}
.pos{color:var(--pos)}.neg{color:var(--neg)}.mute{color:var(--mute)}
.lv{font-size:11px;font-weight:700;padding:1px 6px;border-radius:4px;border:1px solid var(--line)}
svg{width:100%;height:180px;display:block}
.foot{color:var(--mute);font-size:12px;text-align:right}
</style></head><body><main>
<div id="app"><p class="mute">Caricamento…</p></div>
<div class="foot" id="foot"></div>
</main>
<script>
const MEANING={INSOLVENTE:"L'LP non copre la coda: i profitti aspettano, le perdite coniano 100 PAPER per $1.",
BOOTSTRAP:"LP sotto $2M: emissione massima, fase di accumulo PAPER.",
DECADIMENTO:"LP tra $2M e $5M: emissione in lento calo, valuta PAPER in base all'APR.",
SWEEP:"LP sopra $5M: ogni dollaro di guadagno extra va agli staker. Staking pieno."};
const $=n=>n==null?"—":"$"+Number(n).toLocaleString("it-IT",{maximumFractionDigits:2});
const num=(n,d=0)=>n==null?"—":Number(n).toLocaleString("it-IT",{maximumFractionDigits:d});
const pct=n=>n==null?"—":(n*100).toFixed(1)+"%";
const cls=n=>n>0?"pos":n<0?"neg":"";
const esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
function table(cols,data){if(!data.length)return '<p class="mute">Nessun dato.</p>';
return '<div class="tw"><table><tr>'+cols.map(c=>'<th>'+c[0]+'</th>').join('')+'</tr>'+
data.map(r=>'<tr>'+cols.map(c=>'<td>'+c[1](r)+'</td>').join('')+'</tr>').join('')+'</table></div>'}
function chart(h){if(h.length<2)return '<p class="mute">Servono più dati per il grafico.</p>';
const W=1000,H=180,P=6,xs=h.map(p=>p.ts),ys=h.flatMap(p=>[p.eff_lp,p.queue]);
const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(0,...ys),y1=Math.max(1,...ys);
const X=t=>P+(t-x0)/(x1-x0||1)*(W-2*P),Y=v=>H-P-(v-y0)/(y1-y0||1)*(H-2*P);
const line=(k,c)=>'<polyline fill="none" stroke="'+c+'" stroke-width="2" points="'+h.map(p=>X(p.ts)+','+Y(p[k])).join(' ')+'"/>';
const zero='<line x1="0" x2="'+W+'" y1="'+Y(0)+'" y2="'+Y(0)+'" stroke="var(--line)"/>';
return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none">'+zero+line('eff_lp','var(--decadimento)')+line('queue','var(--insolvente)')+'</svg>'+
'<div class="mute" style="font-size:12px">— LP effettiva (blu) · — coda (rosso) · max '+$(y1)+'</div>'}
function render(d){const a=document.getElementById('app');
if(d.error){a.innerHTML='<section><p>'+esc(d.error)+'</p></section>';return}
const L=d.last,c='var(--'+L.regime.toLowerCase()+')';
a.innerHTML=
'<div class="hero" style="--c:'+c+'"><div class="r">'+L.regime+'</div><div class="m">'+(MEANING[L.regime]||'')+' · aggiornato '+L.time+'</div></div>'+
'<div class="grid">'+
[['LP effettiva',$(L.eff_lp)],['Coda',$(L.queue)+' <span class="mute" style="font-size:13px">('+num(L.queue_len)+')</span>'],
['Emissione',num(L.emission,1)+' /$'],['Prezzo PAPER',L.paper_price==null?'n/d':'$'+Number(L.paper_price).toFixed(6)],
['APR staking',pct(L.apr)],['PAPER tuoi',num((L.my_paper||0)+(L.my_staked||0))],['Ricompense',$(L.my_rewards)],['In coda per te',$(L.my_queued)]]
.map(k=>'<div class="k"><div class="l">'+k[0]+'</div><div class="v">'+k[1]+'</div></div>').join('')+'</div>'+
'<section><h2>Scheda azioni</h2><pre>'+esc(L.card)+'</pre></section>'+
'<section><h2>LP effettiva e coda</h2>'+chart(d.hist)+'</section>'+
'<div class="two"><section><h2>Posizioni aperte</h2>'+table([['#',r=>r.id],['Strat.',r=>r.strategy],['Asset',r=>r.asset],['Lato',r=>r.side],
['Margine',r=>$(r.margin)],['Leva',r=>num(r.leverage)+'x'],['Entry',r=>num(r.entry,2)],['Dalle',r=>r.opened]],d.open)+'</section>'+
'<section><h2>Risultati per strategia</h2>'+table([['Strat.',r=>r.strategy],['Trade',r=>r.n],['Vinti',r=>r.wins],
['PnL',r=>'<span class="'+cls(r.pnl)+'">'+$(r.pnl)+'</span>'],['PAPER',r=>num(r.paper)],
['$/PAPER',r=>r.cost_per_paper==null?'—':Number(r.cost_per_paper).toFixed(4)],['In coda',r=>$(r.queued)]],d.strategies)+'</section></div>'+
'<section><h2>Ultimi trade chiusi</h2>'+table([['#',r=>r.id],['Ora',r=>r.closed],['Strat.',r=>r.strategy],['Asset',r=>r.asset],['Lato',r=>r.side],
['Leva',r=>num(r.leverage)+'x'],['PnL',r=>'<span class="'+cls(r.pnl)+'">'+$(r.pnl)+'</span>'],['PAPER',r=>num(r.paper_minted)],
['Trattenuta',r=>r.haircut==null?'—':pct(r.haircut)],['Motivo',r=>esc(r.close_reason)]],d.trades)+'</section>'+
'<section><h2>Registro</h2>'+table([['Ora',r=>r.time],['Livello',r=>'<span class="lv">'+r.level+'</span>'],['Messaggio',r=>esc(r.msg)]],d.events)+'</section>';
document.getElementById('foot').textContent='Ultimo aggiornamento pagina: '+new Date().toLocaleTimeString('it-IT',{timeZone:'Europe/Rome'})+' (ora di Roma)';}
async function tick(){try{render(await (await fetch('/api/state')).json())}catch(e){document.getElementById('foot').textContent='Bot o dashboard non raggiungibili: '+e}}
tick();setInterval(tick,5000);
</script></body></html>"""


def make_handler(db_path: str):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/state"):
                body = json.dumps(state(db_path), default=str).encode()
                ctype = "application/json"
            elif self.path in ("/", "/index.html"):
                body, ctype = PAGE.encode(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    return H


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args()
    db = a.db or yaml.safe_load(Path(a.config).read_text())["db_path"]
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(db))
    print(f"Dashboard su http://localhost:{a.port}  (database: {db})  —  Ctrl+C per chiudere")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
