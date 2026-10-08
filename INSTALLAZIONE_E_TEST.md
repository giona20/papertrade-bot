# Installazione e test del Papertrade Bot

Questa guida porta il bot da zero a pronto per il lancio, con i test da fare in ordine. GUIDA.md spiega le strategie; qui ci sono solo i passi pratici. Orari in ora di Roma.

---

## Parte 1: installazione

### 1.1 Requisiti
- Python 3.11 o superiore. Verifica con `python3 --version` (su Windows: `python --version`).
- Un computer che resti acceso durante il lancio, connesso stabilmente.

### 1.2 Installa

**Windows, il modo più semplice:** estrai lo zip in una cartella nuova e fai doppio clic su `setup.bat`. Crea l'ambiente virtuale, installa le dipendenze e fa un test.

Da lì in poi usa:
- `simulazione.bat`
- `avvia_bot.bat`
- `dashboard.bat`
- `comando.bat`, per i comandi da terminale.

**Mac / Linux:** `bash setup.sh`, poi `bash avvia_bot.sh`.

**A mano, se preferisci:** vedi README, sezione "Se preferisci il terminale". Spiega cos'è l'ambiente virtuale e come attivarlo.

### 1.3 Verifica che funzioni
```bash
python run.py sim --hours 24
```
Deve stampare un report con le strategie RUSH, S1, S2, S3, i regimi e l'ultima scheda azioni. Se lo vedi, l'installazione è a posto.

### 1.4 Dashboard
```bash
python dashboard.py
```
Apri `http://localhost:8765` nel browser. È una pagina locale, senza installazioni aggiuntive, e si aggiorna da sola ogni 5 secondi.
- Dati di una simulazione: `python dashboard.py --db data/sim.db`.
- Porta diversa: `--port 9000`.

Lasciala aperta in un secondo terminale mentre il bot gira. È visibile solo dal tuo computer (ascolta su 127.0.0.1).

### 1.5 Telegram (consigliato)
1. Su Telegram scrivi a @BotFather, comando `/newbot`, e copia il token.
2. Scrivi un messaggio qualsiasi al tuo nuovo bot. Poi apri `https://api.telegram.org/bot<TOKEN>/getUpdates` e copia il numero `chat.id`.
3. Inseriscili in `config.yaml` → `alerts`.
4. Prova: `python run.py test-alert`.

In fase 1 i segnali sono manuali: Telegram è il modo più comodo per riceverli dal telefono.

---

## Parte 2: test prima del lancio (fino al 9/10)

### Test 1: simulazione completa
```bash
python run.py sim --hours 72
```
Cosa controllare:
- **RUSH:** costo netto per PAPER intorno a $0,004–0,006 in modalità coppie.
- **Regimi:** il bot passa da INSOLVENTE/BOOTSTRAP a DECADIMENTO o SWEEP in base all'LP simulata.
- **Trattenuta:** dopo qualche chiusura risulta "stimata dai trade", con b e K vicini ai valori nascosti della simulazione (b = 0,03, K = 0,0008). Così sai che la calibrazione funziona.

### Test 1b: audit dei calcoli
```bash
python run.py audit --hours 24
```
Ricalcola in modo indipendente, trade per trade:
- i PAPER coniati: margine × emissione sulle liquidazioni, perdita × emissione (× 0,98 a coda vuota) sulle chiusure in perdita;
- la trattenuta sulle vincite;
- la distanza di liquidazione (1/leva − 0,05%).

Devono risultare 0 errati.

### Test 1c: scenari
```bash
python run.py scenarios --seeds 3 --hours 12
python run.py scenarios --param thresholds.drain_mode --values defensive,opportunistic --seeds 3 --hours 12
```
Prova la configurazione in cinque scenari: corsa debole, forte o enorme, balene che svuotano l'LP, folla vincente con LP insolvente. Scegli le impostazioni che vanno bene in tutti gli scenari, guardando soprattutto la colonna "peggiore".

### Test 2: sensibilità dei parametri (sweep)
Il comando `sweep` prova più valori di un parametro su più scenari casuali e confronta i risultati:
```bash
python run.py sweep --param <parametro> --values a,b,c --seeds 5 --hours 4
```

Come leggere la tabella:
- **costo/PAPER:** più basso è meglio.
- **PAPER:** quanti ne coni.
- **peggior PnL:** lo scenario peggiore, cioè il tuo rischio reale.

Gli sweep da fare, in ordine di importanza:

| # | Comando | Cosa decide |
|---|---|---|
| a | `--param rush.pair_target_minutes --values 5,15,30` | Velocità contro costo nella corsa. Valori bassi = più PAPER ma più cari; valori alti = più economici ma ne coni meno entro la finestra. Esempio di prova: 5 min → 26k PAPER a $0,0046; 30 min → 12k a $0,0035 |
| b | `--param rush.margin_usd --values 5,10,20` | Margine per ordine: più alto = più nozionale e priorità, ma meno ordini col budget |
| c | `--param rush.mode --values pairs,burn` | Coppie o burn: guarda soprattutto la colonna "peggior PnL" |
| d | `--param haircut.target_max --values 0.10,0.15,0.25` | Distanza dei TP in S1/S3: più bassa = TP più lontani e leva minore |
| e | `--param strategies.farming.min_ev_per_margin --values 0.02,0.05,0.15` | Quanto deve essere positivo l'EV per attivare S3 |
| f | `--param sim.rush_loss_per_hour --values 500000,2000000,6000000` | Corsa debole, media o enorme: come cambia la scelta coppie/burn e la durata |

**Attenzione:** i risultati della simulazione dipendono dalle ipotesi sulla folla. Servono a confrontare le impostazioni tra loro, non a prevedere i guadagni. Scegli valori che vanno bene in tutti gli scenari, non solo nel migliore.

