"""
Prezzo di un token DOPO la graduation.

Quando un token pump.fun gradua, la bonding curve si congela e il
mercato si sposta su un AMM. I moltiplicatori grossi della storia
(WIF, BONK e simili) sono avvenuti li', nell'arco di settimane.
La curva non li vede.

Invece di dipendere dal program ID di uno specifico AMM (che cambia
nel tempo), il pool viene scoperto dai dati:

  1. il maggior detentore del token, dopo la graduation, e' il pool
  2. si legge l'owner di quel token account (una PDA del programma AMM)
  3. si cerca l'account WSOL dello stesso owner
  4. prezzo = riserve WSOL / riserve token

Funziona con qualsiasi AMM a prodotto costante. Se la struttura non
corrisponde, la funzione restituisce None invece di tirare a indovinare.
"""

WSOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000

# Sotto queste soglie non e' un pool: e' un wallet grosso qualunque.
MIN_POOL_SOL = 0.5
MIN_POOL_TOKENS = 1_000.0


async def find_pool(rpc, mint):
    """
    Individua il pool di un token graduato.
    Ritorna dict(pool_owner, token_account, wsol_account) oppure None.
    """
    largest = await rpc("getTokenLargestAccounts", [mint])
    accounts = (largest or {}).get("value", [])
    if not accounts:
        return None

    # I candidati sono i primi per saldo: il pool e' fra questi.
    for cand in accounts[:3]:
        addr = cand.get("address")
        amount = float(cand.get("uiAmount") or 0)
        if not addr or amount < MIN_POOL_TOKENS:
            continue

        info = await rpc("getAccountInfo", [addr, {"encoding": "jsonParsed"}])
        parsed = (((info or {}).get("value") or {}).get("data") or {}).get("parsed")
        if not parsed:
            continue
        owner = (parsed.get("info") or {}).get("owner")
        if not owner:
            continue

        # L'owner di un pool e' una PDA del programma AMM e possiede
        # anche un account WSOL: e' quello che lo distingue da un wallet.
        wsol = await rpc("getTokenAccountsByOwner",
                         [owner, {"mint": WSOL_MINT},
                          {"encoding": "jsonParsed"}])
        for acc in (wsol or {}).get("value", []):
            ta = ((acc.get("account", {}).get("data", {})
                   .get("parsed", {}).get("info", {})))
            bal = float((ta.get("tokenAmount") or {}).get("uiAmount") or 0)
            if bal >= MIN_POOL_SOL:
                return {"pool_owner": owner,
                        "token_account": addr,
                        "wsol_account": acc.get("pubkey")}
    return None


async def read_pool(rpc, pool):
    """
    Prezzo e liquidita' correnti del pool.
    Ritorna dict(price_sol, liq_sol, tokens) oppure None.
    """
    tok = await rpc("getTokenAccountBalance", [pool["token_account"]])
    sol = await rpc("getTokenAccountBalance", [pool["wsol_account"]])

    tokens = float(((tok or {}).get("value") or {}).get("uiAmount") or 0)
    liq = float(((sol or {}).get("value") or {}).get("uiAmount") or 0)

    if tokens <= 0:
        return None
    return {"price_sol": liq / tokens, "liq_sol": liq, "tokens": tokens}


async def price_after_graduation(rpc, mint, pool=None):
    """
    Comodità: scopre il pool se serve e ne legge il prezzo.
    Ritorna (dati_prezzo, pool) — entrambi None se il pool non si trova.
    """
    pool = pool or await find_pool(rpc, mint)
    if not pool:
        return None, None
    data = await read_pool(rpc, pool)
    return data, pool
