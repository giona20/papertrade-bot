# Papertrade Bot: guida operativa

Questa guida spiega cosa fa il bot, ogni dato che monitora, ogni strategia e la procedura per il giorno del lancio (10 ottobre 2026). Tutti gli orari sono in ora di Roma.

---

## 1. Come funziona il bot

Il bot gira in locale e ha tre parti.

**Monitor.** Legge due fonti di dati:
- Hyperliquid, cioè il BBO (miglior bid e miglior ask) da cui Papertrade calcola il prezzo, più mark price, oracle price e candele a 1 minuto.
- Il contratto Papertrade su HyperEVM: saldo LP, coda FIFO, PAPER emessi e in staking, ricompense.

**Motore strategie.** Classifica il protocollo in un regime, calcola l'EV di ogni strategia e genera gli ordini.

**Esecuzione + rischio.** Ogni ordine passa dai controlli di rischio. Poi viene simulato (dry run) oppure inviato al contratto (live).

Ci sono due cicli:
- **Veloce, ogni 2 secondi.** Controlla le posizioni aperte: take profit, stop, liquidazione, tempo massimo.
- **Lento, ogni 60 secondi.** Aggiorna lo stato del protocollo e il regime, produce la scheda azioni e apre nuove posizioni.

La **scheda azioni** è l'output principale: in ogni momento ti dice in che regime sei e cosa fare su ogni strategia e sul token PAPER.

---

## 2. Installazione e comandi

```bash
pip install -r requirements.txt
python run.py sim --hours 72     # test offline completo, nessun rischio
python run.py run                # avvio con la configurazione in config.yaml
python run.py status             # ultima scheda azioni + posizioni aperte
python dashboard.py              # dashboard locale: http://localhost:8765
python run.py calib show         # curva della trattenuta stimata
python run.py kill               # chiude tutto e ferma il bot
python run.py unkill             # riabilita
```

Le tre modalità principali, impostate in `config.yaml`:

| Uso | mode | market_source | protocol_source |
|---|---|---|---|
| Test offline | dry_run | sim | sim |
| Prima del lancio, prezzi veri | dry_run | hyperliquid | sim |
| Dal 10/10, osservazione | dry_run | hyperliquid | onchain |
| Dal 10/10, ordini veri | live | hyperliquid | onchain |

---

## 3. Le fasi del lancio e cosa fare in ognuna

L'annuncio non specifica il fuso orario. Se è UTC, aggiungi 2 ore per l'ora di Roma.

| Fase | Quando | Cosa succede | Cosa fai tu | Cosa fa il bot |
|---|---|---|---|---|
| 0 Predeposito | dall'08/10 | Solo depositi e creazione account, trading in pausa | Deposita e crea l'account subito: dopo il lancio i depositi avranno priorità bassa. Depositare prima dentro la fase non dà vantaggi | Monitora, scheda "DEPOSITA ORA" |
| 1 Solo frontend | dal 10/10 | Ordini solo da papertrade.xyz tramite relayer whitelisted; contratto chiuso alle chiamate dirette; sistema di intent con priorità per tipo e nozionale; PAPER non trasferibile | Esegui a mano i segnali del bot; annulla gli ordini rimasti in attesa troppo a lungo | Segnali MANUALI su console/Telegram + simulazione dei ritardi |
| 2 Contratto aperto | dopo la congestione (data da annunciare) | Chiunque (umano, contratto, agent) apre posizioni atomiche onchain | Collega l'ABI e passa a `mode: live` | Esecuzione automatica |
| 3 Builder code | dopo | I frontend ricevono l'1% della quota LP. Nessun costo per l'utente | Niente | — |
| 4 PAPER trasferibile | dopo un periodo non precisato | PAPER si può comprare e vendere | Strategia S4 completa | Usa il prezzo di mercato |

