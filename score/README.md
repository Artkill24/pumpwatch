# score — wallet score

Dato un wallet (o un mint), dice cosa è successo ai token che quel
creator ha già lanciato: quanti, quanti ruggati, quanti mai comprati.

Sono conteggi sui dati raccolti dal collector. Nessun modello, nessuna
predizione di prezzo.

## Uso

Dalla cartella `score/`, con il DB del collector accessibile:

```bash
PUMPWATCH_DB=../pumpwatch.db python score.py            # i wallet peggiori
PUMPWATCH_DB=../pumpwatch.db python score.py <wallet>   # un profilo
PUMPWATCH_DB=../pumpwatch.db python api.py              # web + API su :8000
```

## Verdetti

| verdetto | quando |
|---|---|
| `alto rischio` | ≥50% dei token con liquidità sono ruggati (min. 3) |
| `rischio medio` | ≥25% ruggati |
| `spam` | ≥20 lanci e ≥80% mai comprati da nessuno |
| `nessun mercato` | nessun token ha raggiunto 1 SOL di liquidità |
| `nessun segnale negativo` | nessuna delle condizioni sopra |
| `dati insufficienti` | meno di 5 token con misure complete |
| `sconosciuto` | wallet assente dal dataset |

Ogni verdetto arriva con i motivi e i numeri che lo sostengono.

## API

```
GET /api/wallet/<indirizzo>   profilo del creator
GET /api/mint/<mint>          profilo del creator di quel token
GET /api/risky                wallet con più rug
GET /api/stats                dimensione del dataset
```

Solo stdlib Python: nessuna dipendenza da installare.

## Limiti

- Copre solo i token osservati da questo collector: un wallet assente
  non è "pulito", è semplicemente non visto.
- Un rug è definito come liquidità reale scesa sotto la metà fra t+30s
  e t+10min, partendo da almeno 1 SOL.
- "Nessun segnale negativo" non è una garanzia sul futuro.
