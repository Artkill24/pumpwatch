# amm — esiti dopo la graduation

Quando un token pump.fun gradua, la bonding curve si congela e il
mercato passa a un AMM. I moltiplicatori grossi della storia — WIF,
BONK e simili — sono avvenuti lì, nell'arco di settimane. Il tracker
della curva non li vede: si ferma alla graduation.

Questo modulo segue i token graduati per **30 giorni** e registra il
massimo raggiunto.

## Come trova il pool

Senza dipendere dal program ID di un AMM specifico, che cambia nel
tempo:

1. il maggior detentore del token, dopo la graduation, è il pool
2. si legge l'`owner` di quel token account (una PDA del programma AMM)
3. si cerca l'account WSOL dello stesso owner
4. prezzo = riserve WSOL / riserve token

Se la struttura non corrisponde, restituisce `None` invece di tirare a
indovinare. Un wallet grosso viene escluso perché non ha un account
WSOL abbinato.

## Uso

```bash
PUMPWATCH_DB=../pumpwatch.db HELIUS_API_KEY=... python amm_tracker.py
PUMPWATCH_DB=../pumpwatch.db python amm_outcomes.py
```

Processo separato: non tocca il collector né il tracker della curva.

Checkpoint (ore dalla graduation): 1, 3, 6, 12, 24, 48, 72, 120, 168,
336, 504, 720.

## Limiti

- Il primo prezzo letto è il riferimento, non il prezzo di graduation
  esatto: il pool può non essere attivo nell'istante della migrazione.
- I token graduati da oltre 30 giorni non vengono iscritti.
- Serve che il collector abbia visto la graduation: token graduati
  prima dell'avvio del sistema restano fuori.
- La scoperta del pool va verificata su un token graduato reale prima
  di fidarsi dei numeri.
