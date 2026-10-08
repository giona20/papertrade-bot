# Thread X (versione tecnica) — ogni post ≤ 280 caratteri

**1/**

Papertrade.xyz va live il 10/10 su Hyperliquid (HyperEVM).

Perp sintetici fino a 1000x, zero funding, zero slippage, nessun orderbook. Prezzo = BBO mid di Hyperliquid.

La vera opportunità non è il trading direzionale. Thread tecnico con tutte le strategie 🧵

---

**2/**

Architettura:

Nessun orderbook. Ogni trade è uno swap sintetico contro l'LP, unica controparte.

Perdi → il margine va all'LP.
Vinci → l'LP paga il profitto meno un haircut.

Unico costo: l'haircut sui profitti. Nessuna fee, nessun gas. Modello "house", tipo GMX v1.

---

**3/**

PAPER è il fee-claim token dell'LP.

Si conia solo perdendo: 100 PAPER per $1 finché l'LP è sotto $2M (anche se negativa). Poi decade lentamente: ~88/$ a +$8M.

Fair launch: zero pre-mint, zero team/VC, zero airdrop. Supply iniziale = 0.

---

**4/**

L'LP parte da $0: al lancio il protocollo è insolvente by design.

Se vinci e l'LP non copre:
✅ il margine torna subito
⏳ il profitto netto entra in una coda FIFO

La coda viene pagata dalle perdite successive degli altri trader.

---

**5/**

Revenue per gli staker di PAPER, in USDC:

1. Una quota dell'haircut su ogni profitto
2. Sopra $5M di LP: 100% del guadagno LP extra (sweep)

Sotto $5M lo staking rende poco. Sopra $5M diventa il trade principale.

---

**6/**

L'haircut è regressivo:

movimento piccolo → haircut alto
movimento grande → haircut basso

Conseguenza: scalping e micro-TP sono EV negativi. A 1000x (liquidazione ~0,05%) l'haircut è pesante. Spesso conviene una leva più bassa con TP più ampi.

---

**7/**

La formula chiave, per un trade con TP = SL:

EV/$ ≈ 0,5 × (E×P − 1 + q×(1−h))

E = PAPER per $ perso
P = prezzo PAPER
q = probabilità che la coda paghi
h = haircut alla distanza scelta

Il valore di PAPER decide se il trade è EV+.

---

**8/**

Esempio: margine $5, distanza 1%, leva 95x, haircut 15%, q 0,95.

Costo atteso ≈ $0,58 a trade, circa 250 PAPER coniati in media.

= PAPER a ~$0,0023, contro $0,01 con una liquidazione voluta. Sopra ~$0,0033 di prezzo il farming simmetrico è EV+.

---

**9/**

Calendario di lancio:

08/10 – Fase 0: solo depositi + creazione account, trading in pausa
10/10 – Fase 1: trading live solo dal frontend, tramite relayer whitelisted

Predeposita: al lancio i depositi avranno priorità bassa. Prima della fase 0 non serve.

---

**10/**

Fase 1 – sistema di intent:

Ordini in coda con priorità per tipo e nozionale, mai per account.
Le liquidazioni passano prima delle aperture.
Nozionale piccolo = conferme lente. Gli ordini pending sono annullabili.

PAPER: solo stake/unstake, niente transfer.

---

**11/**

Dopo:

Fase 2 – contratto aperto: umani, contratti e agent aprono posizioni atomiche onchain
Fase 3 – builder code: 1% della quota LP ai frontend, zero costi per l'utente
Fase 4 – PAPER trasferibile

Fino alla fase 4 il valore di PAPER = solo flussi di staking.

---

**12/**

Cosa cambia in fase 1:

- Pochi ordini con nozionale più alto (alza il margine, non la leva)
- Distanza di liquidazione ampia: un TP può aspettare, una liquidazione no
- Annulla le aperture rimaste pending se il prezzo è già scappato

---

**13/**

Sicurezza:

- Unico dominio: papertrade.xyz
- Niente whitelist, niente airdrop, niente stealth launch
- PAPER non esiste prima del lancio

Qualsiasi token "papertrade" quotato prima del 10/10 è falso.

---

**14/**

Setup:

1. Wallet trading: solo capitale che accetti di perdere al 100%
2. Wallet PAPER: staking
3. Wallet riserva: non interagisce mai col contratto

Niente fondi idle sul contratto. Profitti ritirati a fine sessione.

---

**15/**

Calibrazione prima di tutto:

6-10 trade minimi, chiusi in profitto a distanze diverse (0,2% / 0,5% / 1% / 2%).

haircut = 1 − profitto ricevuto / profitto atteso al BBO mid

Così ricavi la curva reale e scegli distanza e leva ottimali.

---

**16/**

S1 – Finestra di lancio (LP < $2M)

Posizioni piccole, TP = SL nella zona di haircut basso, ingresso su breakout.

Perdi → massimo PAPER per $.
Vinci → margine indietro e posizione alta nella coda FIFO.

Size piccola, tante sessioni.

---

**17/**

S2 – Straddle sugli eventi (CPI, FOMC, NFP)

Long + short insieme, 30 min prima.
Liquidazione al ~35% del movimento atteso, TP della gamba vincente oltre il movimento atteso.

La gamba perdente conia PAPER, zero funding. Whipsaw = entrambe liquidate.

---

**18/**

S3 – Farming EV+

Si attiva solo quando PAPER ha un prezzo di mercato e la formula dà EV sopra soglia.

Si spegne da sola quando le emissioni decadono (LP > $2M) o il prezzo di PAPER scende.

Niente perdite deliberate "alla cieca".

---

**19/**

S4 – Gestione PAPER

Fasi 1-3: non trasferibile → solo stake di quelli coniati.
Fase 4, per regime:
- LP < $2M: non comprare
- $2M – $5M: compra + stake se l'APR supera la soglia
- > $5M: stake pieno

Claim periodico degli USDC.

---

**20/**

S5 – Difesa

Rischio principale: una balena con edge drena l'LP → le emissioni risalgono → PAPER perde valore → i trader escono.

Se l'LP scende >30% dal massimo dopo aver superato $2M: stop farming, niente acquisti di PAPER, riduci i liberi.

---

**21/**

Metriche da monitorare:

- LP effettiva (LP − coda)
- Lunghezza e crescita della coda
- Emissione PAPER/$
- Prezzo PAPER e APR di staking
- Spread BBO e divergenza mid/oracle su Hyperliquid

Il regime decide la strategia, non il feeling.

---

**22/**

Rischi:

- Contratto nuovo, audit non noto
- Coda FIFO: i profitti possono restare bloccati a lungo
- Curva dell'haircut e formula di decadimento non pubbliche
- Rischio regolamentare

Size sempre da capitale a perdere.

---

**23/**

TL;DR

1. Predeposito 08/10, live 10/10, solo papertrade.xyz
2. Calibra l'haircut prima di scalare
3. Lancio: posizioni piccole, accumula PAPER
4. Eventi: straddle, zero funding
5. PAPER: compra tra $2M e $5M se l'APR regge, stake pieno sopra $5M

Stai dalla parte dell'LP.

---