**Cosa implica la fase 1 per le strategie**
- **Ritardi.** Gli ordini piccoli (nozionale basso) vengono confermati per ultimi. Meglio pochi ordini con nozionale più alto. Alza il margine, non la leva: la leva alta peggiora la trattenuta.
- **Priorità asimmetrica.** Le liquidazioni hanno priorità sulle altre operazioni. Un take profit o uno stop richiesto da te può aspettare, mentre una liquidazione passa subito. In fase 1 conviene una distanza di liquidazione più ampia (leva più bassa).
- **Ordini annullabili.** Un ordine non ancora eseguito si può annullare. Se il prezzo è già andato oltre il tuo TP mentre aspetti, valuta di annullare l'apertura.
- **PAPER non vendibile.** Il suo valore viene solo dalle ricompense di staking. Il bot calcola un valore implicito e lo usa per S3 con uno sconto del 50%.
- **No a scorciatoie.** Il bot non automatizza il frontend in fase 1. Il protocollo vuole che i primi partecipanti siano utenti reali del frontend, e aggirarlo rischia esclusioni.

---

## 3b. Wallet e capitale (budget $1.000)

| Wallet | Ruolo | Deposito | Perché |
|---|---|---|---|
| A | Corsa in modalità burn | parte dei $500 | Usato solo se il bot sceglie il burn (un lato per asset) |
| B | Lato long | $250 + metà budget corsa | Gamba long delle coppie, long di S1/S3, gamba long di S2 |
| C | Lato short | $250 + metà budget corsa | Gamba short delle coppie, short di S1/S3, gamba short di S2 |
| Riserva | — | $0 sul sito | Non collegarla mai |

**Perché quattro e non tre.** Nello stesso account non puoi avere long e short sullo stesso asset. Lo straddle di S2 ha quindi bisogno di due account.
- Il bot assegna ogni ordine al wallet giusto e scrive il wallet nel segnale.
- Scarta gli ordini che metterebbero lati opposti sullo stesso asset nello stesso wallet.

**PAPER resta dove viene coniato.** Fino alla fase 4 non è trasferibile, quindi non esiste un "wallet PAPER" separato: fai staking da ogni wallet che ne conia.

**$500 per il trading bastano?** Sì, per questo uso. Il trading qui non ha un edge da scalare: il capitale serve ad avere nozionale sufficiente per la priorità del relayer.
- S1/S3: $15 di margine a circa 90x danno circa $1.350 di nozionale.
- S2: $20 per gamba.
- Con $250 per wallet hai spazio per 3–4 posizioni e uno stop giornaliero di $100.
- Aumentare il capitale ha senso solo se il protocollo mostra un EV positivo dopo la calibrazione.

**Nozionale** = valore della posizione = margine × leva. Per esempio $10 a 1000x = $10.000. La priorità del relayer dipende dal nozionale, non dal margine.

---

## 3c. Checklist cronologica

Le priorità sono in ordine: se il tempo stringe, fai i punti in quest'ordine.

**Fino al 7/10**
1. Crea i wallet A, B, C. Metti gli USDC sul conto spot Hyperliquid di ciascuno: il deposito è un invio di USDC spot all'indirizzo personale mostrato dal sito. Tieni un po' di HYPE su HyperEVM solo per i prelievi (`withdrawToCore`).
2. Esegui `python run.py sim` e leggi il report.
3. Configura Telegram (`alerts`). Imposta i budget in `wallets` e `rush.budget_usd`.
4. Inserisci gli eventi della settimana in `events.yaml` (ora di Roma).
5. Avvia `run` con prezzi reali e LP simulata per qualche ora, per controllare connessione, spread e segnali.

**8/10, fase 0 (la priorità assoluta)**
1. Su papertrade.xyz, per A, B e C: clicca Deposit e invia gli USDC all'indirizzo personale.
   - Usa un invio spot da Hyperliquid (o il percorso guidato). Un trasferimento ERC-20 diretto al proxy su HyperEVM NON viene rilevato.
   - Il saldo arriva in pochi blocchi.
2. Registra la **session key** per ogni wallet, se il sito la propone. Poi firmi gli ordini senza popup, e il 10/10 ogni secondo conta.
3. Cerca gli indirizzi dei contratti (Exchange, PaperStaking, PaperTokenomics) nei docs o nell'explorer di HyperEVM. Se sono verificati, scarica le ABI in `abi/` e mappa in `onchain.functions`:
   - le letture di stato: treasury/LP, sideBucket, coda, tailProgressUsd, supply, staking, i tuoi saldi;
   - se esistono, i parametri della curva (baseRate, rateMultiplier, positionMultiplier, referenceNotional). Con questi la trattenuta è esatta da subito, senza calibrare.
