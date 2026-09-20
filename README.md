# pumpwatch

Raccolta dati on-chain sui nuovi token pump.fun (Solana), per capire
quali ruggano e quali no — a partire dai dati, non dalle opinioni.

Legge la bonding curve direttamente dagli account on-chain, quindi
prezzo, liquidità reale e slippage non sono stime: sono i numeri esatti
che il programma usa.

## Il risultato principale

Su **7.106 token** con dati completi (snapshot a 30s, 2min, 10min):

| | quota |
|---|---|
| liquidità reale sempre sotto 0.1 SOL | 60.0% |
| mai comprati da nessuno entro t+10min | 42.8% |
| **liquidità crollata >50% fra t+30s e t+10min** | **13.0%** |
| liquidità raddoppiata | 4.7% |
| graduati | 2.4% |

Ristretto ai soli token che hanno avuto liquidità vera (≥ 1 SOL a t+30s,
n=1.228): **il 75.4% ne perde più della metà entro dieci minuti.**

Non serve un modello per dire che un token pump.fun è rischioso. Lo è
quasi sempre.

## Cosa NON funziona: i filtri sul singolo token

Nessuna caratteristica misurata a t+30s separa i rug dal resto. Tutti i
filtri provati stanno **sotto** la base:

| filtro (a t+30s) | tasso di rug | vs base | n |
|---|---|---|---|
| base (liquidità ≥ 1 SOL) | 75.4% | — | 1228 |
| top1 > 50% | 74.2% | −1.2 pt | 1148 |
| top1 > 80% | 73.3% | −2.1 pt | 836 |
| meno di 10 holder | 73.7% | −1.7 pt | 819 |
| liquidità > 50 SOL | 70.5% | −5.0 pt | 88 |
| liq > 50 SOL e < 10 holder | 66.7% | −8.7 pt | 42 |

### Un artefatto di misura, e come è stato scoperto

Le versioni precedenti di questo README riportavano che il filtro
`liquidità > 50 SOL` alzava il tasso di rug di **+12 punti**, e il
risultato aveva perfino **replicato su un secondo dataset indipendente**
(+14 punti).

Era un artefatto dello strumento.

Il collector limitava i task di campionamento concorrenti con un
semaforo, ma acquisiva lo slot *prima* di far partire il cronometro. Con
la coda piena, uno snapshot etichettato "t+30s" veniva in realtà
prelevato molto più tardi — quando la liquidità si era già mossa. Il
punto di partenza era sbagliato, e il confronto con t+10min
sottostimava i crolli in modo non casuale.

Corretto il bug (il cronometro parte alla nascita del token; un
checkpoint troppo in ritardo viene saltato, non spostato), la copertura
è passata dal 56% al 70% e il tasso di rug di base dal 32% al 75%. Tutti
i filtri sul singolo token sono spariti.

**La replica su un campione indipendente non protegge da un errore
sistematico**: entrambi i dataset erano stati raccolti con lo stesso
strumento difettoso.

## Cosa funziona: la storia dei wallet

Il segnale che resta non è nel token, è in chi lo lancia. Su creator con
almeno 40 token tracciati:

```
4goaRhdG...   127 token    48 rug    113 mai comprati da nessuno
bwamJzzt...    66 token    34 rug     33 mai comprati
aH31qjgP...    48 token    30 rug     21 mai comprati
AjHee8HU...    50 token     8 rug      0 mai comprati

4NNe9CRM...   173 token     1 rug    159 mai comprati
3zQ81HMt...    76 token     1 rug      1 mai comprati
9QAEGBhQ...    42 token     0 rug      0 mai comprati
8o6u9u4a...    64 token     0 rug     64 mai comprati
```

Tre popolazioni distinte:

- **chi rugga in serie** — decine di rug su decine di lanci
- **le farm di spam** — centinaia di lanci che nessuno compra mai,
  quindi non ruggano: muoiono e basta
- **chi lancia molto senza ruggare** — probabilmente servizi automatici

Questa separazione non dipende dal timing degli snapshot, quindi non è
vulnerabile all'artefatto descritto sopra. È il pezzo su cui vale la
pena costruire.

## Sul trading

Il progetto è nato come strumento di trading. Il tracker segue fino a
24h i token con liquidità vera e registra il **massimo** raggiunto dopo
t+10min — cioè assume di vendere al picco perfetto.

Su 474 token seguiti (due campioni separati): **zero che fanno 3x**.
Mediana 1.00x, cioè metà non supera mai il prezzo d'ingresso nemmeno per
un istante. Il costo minimo di un giro completo (fee pump.fun 1% + 1%,
priority fee, slippage) è circa il 5%.

E i pochi che salgono sono gli stessi che poi crollano:

```
2PecSHpJ   1.69x al minuto 90   →  liquidità poi 0.00 SOL
5AeHYnYc   1.32x al minuto 20   →  liquidità poi 0.00 SOL
```

Il segnale "sta salendo" e il segnale "sta per ruggare" coincidono.

*Nota: questa misura precede la correzione del bug di timing. La
direzione è robusta — zero 3x su 474 non diventa un numero buono — ma i
valori andranno rimisurati.*

## Componenti

| file | ruolo |
|---|---|
| `main.py` | listener WebSocket: registra ogni nuovo mint + snapshot a 30s/2min/10min |
| `tracker.py` | segue i token con liquidità vera fino a 24h, registra il picco |
| `curve.py` | decoder della bonding curve: prezzo, liquidità, slippage, progress |
| `pda.py` | derivazione PDA e ATA in Python puro (nessuna dipendenza nativa) |
| `analyze.py` | affidabilità del dataset e potere predittivo dei filtri |
| `outcomes.py` | distribuzione dei moltiplicatori |
| `report.py` | riepilogo rapido |

Solo dipendenze Python pure: gira anche su Termux/Android.

## Uso

```bash
pip install -r requirements.txt
cp .env.example .env        # inserisci la chiave, gratuita su helius.dev
set -a; source .env; set +a

python main.py              # raccolta
python tracker.py           # esiti a lungo termine (processo separato)

python analyze.py           # cosa dicono i dati
python outcomes.py
```

Su un server, come servizio systemd (riparte da solo dopo un crash):

```ini
[Unit]
Description=pumpwatch
After=network-online.target

[Service]
WorkingDirectory=/opt/pumpwatch
EnvironmentFile=/opt/pumpwatch/.env
ExecStart=/opt/pumpwatch/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Note tecniche

- **Il timing degli snapshot è parte della misura.** Il cronometro parte
  alla nascita del token, prima della coda per uno slot; un checkpoint
  in ritardo di oltre 15s viene saltato invece che spostato. Senza
  questo, i dati sono sbagliati in modo silenzioso.
- Il layout dell'account bonding curve è cambiato tra versioni del
  programma. Verifica `decode_curve` contro l'IDL corrente prima di
  fidarti dei numeri in produzione.
- `getTokenLargestAccounts` restituisce al massimo 20 account: un
  `holder_count` di 20 va letto come "20 o più".
- L'account della bonding curve viene escluso dal conteggio holder,
  altrimenti ogni token nuovo risulterebbe al 100% di concentrazione.
- Quando un token gradua, la curva si congela e il mercato passa
  all'AMM: il tracking si chiude lì.
- La copertura degli snapshot è ~70%. Le analisi usano solo i mint con
  tutti e tre i punti.

## Cosa non fa

Non compra, non vende e non dà segnali di acquisto. Raccoglie e misura.

## Licenza

MIT
