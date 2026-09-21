"""
pumpwatch collector on the PumpPortal data stream.

Replaces main.py (Helius polling) and tracker.py (24h curve tracking)
with a single WebSocket connection and ZERO RPC calls:

  - new tokens arrive from subscribeNewToken
  - every buy/sell on a token arrives from subscribeTokenTrade,
    carrying the bonding curve state after the trade
  - holders are rebuilt from each trader's balance after every trade,
    so the count is exact instead of capped at 20
  - the price peak is seen when it happens, not sampled

It writes the same tables as before (mints, snapshots, outcomes), so
score/, analyze.py, outcomes.py and amm/ keep working unchanged.

Measurement rule, same as the Helius version: if the connection drops,
trades are missed and in-memory state is no longer exact. Every token
alive during a gap is marked tainted and gets no further snapshots.
Better no data than wrong data.

Usage:  python collector_pp.py
Env:    PUMPWATCH_DB   database path (default pumpwatch.db)
        PP_DUMP        save the first N raw messages to pp_sample.jsonl
                       (default 50) to verify field names
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import websockets

import db as store

WS_URL = os.environ.get("PP_WS_URL", "wss://pumpportal.fun/api/data")
DB_PATH = os.environ.get("PUMPWATCH_DB", "pumpwatch.db")

SNAPSHOT_OFFSETS = [int(x) for x in
                    os.environ.get("PP_OFFSETS", "30,120,600").split(",")]
LONG_TRACK_SECONDS = int(os.environ.get("PP_LONG_SECONDS", 24 * 3600))
LONG_TRACK_MIN_LIQ = 1.0      # follow for 24h only tokens with a real market
DEAD_LIQ = 0.05               # below this a long-tracked curve is dead
FLUSH_SECONDS = 60            # how often long-tracking state hits the DB
DUMP_N = int(os.environ.get("PP_DUMP", 50))

# pump.fun bonding curve at creation (UI units: SOL and whole tokens)
V_SOL_START = 30.0
V_TOK_START = 1_073_000_000.0
TOK_SELLABLE = 793_100_000.0
TOK_VIRTUAL_EXTRA = V_TOK_START - TOK_SELLABLE     # never sold
TOTAL_SUPPLY = 1_000_000_000.0

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pp")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Token:
    __slots__ = ("mint", "creator", "born", "born_iso", "vsol", "vtok",
                 "balances", "complete", "tainted", "next_snap",
                 "long", "entry_price", "max_price", "max_at", "dirty",
                 "last_flush")

    def __init__(self, mint, creator, born, born_iso):
        self.mint = mint
        self.creator = creator
        self.born = born                  # monotonic seconds
        self.born_iso = born_iso
        self.vsol = V_SOL_START
        self.vtok = V_TOK_START
        self.balances = {}
        self.complete = False
        self.tainted = False
        self.next_snap = 0                # index into SNAPSHOT_OFFSETS
        self.long = False
        self.entry_price = None
        self.max_price = None
        self.max_at = None
        self.dirty = False
        self.last_flush = 0.0

    # --- curve ---------------------------------------------------------
    @property
    def liq(self):
        """Real SOL in the curve: everything above the 30 virtual SOL."""
        return max(self.vsol - V_SOL_START, 0.0)

    @property
    def price(self):
        return self.vsol / self.vtok if self.vtok > 0 else 0.0

    @property
    def progress(self):
        real_tokens = max(self.vtok - TOK_VIRTUAL_EXTRA, 0.0)
        return min(max((TOK_SELLABLE - real_tokens) / TOK_SELLABLE, 0.0), 1.0)

    # --- holders -------------------------------------------------------
    def holders(self):
        amts = sorted((b for b in self.balances.values() if b > 0),
                      reverse=True)
        circ = sum(amts)
        if circ <= 0:
            return {"top1": None, "top5": None, "count": 0}
        return {"top1": amts[0] / circ,
                "top5": sum(amts[:5]) / circ,
                "count": len(amts)}

    def curve(self):
        return {"price": self.price,
                "mcap": self.price * TOTAL_SUPPLY,
                "liq": self.liq,
                "progress": self.progress,
                "graduated": self.complete or self.progress >= 0.999}

    # --- updates -------------------------------------------------------
    def apply(self, msg):
        vs = msg.get("vSolInBondingCurve")
        vt = msg.get("vTokensInBondingCurve")
        if vs is not None and vt is not None:
            self.vsol = float(vs)
            self.vtok = float(vt)
        trader = msg.get("traderPublicKey")
        bal = msg.get("newTokenBalance")
        if bal is None and msg.get("txType") == "create":
            bal = msg.get("initialBuy")
        if trader and bal is not None:
            self.balances[trader] = float(bal)
        if self.progress >= 0.999:
            self.complete = True
        if self.long and self.entry_price:
            p = self.price
            if p > (self.max_price or 0):
                self.max_price = p
                self.max_at = int((time.monotonic() - self.born) // 60)
            self.dirty = True


class Collector:
    def __init__(self, db):
        self.db = db
        self.tokens = {}
        self.ws = None
        self.connected = False
        self.dumped = 0
        self.warned_fields = False
        self.stats = {"new": 0, "trades": 0, "snaps": 0}

    # --- websocket ----------------------------------------------------
    async def send(self, payload):
        if self.ws is not None and self.connected:
            try:
                await self.ws.send(json.dumps(payload))
            except Exception as e:
                log.warning("send failed: %s", e)

    async def subscribe(self, mints):
        if mints:
            await self.send({"method": "subscribeTokenTrade",
                             "keys": list(mints)})

    async def unsubscribe(self, mints):
        if mints:
            await self.send({"method": "unsubscribeTokenTrade",
                             "keys": list(mints)})

    def dump(self, raw):
        if self.dumped >= DUMP_N:
            return
        with open("pp_sample.jsonl", "a", encoding="utf-8") as f:
            f.write(raw.strip() + "\n")
        self.dumped += 1
        if self.dumped == DUMP_N:
            log.info("saved %d raw messages to pp_sample.jsonl", DUMP_N)

    async def on_message(self, raw):
        self.dump(raw)
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        tx = msg.get("txType")
        mint = msg.get("mint")
        if not tx or not mint:
            if msg.get("message"):
                log.info("server: %s", msg["message"])
            return

        if tx == "create":
            await self.on_create(msg)
        elif tx in ("buy", "sell"):
            t = self.tokens.get(mint)
            if t:
                t.apply(msg)
                self.stats["trades"] += 1
        elif tx == "migrate":
            t = self.tokens.get(mint)
            if t:
                t.complete = True
                t.dirty = True

    async def on_create(self, msg):
        mint = msg["mint"]
        if mint in self.tokens:
            return
        if not self.warned_fields and (
                "vSolInBondingCurve" not in msg
                or "vTokensInBondingCurve" not in msg
                or "traderPublicKey" not in msg):
            self.warned_fields = True
            log.warning("create message without expected fields; "
                        "keys received: %s", sorted(msg.keys()))

        t = Token(mint, msg.get("traderPublicKey"),
                  time.monotonic(), now_iso())
        t.apply(msg)
        self.tokens[mint] = t
        self.stats["new"] += 1
        inserted = await store.save_mint(self.db, mint, t.creator,
                                         msg.get("signature") or "", None,
                                         t.born_iso)
        if inserted:
            await self.subscribe([mint])

    # --- scheduler ----------------------------------------------------
    async def tick(self):
        """Snapshots, long-tracking flushes and cleanup. Runs every second."""
        mono = time.monotonic()
        to_unsub = []
        for mint, t in list(self.tokens.items()):
            age = mono - t.born

            # fixed snapshots at 30s / 2min / 10min
            while (t.next_snap < len(SNAPSHOT_OFFSETS)
                   and age >= SNAPSHOT_OFFSETS[t.next_snap]):
                off = SNAPSHOT_OFFSETS[t.next_snap]
                t.next_snap += 1
                if t.tainted or not self.connected:
                    t.tainted = True
                    continue
                await store.save_snapshot(self.db, mint, off, now_iso(),
                                          t.holders(), t.curve())
                self.stats["snaps"] += 1
                if off == SNAPSHOT_OFFSETS[-1]:
                    await self.maybe_start_long(t)

            done_snaps = t.next_snap >= len(SNAPSHOT_OFFSETS)
            if not done_snaps:
                continue

            if not t.long:
                to_unsub.append(mint)
                del self.tokens[mint]
                continue

            # long tracking: stop at 24h, death, graduation or taint
            finished = (age >= LONG_TRACK_SECONDS or t.liq < DEAD_LIQ
                        or t.complete or t.tainted)
            if finished or (t.dirty and mono - t.last_flush >= FLUSH_SECONDS):
                await self.flush_long(t, final=finished)
            if finished:
                to_unsub.append(mint)
                del self.tokens[mint]

        await self.unsubscribe(to_unsub)

    async def maybe_start_long(self, t):
        if t.tainted or t.liq < LONG_TRACK_MIN_LIQ:
            return
        t.long = True
        t.entry_price = t.price
        t.max_price = t.price
        t.max_at = int((time.monotonic() - t.born) // 60)
        end = (datetime.fromisoformat(t.born_iso)
               + timedelta(seconds=LONG_TRACK_SECONDS)).isoformat()
        await self.db.execute("""
            INSERT OR IGNORE INTO outcomes
              (mint, entry_price, entry_liq, max_price, max_at_min,
               last_price, last_liq, graduated, checks_done,
               next_check_at, started_at)
            VALUES (?,?,?,?,?,?,?,?,0,?,?)""",
            (t.mint, t.entry_price, t.liq, t.max_price, t.max_at,
             t.price, t.liq, 1 if t.complete else 0, end, t.born_iso))
        await self.db.commit()

    async def flush_long(self, t, final):
        await self.db.execute("""
            UPDATE outcomes SET max_price=?, max_at_min=?, last_price=?,
                   last_liq=?, graduated=?, checks_done=checks_done+1,
                   next_check_at=CASE WHEN ? THEN NULL ELSE next_check_at END
            WHERE mint=?""",
            (t.max_price, t.max_at, t.price, t.liq,
             1 if t.complete else 0, 1 if final else 0, t.mint))
        await self.db.commit()
        t.dirty = False
        t.last_flush = time.monotonic()
        if final:
            mult = (t.max_price / t.entry_price) if t.entry_price else 0
            why = ("graduated" if t.complete else "dead" if t.liq < DEAD_LIQ
                   else "tainted" if t.tainted else "24h")
            log.info("closed %s  max=%.2fx at %smin  (%s)",
                     t.mint[:8], mult, t.max_at, why)

    async def scheduler(self):
        last_report = time.monotonic()
        while True:
            try:
                await self.tick()
            except Exception as e:
                log.error("tick failed: %s", e)
            if time.monotonic() - last_report >= 60:
                longs = sum(1 for t in self.tokens.values() if t.long)
                log.info("last 60s: %d new, %d trades, %d snapshots | "
                         "tracking %d tokens (%d long)",
                         self.stats["new"], self.stats["trades"],
                         self.stats["snaps"], len(self.tokens), longs)
                self.stats = {"new": 0, "trades": 0, "snaps": 0}
                last_report = time.monotonic()
            await asyncio.sleep(1)

    # --- connection loop ---------------------------------------------
    async def run(self):
        asyncio.create_task(self.scheduler())
        backoff = 1
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=20,
                                              ping_timeout=20,
                                              max_size=4 << 20) as ws:
                    self.ws = ws
                    self.connected = True
                    await self.send({"method": "subscribeNewToken"})
                    await self.send({"method": "subscribeMigration"})
                    log.info("connected to PumpPortal")
                    backoff = 1
                    async for raw in ws:
                        await self.on_message(raw)
            except Exception as e:
                log.error("disconnected (%s), retry in %ss", e, backoff)
            finally:
                self.connected = False
                self.ws = None
                # every token alive during the gap misses trades
                for t in self.tokens.values():
                    t.tainted = True
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def main():
    db = await store.init(DB_PATH)
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS outcomes (
            mint TEXT PRIMARY KEY, entry_price REAL, entry_liq REAL,
            max_price REAL, max_at_min INTEGER, last_price REAL,
            last_liq REAL, graduated INTEGER DEFAULT 0,
            checks_done INTEGER DEFAULT 0, next_check_at TEXT,
            started_at TEXT);""")
    await db.commit()
    log.info("db ready: %s", DB_PATH)
    try:
        await Collector(db).run()
    finally:
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("exit")