4. Inserisci il tuo indirizzo in `onchain.my_address`. Imposta `protocol_source: onchain` e `mode: dry_run`. Avvia `run` e verifica: LP = 0, emissione 100, saldi corretti.
5. Se le ABI non ci sono, il bot resta su LP simulata e prezzi reali. I segnali funzionano lo stesso.

**9/10**
1. Ricontrolla il budget della corsa: è denaro che consideri speso.
2. Fai un test completo dell'avvio.

**10/10, 30 minuti prima del lancio** (00:00 ufficiali; se UTC sono le 02:00 a Roma)
1. Avvia il bot e la dashboard.
2. Apri papertrade.xyz con i tre wallet collegati, in finestre o profili separati.

**Regole operative dai docs**
- Mercati al lancio: solo BTC ed ETH, fino a 1000x. Tetto per utente: $10M.
- Ogni posizione ha margine fisso. Non si aggiunge margine, non si chiude in parte. Per aumentare, apri una seconda posizione; per ridurre, chiudi e riapri più piccola.
- Una transazione può chiudere fino a 25 posizioni insieme.
- Ogni ordine firmato vale 1 ora: se resta in attesa più a lungo, scade. La session key dura 7 giorni.

**Al lancio: la corsa (circa 1 ora)**
1. Esegui i segnali APRI del wallet A così come arrivano.
2. Esegui gli ANNULLA sugli ordini ancora in attesa.
3. Fai STAKE dei PAPER coniati appena possibile.

**Dopo la corsa: calibrazione (circa 1–2 ore)**
1. Solo se i parametri della curva non sono leggibili: con B o C fai 3 trade e chiudili in profitto a distanze diverse (circa 0,3%, 1%, 2%).
2. Registra ogni chiusura in profitto (vedi 3d). Con tre punti il bot ricava la curva intera.
4. Poi lascia lavorare S1, S2 e S3 con i segnali.

**Fase 2** (quando annunciata): collega `LiveExecutor` e passa a `mode: live`.

**Fase 4** (quando annunciata): compila `phases.transferable_time`. Da lì si attivano i segnali di vendita di PAPER.

---

## 3d. Come registrare la trattenuta

La formula ufficiale ha due parametri effettivi:

**trattenuta h = 1 − (1 − b) × m / (m + K)**, con m = movimento − 0,2 bps (deadband anti-jitter).

Il bot stima b e K dalle chiusure in profitto che registri.

Per ogni chiusura in profitto annota:
- **Entry** ed **exit**: i prezzi mostrati dal frontend, cioè il BBO mid.
- **Margine** e **leva**.
- **Ricevuto**: profitto accreditato, margine escluso.

Poi calcola:
- **move** = (exit / entry − 1) per un long, (entry / exit − 1) per uno short.
- **gross** = margine × leva × move.

**Esempio.** Long BTC, entry 100.000, exit 101.000 (move 0,01), margine $15, leva 95x.
- gross = 15 × 95 × 0,01 = $14,25.
- Ricevuti $12,10.

```bash
python run.py calib add --move 0.01 --gross 14.25 --net 12.10
python run.py calib show
```

Trattenuta = 1 − 12,10 / 14,25 = 15%. Non c'è nessuna fee da separare: l'utente non paga altro.

---

## 3e. Fee: confermato, l'utente non paga

Dai docs:
- **Vincite:** l'unico costo è la trattenuta (asymmetric impact) sul profitto realizzato. Non dipende dalla dimensione della posizione.
- **Perdite:** paghi solo la perdita. La fee del 2% sulle perdite è presa dal guadagno dell'LP, non da te. Ha due effetti:
  - Se la coda è vuota, il PAPER si conia sulla perdita − 2%.
  - Se la coda è attiva, la fee non si applica e si conia sulla perdita intera.
- **Liquidazioni:** il PAPER si conia sempre sul margine intero.
- **Builder code 1%:** esce dalla quota dell'LP, invisibile per l'utente.
- **Gas:** nessuno sui trade. Serve solo per prelevare in autonomia.

---

## 4. I dati monitorati, uno per uno

### Dati del protocollo

**Saldo LP (lp_usd)**
- Gli USDC nel pool che fa da controparte a tutti i trade.
- Parte da $0 e cresce solo con le perdite dei trader.

