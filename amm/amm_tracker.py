"""
Tracker post-graduation.

Segue i token graduati sull'AMM per 30 giorni e registra il massimo
raggiunto. Risponde alla domanda: i moltiplicatori grossi avvengono
dopo la graduation, fuori dalla finestra che il tracker della curva
riesce a vedere?

Processo separato: non tocca collector ne' tracker della curva.

Uso:  HELIUS_API_KEY=xxx python amm_tracker.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import aiosqlite
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from amm.pool import find_pool, read_pool  # noqa: E402

API_KEY = os.environ.get("HELIUS_API_KEY")
if not API_KEY:
    sys.exit("Manca HELIUS_API_KEY")

DB_PATH = os.environ.get("PUMPWATCH_DB", "pumpwatch.db")
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"

# Ore dalla graduation in cui ricontrollare.
CHECKPOINTS_H = [1, 3, 6, 12, 24, 48, 72, 120, 168, 336, 504, 720]

LOOP_SECONDS = 120
BATCH = 20

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("amm")

SCHEMA = """
CREATE TABLE IF NOT EXISTS amm_outcomes (
    mint            TEXT PRIMARY KEY,
    graduated_at    TEXT,
    pool_owner      TEXT,
    token_account   TEXT,
    wsol_account    TEXT,
    entry_price     REAL,      -- primo prezzo letto sull'AMM
    entry_liq       REAL,
    max_price       REAL,
    max_at_hours    REAL,
    last_price      REAL,
    last_liq        REAL,
    checks_done     INTEGER DEFAULT 0,
    next_check_at   TEXT,      -- NULL = finito o pool introvabile
    note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_amm_due ON amm_outcomes(next_check_at);
"""


def now():
    return datetime.now(timezone.utc)


def parse(ts):
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


_http: httpx.AsyncClient | None = None


async def rpc(method, params):
    r = await _http.post(RPC_URL, json={"jsonrpc": "2.0", "id": 1,
                                        "method": method, "params": params})
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"{method}: {d['error']}")
    return d.get("result")


def next_checkpoint(graduated_at):
    for h in CHECKPOINTS_H:
        t = graduated_at + timedelta(hours=h)
        if t > now():
            return t
    return None


async def enroll(db):
    """Iscrive i token che risultano graduati e non ancora seguiti."""
    cur = await db.execute("""
        SELECT s.mint, MIN(s.taken_at) AS grad_at
        FROM snapshots s
        WHERE s.graduated = 1
          AND s.mint NOT IN (SELECT mint FROM amm_outcomes)
        GROUP BY s.mint""")
    rows = await cur.fetchall()

    n = 0
    for mint, grad_at in rows:
        try:
            g = parse(grad_at)
        except Exception:
            g = now()
        nxt = next_checkpoint(g)
        if nxt is None:
            continue                     # graduato da oltre 30 giorni
        await db.execute("""
            INSERT OR IGNORE INTO amm_outcomes
              (mint, graduated_at, next_check_at) VALUES (?,?,?)""",
            (mint, g.isoformat(), nxt.isoformat()))
        n += 1
    await db.commit()
    if n:
        log.info("iscritti %d token graduati", n)

    # anche quelli visti graduare dal tracker della curva
    try:
        cur = await db.execute("""
            SELECT mint FROM outcomes
            WHERE graduated = 1
              AND mint NOT IN (SELECT mint FROM amm_outcomes)""")
        extra = await cur.fetchall()
        for (mint,) in extra:
            await db.execute("""
                INSERT OR IGNORE INTO amm_outcomes
                  (mint, graduated_at, next_check_at) VALUES (?,?,?)""",
                (mint, now().isoformat(),
                 next_checkpoint(now()).isoformat()))
        if extra:
            await db.commit()
            log.info("iscritti %d token dal tracker della curva", len(extra))
    except Exception:
        pass        # la tabella outcomes puo' non esistere ancora


async def check_one(db, row):
    (mint, grad_at, owner, tok_acc, wsol_acc,
     entry_price, max_price, max_at, done) = row

    g = parse(grad_at)
    hours = (now() - g).total_seconds() / 3600

    pool = None
    if owner and tok_acc and wsol_acc:
        pool = {"pool_owner": owner, "token_account": tok_acc,
                "wsol_account": wsol_acc}

    try:
        if pool is None:
            pool = await find_pool(rpc, mint)
            if pool is None:
                # puo' essere presto: l'AMM potrebbe non essere ancora attivo
                nxt = next_checkpoint(g)
                await db.execute(
                    "UPDATE amm_outcomes SET checks_done=?, next_check_at=?, "
                    "note=? WHERE mint=?",
                    (done + 1, nxt.isoformat() if nxt else None,
                     "pool non trovato", mint))
                log.info("%s  t+%-5.1fh  pool non trovato", mint[:8], hours)
                return
        data = await read_pool(rpc, pool)
    except Exception as e:
        log.warning("%s: %s", mint[:8], e)
        return

    if not data:
        return

    price, liq = data["price_sol"], data["liq_sol"]
    if not entry_price:
        entry_price = price
    new_max, new_at = ((price, hours) if price > (max_price or 0)
                       else (max_price, max_at))

    nxt = next_checkpoint(g)
    await db.execute("""
        UPDATE amm_outcomes SET pool_owner=?, token_account=?, wsol_account=?,
               entry_price=COALESCE(entry_price,?), entry_liq=COALESCE(entry_liq,?),
               max_price=?, max_at_hours=?, last_price=?, last_liq=?,
               checks_done=?, next_check_at=?, note=NULL
        WHERE mint=?""",
        (pool["pool_owner"], pool["token_account"], pool["wsol_account"],
         price, liq, new_max, new_at, price, liq,
         done + 1, nxt.isoformat() if nxt else None, mint))

    mult = new_max / entry_price if entry_price else 0
    log.info("%s  t+%-5.1fh  liq=%8.2f SOL  max=%6.2fx", mint[:8], hours,
             liq, mult)


async def main():
    global _http
    _http = httpx.AsyncClient(timeout=25)
    db = await aiosqlite.connect(DB_PATH)
    await db.executescript(SCHEMA)
    await db.commit()
    log.info("amm_tracker avviato (fino a 30 giorni dalla graduation)")

    try:
        while True:
            try:
                await enroll(db)
                cur = await db.execute("""
                    SELECT mint, graduated_at, pool_owner, token_account,
                           wsol_account, entry_price, max_price,
                           max_at_hours, checks_done
                    FROM amm_outcomes
                    WHERE next_check_at IS NOT NULL AND next_check_at <= ?
                    ORDER BY next_check_at LIMIT ?""",
                    (now().isoformat(), BATCH))
                due = await cur.fetchall()
                for row in due:
                    await check_one(db, row)
                    await asyncio.sleep(0.4)
                await db.commit()
                if due:
                    log.info("%d controlli eseguiti", len(due))
            except Exception as e:
                log.error("ciclo fallito: %s", e)
            await asyncio.sleep(LOOP_SECONDS)
    finally:
        await db.close()
        await _http.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("uscita")
