"""
Market context from CoinMarketCap.

The key stays on the server (CMC_API_KEY): the browser asks the
pumpwatch API, never CMC directly.

Every reading is also stored in the DB (at most once every 5 minutes),
so over time pump.fun activity can be compared with BTC and SOL.
"""

import json
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

CMC_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
SYMBOLS = ["BTC", "ETH", "SOL"]
CACHE_SECONDS = 60
STORE_EVERY_SECONDS = 300

_cache = {"at": 0.0, "data": None}
_last_store = 0.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    taken_at   TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    price_usd  REAL,
    change_24h REAL,
    PRIMARY KEY (taken_at, symbol)
);
"""


def _fetch_cmc(key):
    url = f"{CMC_URL}?symbol={','.join(SYMBOLS)}&convert=USD"
    req = urllib.request.Request(url, headers={
        "X-CMC_PRO_API_KEY": key, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _parse(payload):
    out = []
    data = payload.get("data") or {}
    for sym in SYMBOLS:
        item = data.get(sym)
        if isinstance(item, list):          # v2 format
            item = item[0] if item else None
        if not item:
            continue
        usd = (item.get("quote") or {}).get("USD") or {}
        out.append({"symbol": sym,
                    "price": usd.get("price"),
                    "change_24h": usd.get("percent_change_24h")})
    return out


def _store(db_path, quotes):
    global _last_store
    if time.time() - _last_store < STORE_EVERY_SECONDS:
        return
    ts = datetime.now(timezone.utc).isoformat()
    db = sqlite3.connect(db_path)
    try:
        db.executescript(SCHEMA)
        db.executemany(
            "INSERT OR IGNORE INTO market_snapshots VALUES (?,?,?,?)",
            [(ts, q["symbol"], q["price"], q["change_24h"]) for q in quotes])
        db.commit()
        _last_store = time.time()
    finally:
        db.close()


def market(db_path, fetch=_fetch_cmc):
    """
    Returns {"available": bool, "quotes": [...], "error": str|None}.
    Without a key the page still works, just without prices.
    """
    key = os.environ.get("CMC_API_KEY")
    if not key:
        return {"available": False, "quotes": [],
                "error": "CMC_API_KEY not set"}

    if _cache["data"] and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]

    try:
        quotes = _parse(fetch(key))
        result = {"available": bool(quotes), "quotes": quotes, "error": None}
        _cache.update(at=time.time(), data=result)
        try:
            _store(db_path, quotes)
        except Exception:
            pass                    # history must never block the page
        return result
    except Exception as e:
        # on error, keep the last good reading if there is one
        if _cache["data"]:
            return _cache["data"]
        return {"available": False, "quotes": [], "error": str(e)}