**Coda (queue_usd, queue_len)**
- I profitti dovuti ai vincitori che l'LP non ha potuto pagare subito, ordinati FIFO: chi entra prima viene pagato prima.
- Il margine del vincitore torna sempre subito: in coda va solo il profitto netto.

**LP effettiva = treasury + sideBucket − coda**
- È la "net equity" dei docs. Il sideBucket raccoglie le perdite incassate mentre la coda è attiva; serve a pagare la coda tramite harvest.
- È il numero che decide il regime.
- Un LP di $1M con $1,2M di debiti in coda è di fatto insolvente.

**Emissione (PAPER per $1)**
- 100 fissi finché l'LP tracciata è sotto $2M, anche se negativa.
- Sopra: 100 × (120M / (120M + H))², dove H è il massimo storico del guadagno LP cumulato oltre $2M.
- Il decadimento è lento: circa 95/$ con $3M di guadagno oltre soglia, 88/$ con $8M, 70/$ con $23M, 51/$ con $48M.
- H non scende mai: lo sweep agli staker non ripristina l'emissione.

**Supply e PAPER in staking**
- Servono a calcolare quanta parte dei flussi spetta a ogni token in staking.

**Ricompense staker 24h**
- USDC distribuiti agli staker nelle ultime 24 ore. Arrivano da due fonti:
  - una quota della trattenuta sui profitti (sempre);
  - il 100% del guadagno LP sopra $5M (lo "sweep").

**Prezzo PAPER**
- Disponibile solo quando PAPER è quotato su un mercato secondario.
- Prima di allora vale "n/d" e le strategie che ne dipendono restano spente.

**APR staking = ricompense 24h × 365 / (PAPER in staking × prezzo)**
- Il rendimento annualizzato di chi fa staking al prezzo attuale.

**In coda a tuo favore (my_queued_usd)**
- I tuoi profitti in attesa.

### Dati di mercato (Hyperliquid)

**BBO mid**
- Media tra miglior bid e miglior ask su Hyperliquid. È il prezzo a cui Papertrade apre e chiude le posizioni, senza slippage.

**Spread (bps)**
- Distanza bid-ask in punti base (1 bps = 0,01%).
- Spread largo significa mid poco affidabile: il bot non apre sopra `max_spread_bps`.

**Divergenza oracle (bps)**
- Distanza tra mid e oracle price di Hyperliquid.
- Se è alta, il book è anomalo o manipolato: il bot non apre.

**Volatilità a 1 minuto**
- Deviazione standard dei rendimenti a 1 minuto sugli ultimi 60 minuti.
- Serve per la regola del rumore: la distanza di liquidazione deve essere più ampia del movimento "normale" atteso.

**Range di breakout**
- Massimo e minimo degli ultimi N minuti. Rompere il massimo dà long, rompere il minimo dà short.

### Dati di posizione

**Distanza di liquidazione = 1 / leva − 0,05%**
- Dai docs: la liquidazione scatta circa 5 bps prima del prezzo di azzeramento.
- A 1000x è circa 0,05%, a 100x circa 0,95%.
- È un "hard bust": oltre quel prezzo perdi tutto il margine, anche se una chiusura volontaria un attimo prima ti avrebbe restituito qualcosa.

**Trattenuta (haircut)**
- La percentuale del profitto lordo che il protocollo trattiene.
- È più alta sui movimenti piccoli e più bassa su quelli grandi.
- Il bot la misura sui trade reali e aggiorna la curva.

---

## 5. I regimi

| Regime | Condizione | Significato | Postura del bot |
|---|---|---|---|
| INSOLVENTE | coda > 0 o LP effettiva ≤ 0 | I profitti aspettano, le perdite coniano 100/$ | S1 attiva, stake dei PAPER ricevuti, nessun acquisto |
| BOOTSTRAP | LP effettiva < $2M | Emissioni massime | S1 attiva, stake dei PAPER ricevuti, nessun acquisto |
| DECADIMENTO | $2M – $5M | Emissioni in calo, staker solo su trattenute | Compra PAPER se APR ≥ soglia |
| SWEEP | ≥ $5M | Tutto il guadagno extra va agli staker | Staking pieno |

**DRENAGGIO** è un flag aggiuntivo, non un regime. Si accende quando l'LP effettiva, dopo aver superato $2M, scende più del 30% dal massimo. È il segnale del rischio principale: una balena drena l'LP, le emissioni risalgono, il prezzo di PAPER scende e i trader se ne vanno.

