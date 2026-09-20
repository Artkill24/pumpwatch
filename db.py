"""Storage SQLite. Nessun server da installare, regge tranquillamente
decine di migliaia di mint."""

import aiosqlite

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS mints (
    mint       TEXT PRIMARY KEY,
    creator    TEXT,
    signature  TEXT NOT NULL,
    slot       INTEGER,
    seen_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    mint            TEXT NOT NULL,
    offset_seconds  INTEGER NOT NULL,
    taken_at        TEXT NOT NULL,
    -- holder
    top1_share      REAL,
    top5_share      REAL,
    holder_count    INTEGER,
    -- curva
    price_sol       REAL,
    market_cap_sol  REAL,
    real_liq_sol    REAL,
    progress        REAL,
    graduated       INTEGER,
    PRIMARY KEY (mint, offset_seconds)
);

CREATE INDEX IF NOT EXISTS idx_creator ON mints(creator);
"""


async def init(path):
    db = await aiosqlite.connect(path)
    await db.executescript(SCHEMA)
    await db.commit()
    return db


async def save_mint(db, mint, creator, sig, slot, ts):
    cur = await db.execute(
        "INSERT OR IGNORE INTO mints (mint,creator,signature,slot,seen_at) "
        "VALUES (?,?,?,?,?)",
        (mint, creator, sig, slot, ts),
    )
    await db.commit()
    return cur.rowcount > 0


async def save_snapshot(db, mint, offset, ts, holder, curve):
    await db.execute(
        """INSERT OR REPLACE INTO snapshots
           (mint,offset_seconds,taken_at,top1_share,top5_share,holder_count,
            price_sol,market_cap_sol,real_liq_sol,progress,graduated)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            mint, offset, ts,
            holder.get("top1"), holder.get("top5"), holder.get("count"),
            curve.get("price"), curve.get("mcap"), curve.get("liq"),
            curve.get("progress"), 1 if curve.get("graduated") else 0,
        ),
    )
    await db.commit()
