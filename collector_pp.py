"""
pumpwatch collector: PumpPortal free stream + batched RPC reads.

Where the data comes from:
  - new tokens and migrations: PumpPortal WebSocket, free methods only
    (subscribeNewToken, subscribeMigration). No API key, no wallet.
    The create message already carries the creator and the initial
    curve state, so no getTransaction call is needed.
  - curve state at t+30s / 2min / 10min: getMultipleAccounts, up to
    100 bonding curves in a single RPC call.
  - holders: derived from the curve and the creator's initial buy,
    with no RPC call (see cheap_holders). Optional on-chain reads with
    HOLDER_RPC=1, for RPCs that allow getTokenLargestAccounts.
  - 24h tracking of tokens with a real market: batched curve reads
    every few minutes.

Rough budget at ~26k new tokens/day: ~17k batched curve reads plus
~9k holder reads, against ~300k calls/day with the old design. That
fits the public Solana RPC comfortably; on the Helius free plan
(~33k/day) it fits, but with little headroom.

It writes the same tables as before (mints, snapshots, outcomes).

Holder counts for tokens under 1 SOL are not read from chain:
  - if only the creator bought, count = 1 (or 0 if they bought nothing)
  - if someone else bought, count is stored as NULL (not measured)
This keeps "never bought by anyone" exact without an RPC call per token.

Usage:  python collector_pp.py
Env:    RPC_URL        default: public Solana RPC
        PUMPWATCH_DB   default: pumpwatch.db
        PP_DUMP        save the first N raw messages (default 50)
"""

import asyncio
import base64
import json
import logging
import os
import time
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone

import httpx
import websockets

import db as store
from curve import decode_curve
from pda import bonding_curve_pda, associated_token_address

WS_URL = os.environ.get("PP_WS_URL", "wss://pumpportal.fun/api/data")
RPC_URL = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")
DB_PATH = os.environ.get("PUMPWATCH_DB", "pumpwatch.db")

SNAPSHOT_OFFSETS = [int(x) for x in
                    os.environ.get("PP_OFFSETS", "30,120,600").split(",")]
LATE_TOLERANCE = 15            # seconds; a later snapshot is skipped
HOLDER_MIN_LIQ = 1.0           # read holders only above this
# Holder reads via getTokenLargestAccounts are off by default: the public
# Solana RPC throttles that method almost immediately, and the data shows
# holder concentration does not separate rugs. Set HOLDER_RPC=1 when
# running on an RPC that allows it (e.g. a paid Helius plan).
HOLDER_RPC = os.environ.get("HOLDER_RPC") == "1"
LONG_TRACK_SECONDS = int(os.environ.get("PP_LONG_SECONDS", 24 * 3600))
LONG_POLL_SECONDS = int(os.environ.get("PP_LONG_POLL", 180))
LONG_TRACK_MIN_LIQ = 1.0
DEAD_LIQ = 0.05
RPC_MIN_INTERVAL = float(os.environ.get("RPC_MIN_INTERVAL", 0.3))
# Snapshots are collected in rounds so several tokens share one RPC call.
# A token can be up to this many seconds late (well under LATE_TOLERANCE).
SNAP_EVERY = float(os.environ.get("PP_SNAP_EVERY", 5))
# Holders are only read at these offsets: the analyses compare t+30s
# with t+10min and never use the middle one.
HOLDER_OFFSETS = {SNAPSHOT_OFFSETS[0], SNAPSHOT_OFFSETS[-1]}
BATCH = 100
DUMP_N = int(os.environ.get("PP_DUMP", 50))

TOK_SELLABLE = 793_100_000.0

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("pp")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- RPC