---

## 6. La formula chiave

Prendi un trade con take profit e stop alla stessa distanza d (quindi circa 50% di probabilità di vincere o perdere, senza edge). L'EV per $1 di margine è:

**EV ≈ 0,5 × (E × P_eff − 1 + q × G × (1 − h(d)))**

- **E** = PAPER coniati per $1 perso (100 sotto $2M).
- **P_eff** = prezzo PAPER × (1 − sconto di liquidità).
- **q** = probabilità che la coda paghi (in INSOLVENTE < 1).
- **h(d)** = trattenuta a quella distanza.
- **G** = profitto lordo per $ di margine al TP = leva × d. Con leva = 1 / (d + 0,05%), G ≈ 0,95 a d = 1%.
- Il saldo in coda è usabile come collaterale per nuove aperture: per questo q è alto anche in INSOLVENTE (0,9).

**Esempio 1: fase 1.** Dati: margine $5, d = 1%, leva 95x, h = 15%, q = 0,95.
- Senza contare PAPER: EV = 0,5 × (−1 + 0,95 × 0,952 × 0,85) ≈ −0,116 per $.
- Circa −$0,58 a trade, in media.
- In media conii 250 PAPER a trade.
- Costo: circa $0,0023 per PAPER.
- Con una liquidazione voluta costa $0,01 per PAPER: il trade simmetrico è circa 4 volte più conveniente, quando la congestione permette di chiudere in profitto.

**Esempio 2: soglia di pareggio.**
- E × P_eff = 0,231, quindi P_eff ≈ $0,0023.
- In fase 4 (sconto di liquidità 30%): PAPER sopra circa $0,0033.
- In fase 1 (valore implicito con sconto 50%): valore implicito sopra circa $0,0046.

**Nota.** La soglia di $0,01 vale solo per chi perde apposta. Con trade simmetrici le vincite coprono gran parte del costo.

**Perché il bot non usa 1000x di default.** La trattenuta è alta sui movimenti piccoli. A 1000x la distanza è circa 0,09% e h sarebbe intorno al 45–50%, quindi l'EV è molto peggiore. Il bot sceglie la distanza più piccola con h ≤ `target_max` (15%) e ricava la leva come 0,9 / d. Se la calibrazione mostra trattenute basse anche su movimenti piccoli, la leva sale da sola.

---

## 7. Le strategie

### S0: corsa di lancio (prima ora circa)

**Cosa succede.** All'apertura la folla si liquida apposta per coniare PAPER a 100 per $1.
- Sotto congestione è la mossa razionale: basta che l'apertura venga confermata, poi la liquidazione è automatica e ha la priorità più alta.
- Un trade simmetrico invece ha bisogno di una chiusura in profitto, che resta in attesa.
- Ipotesi di base: corsa forte, circa un'ora di congestione pesante.
- **Correzione con la curva reale.** Superare i $2M non chiude la finestra: l'emissione scende lentamente (95/$ con circa $3M oltre soglia). La corsa finisce quando l'emissione scende sotto `rush.min_emission` (95/$) o dopo `max_minutes`.
- **Costo.** La liquidazione voluta costa $0,01 per PAPER contro circa $0,0023 di un trade simmetrico. Ha senso solo finché la congestione blocca le chiusure in profitto, e per avere PAPER in staking mentre la folla, perdendo, porta l'LP verso lo sweep dei $5M.

**Cosa fa il bot.**
- Si attiva da sola all'inizio della fase 1 e aggiorna lo stato ogni 10 secondi.
- Misura la velocità di crescita dell'LP e stima l'emissione futura.
- Mette in pausa S1 e S3.
- Sceglie tra due modalità (`rush.mode: auto`).

**COPPIE (default).** Stesso asset, stessa size, long sul wallet B e short sul wallet C, insieme.
- La distanza di liquidazione è il movimento atteso in circa 15 minuti (minimo 0,10%), quindi la leva è di solito 300–600x.
- Quando il prezzo si muove, una gamba si liquida e conia PAPER sul margine intero. L'altra è in profitto della stessa distanza e chiude al TP; se la chiusura tarda e il prezzo torna indietro, si liquida anche lei e conia altro PAPER.
- In simulazione: circa $0,004 per PAPER, stabile.

