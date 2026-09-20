"""
pumpwatch - tracker a lungo termine.

Processo SEPARATO dal collector: gira in parallelo, legge i mint gia'
registrati e segue solo quelli che hanno mostrato liquidita' vera.

Per ognuno registra, fino a 24h: prezzo, liquidita', e soprattutto il
MASSIMO raggiunto. Da li' esce il moltiplicatore, cioe' la risposta a
"quanti dei token che passano il filtro fanno 5x".

Lo scheduling sta nel DB, non in memoria: se il processo muore e
riparte, riprende da dove era.

Uso:  HELIUS_API_KEY=xxx python tracker.py
"""

import asyncio
import base64
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import aiosqlite
import httpx

from curve import decode_curve, bonding_curve_pda

API_KEY = os.environ.get("HELIUS_API_KEY")
if not API_KEY:
    sys.exit("Manca HELIUS_API_KEY")

DB_PATH = os.environ.get("PUMPWATCH_DB", "pumpwatch.db")
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"

# Segui solo i token che a t+10min avevano almeno questa liquidita' reale.
# Sotto questa soglia non c'e' mercato: seguirli sprecherebbe rate limit.
MIN_LIQ_SOL = 1.0

# Ogni quanti minuti dal lancio ricontrollare.
CHECKPOINTS_MIN = [12, 15, 20, 25, 30, 40, 50, 60, 90, 120,
                   180, 240, 360, 480, 720, 1440]

LOOP_SECONDS = 60          # ogni quanto cercare controlli scaduti
BATCH = 25                 # quanti controlli per ciclo (rate limit)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("tracker")

SCHEMA = """
CREATE TABLE IF NOT EXISTS outcomes (
    mint           TEXT PRIMARY KEY,
    entry_price    REAL,      -- prezzo a t+10min
    entry_liq      REAL,
    max_price      REAL,      -- massimo osservato dopo l'ingresso
    max_at_min     INTEGER,   -- a quanti minuti dal lancio
    last_price     REAL,
    last_liq       REAL,
    graduated      INTEGER DEFAULT 0,
    checks_done    INTEGER DEFAULT 0,
    next_check_at  TEXT,      -- ISO, NULL = finito
    started_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_due ON outcomes(next_check_at);
"""


def now():
    return datetime.now(timezone.utc)


async def rpc(client, method, params):
    r = await client.post(RPC_URL, json={"jsonrpc": "2.0", "id": 1,
                                         "method": method, "params": params})
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"{method}: {d['error']}")
    return d.get("result")


async def read_curve(client, mint):
    res = await rpc(client, "getAccountInfo",
                    [bonding_curve_pda(mint), {"encoding": "base64"}])
    val = (res or {}).get("value")
    if not val:
        return None
    return decode_curve(base64.b64decode(val["data"][0]))


async def enroll(db):
    """
    Iscrive al tracking i mint nuovi che meritano: quelli con uno
    snapshot a t+600 e liquidita' sopra soglia.
    """
    cur = await db.execute("""
        SELECT s.mint, s.price_sol, s.real_liq_sol, m.seen_at
        FROM snapshots s
        JOIN mints m ON m.mint = s.mint
        WHERE s.offset_seconds = 600
          AND s.real_liq_sol >= ?
          AND s.mint NOT IN (SELECT mint FROM outcomes)
    """, (MIN_LIQ_SOL,))
    rows = await cur.fetchall()
    n = 0
    for mint, price, liq, seen_at in rows:
        try:
            born = datetime.fromisoformat(seen_at)
        except Exception:
            born = now()
        if born.tzinfo is None:
            born = born.replace(tzinfo=timezone.utc)

        # primo checkpoint ancora futuro rispetto alla nascita
        nxt = None
        for m in CHECKPOINTS_MIN:
            t = born + timedelta(minutes=m)
            if t > now():
                nxt = t
                break
        if nxt is None:
            continue    # token gia' vecchio di oltre 24h: inutile

        await db.execute("""
            INSERT OR IGNORE INTO outcomes
              (mint, entry_price, entry_liq, max_price, max_at_min,
               last_price, last_liq, next_check_at, started_at)
            VALUES (?,?,?,?,0,?,?,?,?)
        """, (mint, price, liq, price, price, liq,
              nxt.isoformat(), born.isoformat()))
        n += 1
    await db.commit()
    if n:
        log.info("iscritti %d nuovi token al tracking", n)


async def check_one(client, db, row):
    mint, entry_price, max_price, max_at, started, done = row
    born = datetime.fromisoformat(started)
    if born.tzinfo is None:
        born = born.replace(tzinfo=timezone.utc)
    age_min = int((now() - born).total_seconds() // 60)

    try:
        st = await read_curve(client, mint)
    except Exception as e:
        log.warning("%s: %s", mint[:8], e)
        st = None

    if st is None:
        # curva sparita o illeggibile: chiudo il tracking
        await db.execute(
            "UPDATE outcomes SET next_check_at=NULL, checks_done=? WHERE mint=?",
            (done + 1, mint))
        return

    price, liq = st.price_sol, st.real_liquidity_sol
    new_max, new_max_at = (price, age_min) if price > (max_price or 0) \
        else (max_price, max_at)

    # prossimo checkpoint ancora futuro
    nxt = None
    for m in CHECKPOINTS_MIN:
        t = born + timedelta(minutes=m)
        if t > now():
            nxt = t.isoformat()
            break

    # una volta graduato la curva non riflette piu' il prezzo:
    # il mercato si sposta sull'AMM. Chiudo qui.
    if st.complete:
        nxt = None

    await db.execute("""
        UPDATE outcomes SET max_price=?, max_at_min=?, last_price=?,
               last_liq=?, graduated=?, checks_done=?, next_check_at=?
        WHERE mint=?""",
        (new_max, new_max_at, price, liq, 1 if st.complete else 0,
         done + 1, nxt, mint))

    mult = (new_max / entry_price) if entry_price else 0
    log.info("%s  t+%-5smin  liq=%7.2f  max=%5.2fx%s",
             mint[:8], age_min, liq, mult,
             "  GRADUATO" if st.complete else "")


async def main():
    db = await aiosqlite.connect(DB_PATH)
    await db.executescript(SCHEMA)
    await db.commit()
    log.info("tracker avviato (soglia %.1f SOL, fino a 24h)", MIN_LIQ_SOL)

    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            try:
                await enroll(db)
                cur = await db.execute("""
                    SELECT mint, entry_price, max_price, max_at_min,
                           started_at, checks_done
                    FROM outcomes
                    WHERE next_check_at IS NOT NULL AND next_check_at <= ?
                    ORDER BY next_check_at LIMIT ?""",
                    (now().isoformat(), BATCH))
                due = await cur.fetchall()
                for row in due:
                    await check_one(client, db, row)
                    await asyncio.sleep(0.3)      # gentile col rate limit
                await db.commit()

                if due:
                    log.info("%d controlli eseguiti", len(due))
            except Exception as e:
                log.error("ciclo fallito: %s", e)
            await asyncio.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("uscita")
