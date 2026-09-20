"""
pumpwatch - raccolta dati su nuovi token pump.fun.

Per ogni token mintato registra, a 30s / 2min / 10min:
  - concentrazione degli holder
  - stato della bonding curve (prezzo, liquidita' reale, progress)

Questa e' la FASE DI RACCOLTA. Lo scoring si scrive dopo,
tarato su questi dati. Non contiene segnali di acquisto.

Uso:  HELIUS_API_KEY=xxx python main.py
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

import aiosqlite  # noqa: F401  (usato da db.py)
import httpx
import websockets

import db as store
from curve import decode_curve, bonding_curve_pda
from pda import associated_token_address

API_KEY = os.environ.get("HELIUS_API_KEY")
if not API_KEY:
    sys.exit("Manca HELIUS_API_KEY. Prendila gratis su helius.dev")

DB_PATH = os.environ.get("PUMPWATCH_DB", "pumpwatch.db")
WS_URL = f"wss://mainnet.helius-rpc.com/?api-key={API_KEY}"
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SNAPSHOT_OFFSETS = [30, 120, 600]
MAX_TRACKS = 400

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("pumpwatch")

_slots = asyncio.Semaphore(MAX_TRACKS)
_http: httpx.AsyncClient | None = None


def now():
    return datetime.now(timezone.utc).isoformat()


async def rpc(method, params):
    r = await _http.post(RPC_URL, json={
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"{method}: {data['error']}")
    return data.get("result")


# ------------------------------------------------------------- estrazione

def extract(tx):
    """Ricava mint e creator dalla tx di create."""
    if not tx:
        return None, None
    msg = tx.get("transaction", {}).get("message", {})
    meta = tx.get("meta") or {}

    groups = [msg.get("instructions", [])]
    for inner in meta.get("innerInstructions") or []:
        groups.append(inner.get("instructions", []))

    mint = None
    for g in groups:
        for ix in g:
            p = ix.get("parsed")
            if isinstance(p, dict) and p.get("type") in ("initializeMint",
                                                         "initializeMint2"):
                mint = p.get("info", {}).get("mint")
                break
        if mint:
            break

    if not mint:
        # Fallback: un mint NUOVO non puo' esistere prima della tx.
        # Senza questo controllo si finisce per registrare USDC o WSOL
        # solo perche' compaiono fra i token balance della transazione.
        pre = {b.get("mint") for b in (meta.get("preTokenBalances") or [])}
        for b in meta.get("postTokenBalances") or []:
            m = b.get("mint")
            if m and m not in pre:
                mint = m
                break

    creator = None
    for a in msg.get("accountKeys", []):
        if isinstance(a, dict) and a.get("signer"):
            creator = a.get("pubkey")
            break
    return mint, creator


# --------------------------------------------------------------- sampling

async def sample_holders(mint):
    """
    Concentrazione fra gli holder VERI.

    getTokenLargestAccounts include l'account della bonding curve, che
    detiene tutta la supply invenduta: senza escluderlo ogni token nuovo
    risulta top1=100%. Le quote sono calcolate sul circolante, cioe' sui
    token gia' usciti dalla curva.
    """
    largest = await rpc("getTokenLargestAccounts", [mint])
    accts = (largest or {}).get("value", [])
    if not accts:
        return {"top1": None, "top5": None, "count": 0}

    curve_ata = associated_token_address(bonding_curve_pda(mint), mint)
    holders = [(a["address"], float(a.get("uiAmount") or 0))
               for a in accts
               if a.get("address") != curve_ata and float(a.get("uiAmount") or 0) > 0]

    circulating = sum(a for _, a in holders)
    if circulating <= 0:
        # nessuno ha ancora comprato: niente da misurare
        return {"top1": None, "top5": None, "count": 0}

    amts = sorted((a for _, a in holders), reverse=True)
    return {"top1": amts[0] / circulating,
            "top5": sum(amts[:5]) / circulating,
            "count": len(holders)}


async def sample_curve(mint):
    pda = bonding_curve_pda(mint)
    res = await rpc("getAccountInfo", [pda, {"encoding": "base64"}])
    val = (res or {}).get("value")
    if not val:
        return {}
    import base64
    raw = base64.b64decode(val["data"][0])
    st = decode_curve(raw)
    return {"price": st.price_sol,
            "mcap": st.market_cap_sol,
            "liq": st.real_liquidity_sol,
            "progress": st.progress,
            "graduated": st.complete}


async def track(db, mint):
    """
    Snapshot agli offset previsti, poi il task termina.

    Il cronometro parte SUBITO, prima di mettersi in coda per uno slot:
    altrimenti con la coda piena si campiona in ritardo salvando
    l'etichetta sbagliata. Un checkpoint mancato si salta, non si sposta.
    """
    t0 = asyncio.get_event_loop().time()
    async with _slots:
        for off in SNAPSHOT_OFFSETS:
            elapsed = asyncio.get_event_loop().time() - t0
            wait = off - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            elif wait < -15:
                # troppo in ritardo: meglio nessun dato che dato falso
                continue
            try:
                holders, curve = await asyncio.gather(
                    sample_holders(mint), sample_curve(mint))
                await store.save_snapshot(db, mint, off, now(), holders, curve)
                t1 = holders["top1"]
                log.info("t+%-4ss %s  top1=%6s  hold=%3s  liq=%6.2f SOL  prog=%5.1f%%",
                         off, mint[:8],
                         f"{t1*100:.1f}%" if t1 is not None else "  -  ",
                         holders["count"],
                         curve.get("liq") or 0,
                         (curve.get("progress") or 0) * 100)
            except Exception as e:
                log.warning("snapshot %s t+%ss: %s", mint[:8], off, e)


async def on_create(db, sig, slot):
    try:
        tx = await rpc("getTransaction", [sig, {
            "encoding": "jsonParsed", "commitment": "confirmed",
            "maxSupportedTransactionVersion": 1}])
    except Exception as e:
        log.warning("getTransaction %s: %s", sig[:10], e)
        return
    mint, creator = extract(tx)
    if not mint:
        return
    if await store.save_mint(db, mint, creator, sig, slot, now()):
        log.info("NUOVO %s  creator=%s", mint, (creator or "?")[:8])
        asyncio.create_task(track(db, mint))


# ------------------------------------------------------------------- loop

async def listen(db):
    backoff = 1
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20,
                                          ping_timeout=20,
                                          max_size=8 << 20) as ws:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                    "params": [{"mentions": [PUMP_PROGRAM_ID]},
                               {"commitment": "confirmed"}]}))
                log.info("in ascolto su pump.fun")
                backoff = 1
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("method") != "logsNotification":
                        continue
                    res = m["params"]["result"]
                    val = res.get("value", {})
                    if val.get("err"):
                        continue
                    if not any("Instruction: Create" in l
                               for l in val.get("logs", [])):
                        continue
                    asyncio.create_task(on_create(
                        db, val.get("signature"),
                        res.get("context", {}).get("slot")))
        except Exception as e:
            log.error("disconnesso (%s) - riprovo fra %ss", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def main():
    global _http
    _http = httpx.AsyncClient(timeout=20)
    db = await store.init(DB_PATH)
    log.info("db pronto: %s", DB_PATH)
    try:
        await listen(db)
    finally:
        await db.close()
        await _http.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("uscita")