**BURN.** Liquidazioni singole sul wallet A a 1000x, senza TP.
- Conia più PAPER per dollaro di budget, ma il risultato è una lotteria. Una posizione che va nel verso giusto e non viene chiusa può vincere molto; in simulazione si va da +$950 a −$500 con lo stesso budget.
- In `auto` il bot passa al burn solo se stima che l'emissione calerà di oltre il 30% nel tempo di un ciclo coppia. Con la curva reale (S = $120M) succede solo con una corsa enorme.

**Lato opposto alla folla.** Se il contratto espone l'OI long e short per asset (`oi_long`/`oi_short`), S1, S3 e il burn aprono dal lato opposto alla folla quando un lato è almeno 1,5 volte l'altro.
- Se la folla vince, l'LP va sotto e la coda si allunga: tu perdi quando l'emissione è al massimo.
- Se la folla perde, l'LP si riempie: le tue vincite vengono pagate subito.

**Segnali.**
- **APRI:** ordini della corsa, con wallet e lato indicati.
- **ANNULLA:** quando la fine della corsa (soglia di emissione o tempo massimo) arriverebbe prima della conferma prevista dell'ordine più `cancel_buffer_s`.
- **STAKE subito:** i PAPER ricevuti vanno messi in staking appena possibile.
- **CORSA FINITA:** emissione sotto 95/$ o tempo massimo superato. Da lì valgono le strategie normali.
- **Limiti di OI.** Ogni strumento ha un tetto separato per lato, circa $5M di spazio sopra l'OI attuale. Se un'apertura fallisce per il tetto, passa all'altro asset.

**Budget.** `rush.budget_usd` è la cifra totale che accetti di perdere nella corsa. Va decisa prima. Con le coppie servono fondi su B e C, quindi sposta lì gran parte del budget e tieni su A solo una riserva per il burn. Le perdite della corsa non contano nello stop giornaliero, perché sono un acquisto deciso a priori.

**Come valutarla.** La scheda mostra la valutazione al conio: supply × $0,01. Esempio: se la corsa conia 200M PAPER, l'intera supply è stata "comprata" per $2M. Per un rendimento del 100% servono $2M l'anno agli staker. È sensato solo se il protocollo mantiene volume dopo il lancio.

**Il vero vantaggio di entrare presto.** Non è il tasso di emissione: sotto i $2M è uguale per tutti. È lo staking. Se la corsa porta l'LP oltre $5M, ogni dollaro perso dopo va agli staker, e chi ha messo in staking per primo incassa le perdite di chi arriva dopo.

### S1: finestra di lancio
- **Quando:** primi 7 giorni, regimi INSOLVENTE o BOOTSTRAP, nessun drenaggio.
- **Cosa fa:** apre una posizione piccola (default $5) quando il prezzo rompe il range degli ultimi 30 minuti. TP e SL sono alla stessa distanza d, scelta dalla curva della trattenuta; la leva è 0,9 / d. Al massimo una posizione per asset.
- **Perché:**
  - Se perdi, prendi il massimo dei PAPER.
  - Se vinci, riprendi il margine e ti metti presto in coda. I primi in coda vengono pagati dalle prime perdite degli altri.
- **Rischi:** il costo atteso è circa h/2 del margine a trade. I profitti possono restare in coda a lungo.

### S2: eventi (straddle)
- **Quando:** gli eventi che inserisci in `events.yaml` (CPI, Fed, dati sul lavoro), da 30 minuti prima a 45 minuti dopo.
- **Cosa fa:** apre long e short insieme sullo stesso asset.
  - La liquidazione è al 35% del movimento atteso.
  - Il TP della gamba vincente è al 120% del movimento atteso.
- **Esempio:** movimento atteso 0,60%, liquidazione 0,21%, leva circa 428x, $10 per gamba.
  - BTC si muove dello 0,8%: lo short si liquida (−$10, +1.000 PAPER) e il long chiude allo 0,72% (lordo circa $31, netto circa $25). Risultato: circa +$15 più 1.000 PAPER.
  - Il prezzo oscilla di ±0,25%: entrambe le gambe si liquidano (−$20, +2.000 PAPER).
- **Perché:** senza funding tenere costa zero, e la gamba perdente conia PAPER.
- **Attenzione:** verifica al lancio se il protocollo compensa long e short sullo stesso account. Se sì, servono due wallet (uno per gamba).