### Test 3: prezzi reali di Hyperliquid
In `config.yaml` lascia `market_source: hyperliquid`, `protocol_source: sim`, `mode: dry_run`.
```bash
python run.py run
```
Lascialo girare almeno 2–3 ore, meglio durante un evento macro. Controlla:
- che non escano errori di rete;
- che spread e divergenza dall'oracle non blocchino le aperture per BTC ed ETH (nel registro: "mercato non idoneo");
- che i segnali abbiano senso, con distanze di liquidazione più ampie quando la volatilità sale.

Fermalo con Ctrl+C. Lo stato resta in `data/papertrade.db`.

### Test 4: eventi
Inserisci in `events.yaml` gli eventi macro della settimana del lancio (ora di Roma), con data verificata sul calendario economico. Con `python run.py status` controlla che la scheda mostri "S2 eventi: prossimo ...".

### Test 5: kill switch
```bash
python run.py kill      # il bot chiude tutto e si ferma
python run.py unkill
```
Provalo con il bot in esecuzione, così sai come fermarlo in fretta.

---

## Parte 3: 8/10, fase 0 (predeposito)

### Test 6: lettura del contratto
1. Deposita su A, B e C e registra la session key (vedi GUIDA §3c).
2. Compila in `config.yaml`: `onchain.contract_address`, `onchain.abi_path` (salva l'ABI in `abi/papertrade.json`), `onchain.my_address` e le funzioni in `onchain.functions`. Puoi anche girarmi l'ABI e la mappatura la faccio io.
3. Prova:
```bash
python run.py check-onchain
```
Ogni funzione mappata stampa il suo valore. Prima del lancio ti aspetti:
- saldo LP = 0;
- coda = 0;
- emissione 100;
- il tuo saldo depositato corretto.

Una riga "ERRORE" indica un nome di funzione sbagliato, un argomento mancante o una scala decimale errata.

Se i parametri della curva sono leggibili, il comando li stampa: in quel caso la calibrazione non serve.

4. Imposta `protocol_source: onchain` e lascia girare `python run.py run` fino al lancio.

---

## Parte 4: test al lancio (10/10)

### Test 7: tempi di conferma dei relayer
Per ogni ordine eseguito a mano, annota quanti secondi passano tra l'invio e la conferma, e registrali:
```bash
python run.py delay add --seconds 45 --notional 10000 --kind apertura
python run.py delay add --seconds 160 --notional 800 --kind chiusura
python run.py delay show
```
Il bot suggerisce i valori da mettere in `congestion.base_delay_s` e `small_extra_delay_s`. Aggiornali e riavvia: segnali di annullamento e queue guard diventano più precisi.

### Test 8: verifica dell'emissione
Dopo la prima perdita, confronta i PAPER ricevuti con quelli attesi:
- **liquidazione:** margine × 100;
- **chiusura in perdita con coda vuota:** perdita × 0,98 × 100;
- **chiusura in perdita con coda attiva:** perdita × 100.

Se non tornano, scrivimi i numeri: vuol dire che la base del conio è diversa da quella dei docs.

### Test 9: calibrazione della trattenuta
Serve solo se i parametri della curva non erano leggibili. Fai 3 chiusure in profitto a distanze diverse (circa 0,3%, 1%, 2%) e registrale:
```bash
python run.py calib add --move 0.01 --gross 14.25 --net 12.10
python run.py calib show
```
Dopo 3 osservazioni la curva è "stimata dai trade". Se il frontend mostra un'anteprima di quanto riceverai alla chiusura, ogni anteprima vale come osservazione gratuita.

### Test 10: staking e ricompense
Dopo il primo stake, controlla che `my_staked` in `check-onchain` salga e che `pendingReward` cresca col tempo. Fai un claim di prova per vedere dove arrivano gli USDC.

---

## Parte 5: dopo il lancio, migliorare la strategia

Con i dati reali raccolti nei primi giorni:
1. **Rifai gli sweep** usando i valori misurati: ritardi reali, curva della trattenuta reale, velocità reale dell'LP nella corsa (`sim.rush_loss_per_hour`), perdite medie della folla (`sim.crowd_net_to_lp_per_hour`). Più la simulazione somiglia al protocollo vero, più gli sweep sono utili.
2. **Confronta il costo per PAPER** nella dashboard, per strategia: tieni attive quelle più economiche e spegni le altre in `config.yaml` (`enabled: false`).
3. **Misura la fetta degli staker:** confronta quanto crescono le tue ricompense con quanto cresce l'LP. Aggiorna `sim.staker_share_of_carve`: serve a stimare il valore implicito di PAPER, da cui dipende S3.
4. **Rivedi il budget** ogni giorno: lo stop giornaliero di $100 e il budget di trading valgono finché l'EV misurato non dimostra il contrario.

---

## Problemi comuni

| Sintomo | Causa probabile | Soluzione |
|---|---|---|
| `ModuleNotFoundError` | Ambiente virtuale non attivo | Riattiva `.venv` e rifai `pip install -r requirements.txt` |
| Errori di rete continui | Connessione o limiti delle API di Hyperliquid | Aumenta `loops.fast_seconds` a 5 |
| Nessun ordine in `run` | Fuori dalla finestra di lancio, mercato non idoneo o kill switch attivo | Leggi il registro nella dashboard o con `status` |
| "Configurazione onchain incompleta" | Indirizzo o ABI mancanti | Compila `onchain` (Parte 3) |
| La dashboard non mostra nulla | Database diverso da quello del bot | Passa il percorso: `python dashboard.py --db data/papertrade.db` |