class RPC:
    """Paced JSON-RPC client: one request every RPC_MIN_INTERVAL seconds."""

    def __init__(self, url):
        self.url = url
        self.client = httpx.AsyncClient(timeout=20)
        self.lock = asyncio.Lock()
        self.next_at = 0.0
        self.calls = 0

    async def call(self, method, params, attempts=3):
        for attempt in range(attempts):
            async with self.lock:
                wait = self.next_at - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self.next_at = time.monotonic() + RPC_MIN_INTERVAL
            self.calls += 1
            r = await self.client.post(self.url, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            if r.status_code == 429:
                if attempt + 1 < attempts:
                    await asyncio.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            d = r.json()
            if "error" in d:
                raise RuntimeError(f"{method}: {d['error']}")
            return d.get("result")
        raise RuntimeError(f"{method}: rate limited")

    async def curves(self, tokens):
        """Curve state for many tokens, 100 per request. {mint: CurveState}"""
        out = {}
        for i in range(0, len(tokens), BATCH):
            chunk = tokens[i:i + BATCH]
            res = await self.call("getMultipleAccounts",
                                  [[t.curve_addr for t in chunk],
                                   {"encoding": "base64"}])
            for t, acc in zip(chunk, (res or {}).get("value") or []):
                if acc and acc.get("data"):
                    try:
                        out[t.mint] = decode_curve(
                            base64.b64decode(acc["data"][0]))
                    except Exception:
                        pass
        return out

    async def holders(self, t):
        # one attempt only: a missing holder count must never stall
        # the snapshot round for every other token
        res = await self.call("getTokenLargestAccounts", [t.mint], attempts=1)
        amts = sorted(
            (float(a.get("uiAmount") or 0) for a in (res or {}).get("value", [])
             if a.get("address") != t.curve_ata
             and float(a.get("uiAmount") or 0) > 0),
            reverse=True)
        circ = sum(amts)
        if circ <= 0:
            return {"top1": None, "top5": None, "count": 0}
        return {"top1": amts[0] / circ, "top5": sum(amts[:5]) / circ,
                "count": len(amts)}


# -------------------------------------------------------------- state

class Token:
    __slots__ = ("mint", "creator", "initial_buy", "born", "born_iso",
                 "curve_addr", "curve_ata", "next_snap", "complete",
                 "long", "entry_price", "max_price", "max_at",
                 "seen_market", "max_sold")

    def __init__(self, mint, creator, initial_buy):
        self.mint = mint
        self.creator = creator
        self.initial_buy = initial_buy
        self.born = time.monotonic()
        self.born_iso = now_iso()
        self.curve_addr = bonding_curve_pda(mint)
        self.curve_ata = associated_token_address(self.curve_addr, mint)
        self.next_snap = 0
        self.complete = False
        self.long = False
        self.entry_price = None
        self.max_price = None
        self.max_at = None
        self.seen_market = False    # ever had >= HOLDER_MIN_LIQ
        self.max_sold = 0.0         # most tokens ever out of the curve

    def age(self):
        return time.monotonic() - self.born

    def cheap_holders(self, st):
        """
        Holders without an RPC call. Only valid for a token that never
        had a market: after a rug, few tokens are left out of the curve
        but the buyers still hold them, so the creator-only rule would
        wrongly report 1 holder.
        """
        sold = TOK_SELLABLE * st.progress
        self.max_sold = max(self.max_sold, sold)
        if self.max_sold <= self.initial_buy * 1.01 + 1:
            if self.initial_buy > 0:
                return {"top1": 1.0, "top5": 1.0, "count": 1}
            return {"top1": None, "top5": None, "count": 0}
        return {"top1": None, "top5": None, "count": None}


def curve_dict(st):
    return {"price": st.price_sol, "mcap": st.market_cap_sol,
            "liq": st.real_liquidity_sol, "progress": st.progress,
            "graduated": st.complete}


# ---------------------------------------------------------- collector

class Collector:
    def __init__(self, db, rpc):
        self.db = db
        self.rpc = rpc
        self.tokens = {}
        self.dumped = 0
        self.warned = False
        self.stats = {"new": 0, "snaps": 0, "skipped": 0, "holder_fail": 0}

    def dump(self, raw):
        if self.dumped < DUMP_N:
            with open("pp_sample.jsonl", "a", encoding="utf-8") as f:
                f.write(raw.strip() + "\n")
            self.dumped += 1

    async def on_message(self, raw):
        self.dump(raw)
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("message") and not msg.get("mint"):
            log.info("server: %s", msg["message"])
            return
        tx, mint = msg.get("txType"), msg.get("mint")
        if not mint:
            return
        if tx == "migrate" or (tx is None and "pool" in msg
                               and mint in self.tokens):
            t = self.tokens.get(mint)
            if t:
                t.complete = True
            return
        if tx != "create":
            return
        pool = msg.get("pool")
        if pool not in (None, "pump"):
            return                      # other launchpads: not our curve
        if mint in self.tokens:
            return
        if not self.warned and "traderPublicKey" not in msg:
            self.warned = True
            log.warning("create message without traderPublicKey; keys: %s",
                        sorted(msg.keys()))
        try:
            t = Token(mint, msg.get("traderPublicKey"),
                      float(msg.get("initialBuy") or 0))
        except Exception as e:
            log.warning("bad mint %s: %s", mint, e)
            return
        if await store.save_mint(self.db, mint, t.creator,
                                 msg.get("signature") or "", None, t.born_iso):
            self.tokens[mint] = t
            self.stats["new"] += 1

    # --- snapshots ------------------------------------------------------
    async def snapshot_round(self):
        due = []
        for t in list(self.tokens.values()):
            while t.next_snap < len(SNAPSHOT_OFFSETS):
                off = SNAPSHOT_OFFSETS[t.next_snap]
                if t.age() < off:
                    break
                t.next_snap += 1
                if t.age() - off > LATE_TOLERANCE:
                    self.stats["skipped"] += 1      # skip, never shift
                    continue
                due.append((t, off))
                break
        if not due:
            return
        states = await self.rpc.curves([t for t, _ in due])
        for t, off in due:
            st = states.get(t.mint)
            if st is None:
                self.stats["skipped"] += 1
                continue
            if st.real_liquidity_sol >= HOLDER_MIN_LIQ:
                t.seen_market = True
            # always run: it also keeps max_sold up to date
            h = t.cheap_holders(st)
            if HOLDER_RPC and t.seen_market and off in HOLDER_OFFSETS:
                try:
                    h = await self.rpc.holders(t)
                except Exception:
                    self.stats["holder_fail"] += 1
            await store.save_snapshot(self.db, t.mint, off, now_iso(),
                                      h, curve_dict(st))
            self.stats["snaps"] += 1
            if off == SNAPSHOT_OFFSETS[-1]:
                await self.maybe_start_long(t, st)

        # tokens done with snapshots and not tracked long: forget them
        for mint, t in list(self.tokens.items()):
            if t.next_snap >= len(SNAPSHOT_OFFSETS) and not t.long:
                del self.tokens[mint]

    # --- 24h tracking ---------------------------------------------------
    async def resume_long(self):
        """
        After a restart, pick up the 24h tracking that was in memory.
        Rows still inside their 24h window are resumed; older ones are
        closed with the last values written, so none stays open forever.
        """
        cur = await self.db.execute("""
            SELECT mint, started_at, entry_price, max_price, max_at_min
            FROM outcomes
            WHERE next_check_at IS NOT NULL AND entry_price > 0""")
        rows = await cur.fetchall()
        resumed = closed = 0
        now = datetime.now(timezone.utc)
        for mint, started, entry, mx, mx_at in rows:
            try:
                born = datetime.fromisoformat(started)
                if born.tzinfo is None:
                    born = born.replace(tzinfo=timezone.utc)
                age = (now - born).total_seconds()
            except Exception:
                age = LONG_TRACK_SECONDS
            if age >= LONG_TRACK_SECONDS or mint in self.tokens:
                await self.db.execute(
                    "UPDATE outcomes SET next_check_at=NULL WHERE mint=?",
                    (mint,))
                closed += 1
                continue
            try:
                t = Token(mint, None, 0.0)
            except Exception:
                continue
            t.born = time.monotonic() - age
            t.born_iso = born.isoformat()
            t.next_snap = len(SNAPSHOT_OFFSETS)
            t.seen_market = True
            t.long = True
            t.entry_price, t.max_price, t.max_at = entry, mx or entry, mx_at
            self.tokens[mint] = t
            resumed += 1
        await self.db.commit()
        if rows:
            log.info("24h tracking: resumed %d tokens, closed %d past 24h",
                     resumed, closed)

    async def maybe_start_long(self, t, st):
        if st.real_liquidity_sol < LONG_TRACK_MIN_LIQ or st.complete:
            return
        t.long = True
        t.entry_price = t.max_price = st.price_sol
        t.max_at = int(t.age() // 60)
        end = (datetime.fromisoformat(t.born_iso)
               + timedelta(seconds=LONG_TRACK_SECONDS)).isoformat()
        await self.db.execute("""
            INSERT OR IGNORE INTO outcomes
              (mint, entry_price, entry_liq, max_price, max_at_min,
               last_price, last_liq, graduated, checks_done,
               next_check_at, started_at)
            VALUES (?,?,?,?,?,?,?,0,0,?,?)""",
            (t.mint, t.entry_price, st.real_liquidity_sol, t.max_price,
             t.max_at, st.price_sol, st.real_liquidity_sol, end, t.born_iso))
        await self.db.commit()

    async def long_round(self):
        longs = [t for t in self.tokens.values() if t.long]
        if not longs:
            return
        states = await self.rpc.curves(longs)
        for t in longs:
            st = states.get(t.mint)
            if st is None:
                continue
            if st.price_sol > (t.max_price or 0):
                t.max_price = st.price_sol
                t.max_at = int(t.age() // 60)
            complete = t.complete or st.complete
            finished = (t.age() >= LONG_TRACK_SECONDS
                        or st.real_liquidity_sol < DEAD_LIQ or complete)
            await self.db.execute("""
                UPDATE outcomes SET max_price=?, max_at_min=?, last_price=?,
                       last_liq=?, graduated=?, checks_done=checks_done+1,
                       next_check_at=CASE WHEN ? THEN NULL ELSE next_check_at END
                WHERE mint=?""",
                (t.max_price, t.max_at, st.price_sol, st.real_liquidity_sol,
                 1 if complete else 0, 1 if finished else 0, t.mint))
            if finished:
                why = ("graduated" if complete else
                       "dead" if st.real_liquidity_sol < DEAD_LIQ else "24h")
                log.info("closed %s  max=%.2fx at %smin  (%s)", t.mint[:8],
                         t.max_price / t.entry_price if t.entry_price else 0,
                         t.max_at, why)
                del self.tokens[t.mint]
        await self.db.commit()

    # --- loops ----------------------------------------------------------
    async def scheduler(self):
        last_long = time.monotonic()
        last_snap = 0.0
        last_report = time.monotonic()
        while True:
            try:
                if time.monotonic() - last_snap >= SNAP_EVERY:
                    last_snap = time.monotonic()
                    await self.snapshot_round()
                if time.monotonic() - last_long >= LONG_POLL_SECONDS:
                    last_long = time.monotonic()
                    await self.long_round()
            except Exception as e:
                log.error("round failed: %s", e)
            if time.monotonic() - last_report >= 60:
                longs = sum(1 for t in self.tokens.values() if t.long)
                log.info("last 60s: %d new, %d snapshots, %d skipped, "
                         "%d rpc calls%s | tracking %d (%d long)",
                         self.stats["new"], self.stats["snaps"],
                         self.stats["skipped"], self.rpc.calls,
                         (f", {self.stats['holder_fail']} holder reads failed"
                          if self.stats["holder_fail"] else ""),
                         len(self.tokens), longs)
                self.stats = {"new": 0, "snaps": 0, "skipped": 0,
                              "holder_fail": 0}
                self.rpc.calls = 0
                last_report = time.monotonic()
            await asyncio.sleep(1)

    async def stream(self):
        backoff = 1
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=20,
                                              ping_timeout=20,
                                              max_size=4 << 20) as ws:
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    log.info("connected to PumpPortal (free streams)")
                    backoff = 1
                    async for raw in ws:
                        await self.on_message(raw)
            except Exception as e:
                log.error("stream disconnected (%s), retry in %ss", e, backoff)
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
    # host only: providers put API keys in the path or in the query
    log.info("db ready: %s | rpc: %s", DB_PATH,
             urlsplit(RPC_URL).netloc or "?")
    rpc = RPC(RPC_URL)
    c = Collector(db, rpc)
    await c.resume_long()
    try:
        await asyncio.gather(c.stream(), c.scheduler())
    finally:
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("exit")