### S3: farming continuo di PAPER
Due modalità (`strategies.farming.mode`).

**price (default).** Il bot conia finché il costo stimato per PAPER resta sotto `max_cost_per_paper` (default $0,003).
- Il costo si calcola come (1 − q·G·(1−h)) / E, con la curva di trattenuta calibrata, l'emissione attuale e il rischio coda.
- Il valore di PAPER lo decidi tu fissando il prezzo massimo che sei disposto a pagare.
- Prima della fase 4 un prezzo di mercato non esiste, e il valore implicito dalle ricompense è troppo instabile per guidare le decisioni.

**ev.** Si attiva solo se l'EV, calcolato con il valore PAPER stimato, supera `min_ev_per_margin`.
- Il valore implicito (prima della fase 4) è diluito: ricompense annue divise per la supply attesa tra 90 giorni.
- È molto prudente.

**Coppie (`use_pairs: true`).** S3 apre long su B e short su C insieme. Il costo atteso per PAPER è lo stesso di un trade singolo, ma ogni ciclo conia di sicuro e la varianza è molto più bassa.

**Drenaggio (`thresholds.drain_mode`).**
- **opportunistic (default):** se una balena riporta l'LP sotto $2M, l'emissione torna a 100/$ (dai docs: sotto soglia vale sempre il tasso pieno). Il bot continua a coniare e blocca solo gli acquisti di PAPER.
- **defensive:** spegne S1 e S3 durante il drenaggio.

### S4: gestione PAPER (scheda azioni)

| Situazione | Azione |
|---|---|
| Fasi 1–3 (non trasferibile) | Nessun acquisto o vendita possibile: stake di tutto ciò che coni, il valore sono gli USDC di staking |
| INSOLVENTE / BOOTSTRAP | Non comprare (offerta in forte crescita), stake dei PAPER ricevuti |
| DECADIMENTO | Compra e stake se APR ≥ `target_apr` (30%), altrimenti aspetta |
| SWEEP | Stake di tutto |
| Ricompense ≥ $5 | Claim |

### S5: difesa (drenaggio)
Quando il flag DRENAGGIO è acceso:
- S1 e S3 si spengono.
- La scheda dice di non comprare PAPER e di alleggerire quelli liberi.
- Resta attiva solo S2, sugli eventi.

---

### S6: chiusura anticipata contro la coda (queue guard)

**Lo scopo.** I profitti si pagano solo se l'LP ha fondi. Se chiudi quando l'LP è già vuota, il profitto va in coda FIFO. Il bot controlla ogni ciclo i trade in profitto (netti della trattenuta, sopra $1) e manda **CHIUDI ORA (anticipo coda)** in tre casi:

1. **Copertura bassa.** L'LP effettiva è meno di 3 volte i profitti aperti. Se il contratto espone il PnL aperto di tutti i trader (`open_pnl_total`), il bot usa quello, che è molto più affidabile dei soli tuoi.
2. **LP in calo veloce.** Al ritmo attuale, l'LP non coprirebbe più i profitti prima che la chiusura venga confermata dal relayer (più 2 minuti di margine). In fase 1 le chiusure aspettano, quindi il segnale deve arrivare prima.
3. **Coda già attiva e in crescita.** Chiudere subito ti mette prima in fila, perché la FIFO paga chi entra prima. Vale solo per i trade già oltre metà strada dal TP: chiudere profitti minuscoli costerebbe troppo in trattenuta.

**Ricorda:** il margine torna sempre, rischi solo il profitto. Con posizioni piccole il rischio reale è concentrato nei primi minuti (LP quasi a zero) e nei momenti in cui una balena incassa grosso.

### Quando vendere PAPER (solo dalla fase 4)

**Il principio.** PAPER vale i flussi in USDC che paga agli staker. L'APR calcolato al prezzo di mercato ti dice se il prezzo è caro o economico rispetto a quei flussi.

| Condizione | Azione |
|---|---|
| APR ≥ 30% | Tieni e accumula (in DECADIMENTO/SWEEP) |
| APR tra 15% e 30% | Tieni, non comprare |
| APR < 20% e c'è un periodo di unstake | PREPARA: avvia l'unstake di metà |
| APR < 15% | VENDI metà: il prezzo sconta più dei flussi reali |
| Prezzo ≥ 2× il tuo costo medio | RECUPERA CAPITALE: vendi quanto serve a riavere ciò che hai speso, tieni il resto in staking a costo zero |
| DRENAGGIO | Riduci i PAPER liberi, non comprare |

