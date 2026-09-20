# pumpwatch

Raccolta dati on-chain sui nuovi token pump.fun (Solana), per capire
quali ruggano e quali no — a partire dai dati, non dalle opinioni.

Legge la bonding curve direttamente dagli account on-chain, quindi
prezzo, liquidità reale e slippage non sono stime: sono i numeri esatti
che il programma usa.

## Perché esiste

Il progetto è nato come strumento di trading. I dati hanno detto di no,
e questo è diventato il risultato principale.

**Su 333 token con liquidità reale, seguiti fino a 24 ore:**

| esito | quota |
|---|---|
| ≥ 1.5x dal prezzo a t+10min | 1.2% |
| ≥ 2x | 0.3% |
| ≥ 3x | **0%** |
| ≥ 5x | **0%** |
| mediana | **1.00x** |

Il moltiplicatore è il *massimo* raggiunto dopo l'ingresso, cioè assume
di vendere al picco perfetto. Anche con quell'ipotesi generosa, metà dei
token non supera mai il prezzo di ingresso. Il costo minimo di un giro
completo (fee pump.fun 1% + 1%, priority fee, slippage) è circa il 5%.

E i pochi che salgono sono gli stessi che poi crollano:

2PecSHpJ 1.69x al minuto 90 → liquidità ora 0.00 SOL
5AeHYnYc 1.32x al minuto 20 → liquidità ora 0.00 SOL


Il segnale "sta salendo" e il segnale "sta per ruggare" coincidono.

## Cosa invece funziona

Su 16.549 mint raccolti (10.434 con dati completi):

- **87.9%** non ha mai avuto liquidità (sotto 0.1 SOL). pump.fun è quasi
  interamente rumore: per la grande maggioranza dei lanci non esiste
  nemmeno un mercato.
- **51.0%** non viene comprato da nessuno nei primi 10 minuti.
- **2.5%** raggiunge la graduation.
- Un filtro identifica i rug meglio del caso:

| filtro (a t+30s) | tasso di rug | vs base | n |
|---|---|---|---|
| base (liquidità ≥ 1 SOL) | 26.8% | — | 410 |
| top1 > 50% | 26.4% | −0.4 pt | 401 |
| top1 > 80% | 22.4% | −4.4 pt | 308 |
| liquidità > 50 SOL | 38.9% | +12.1 pt | 54 |
| **liq > 50 SOL e < 10 holder** | **48.0%** | **+21.2 pt** | 25 |

La concentrazione degli holder da sola non predice nulla — anzi, va
leggermente sotto la base. Liquidità alta concentrata in pochissimi
wallet sì.

### Una lezione sui campioni piccoli

Alla prima lettura (n=16) il filtro migliore dava **68.8%**. Raddoppiando
il campione (n=25) è sceso a **48.0%**, e un terzo filtro che sembrava
promettente (+12.9 pt) è crollato a +1.2 pt, cioè a zero.

Regressione verso la media. Le direzioni hanno retto, i valori no: è il
motivo per cui in questo README ogni riga riporta il proprio `n`.

Rug catturati mentre accadevano (liquidità reale, t+30s → t+10min):

HcFFtt7d 1055.60 SOL → 2.95 SOL
9MuezEGX 812.77 SOL → 0.00 SOL
4ETyDgTt 164.04 SOL → 0.00 SOL


E wallet che lanciano in serie: 391 lanci da un singolo creator,
155 token consecutivi mai comprati da nessuno da un altro.

*I campioni sono ancora piccoli sulle righe più selettive. Le direzioni
sono solide, i valori esatti no: vanno letti come ordini di grandezza.*

## Componenti

| file | ruolo |
|---|---|
| `main.py` | listener WebSocket: registra ogni nuovo mint + snapshot a 30s/2min/10min |
| `tracker.py` | segue i token con liquidità vera fino a 24h, registra il picco |
| `curve.py` | decoder della bonding curve: prezzo, liquidità, slippage, progress |
| `pda.py` | derivazione PDA e ATA in Python puro (nessuna dipendenza nativa) |
| `analyze.py` | misura l'affidabilità del dataset e il potere predittivo dei filtri |
| `outcomes.py` | distribuzione dei moltiplicatori |
| `report.py` | riepilogo rapido |

Solo dipendenze Python pure: gira anche su Termux/Android.

## Uso

```bash
pip install -r requirements.txt
export HELIUS_API_KEY=...        # chiave gratuita su helius.dev

python main.py                   # raccolta
python tracker.py                # esiti a lungo termine (processo separato)

python analyze.py                # cosa dicono i dati
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

- Il layout dell'account bonding curve è cambiato tra versioni del
  programma. Verifica `decode_curve` contro l'IDL corrente prima di
  fidarti dei numeri in produzione.
- `getTokenLargestAccounts` restituisce al massimo 20 account: un
  `holder_count` di 20 va letto come "20 o più".
- L'account della bonding curve viene escluso dal conteggio holder,
  altrimenti ogni token nuovo risulterebbe al 100% di concentrazione.
- Quando un token gradua, la curva si congela e il mercato passa
  all'AMM: il tracking si chiude lì.

## Cosa non fa

Non compra, non vende e non dà segnali di acquisto. Raccoglie e misura.

## Licenza

MIT