**Il costo medio** è calcolato dal bot: perdite totali divise per i PAPER coniati.

**Staking.** Dai docs: stake e unstake istantanei, senza cooldown, lockup o minimi. Le ricompense in USDC maturano di continuo e si ritirano con un claim, che le accredita sul saldo di trading. Vendere quindi non richiede preparazione: basta l'unstake quando arriva il segnale.

### S7: riciclo del saldo in coda
Dai docs: il saldo in coda si può usare come margine. Una perdita finanziata con il credito in coda conia PAPER sulla parte di credito distrutta. Quando hai almeno $20 in coda e la coda è lunga (5 o più posizioni), il bot segnala **RICICLA CODA**: usa quel saldo per S1/S3. Un credito lento da incassare si trasforma in PAPER al tasso pieno, oppure in altro credito se vinci.

## 8. Rischio e kill switch

Ogni ordine viene scartato se una di queste condizioni è vera:
- kill switch attivo (file `KILL`);
- perdita del giorno oltre `daily_loss_cap_usd` (giornata in ora di Roma);
- posizioni aperte al massimo;
- margine oltre il limite per trade o budget esaurito;
- spread o divergenza oracle oltre soglia, oppure dati più vecchi di 30 secondi.

Alert da tenere d'occhio: cambio regime, drenaggio, coda cresciuta oltre il 50% in un'ora, errori di rete. Arrivano in console e, se configurato, su Telegram.

---

## 9. Parametri principali (config.yaml)

| Parametro | Cosa controlla |
|---|---|
| haircut.target_max | Trattenuta massima accettata: più bassa significa distanza più ampia, leva minore, trade più lunghi |
| leverage.liq_buffer | Anticipo della liquidazione rispetto all'azzeramento (5 bps dai docs) |
| leverage.noise_multiple | Quanto la liquidazione deve superare il rumore a 15 minuti |
| paper.queue_pay_prob | Fiducia nel pagamento della coda per regime |
| paper.liquidity_discount | Sconto sul prezzo PAPER per slippage in vendita |
| strategies.*.margin_usd | Margine per trade di ogni strategia |
| thresholds.drain_drop_pct | Sensibilità del flag drenaggio |

---

## 10. Cosa va collegato al lancio

1. Indirizzo, ABI e funzioni di lettura del contratto (`onchain`). Servono già in fase 1 per leggere LP, coda e PAPER.
2. Le funzioni di apertura e chiusura per `LiveExecutor`. Utilizzabili solo dalla fase 2.
3. Se leggibili, i parametri della curva di trattenuta per strumento (trattenuta esatta senza calibrare).
4. Le date delle fasi 2 e 4 (`phases`), appena annunciate.
5. La formula reale di decadimento delle emissioni e la quota staker della trattenuta. Ora sono ipotesi marcate nel codice.

## 11. Limiti onesti

- Nessuna strategia qui ha un edge direzionale dimostrato: l'EV positivo dipende dal valore di PAPER.
- I numeri della simulazione dipendono da ipotesi (comportamento della folla, prezzo PAPER). Servono a testare la logica, non a prevedere i guadagni.
- Il protocollo è nuovo, senza audit noto. Il rischio del contratto è reale.
- **Manipolazione del BBO.** È il rischio principale secondo i docs: non c'è un controllo di deviazione sul prezzo. Ordini che spostano il bid o l'ask possono falsare il mid per un blocco. I controlli del bot su spread e divergenza dall'oracle servono proprio a questo.
- **Pausa e ritiro dei mercati.** Il guardian può ritirare un mercato: le posizioni restano chiudibili solo al BBO congelato del momento del ritiro. C'è anche una pausa globale delle aperture.
- **Contratti aggiornabili.** I contratti sono proxy con timelock: l'owner può modificare i parametri (curva, fee) e aggiornare l'implementazione. Le modifiche sono visibili prima dell'esecuzione: controllale.
- A leva alta, anche un solo bug di prezzo può azzerare una posizione. Non tenere fondi fermi sul contratto.
