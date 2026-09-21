"""
Wallet score: risk profile of a pump.fun creator.

It does not predict whether a token will go up. It reports what the
creator did before: how many tokens, how many rugged, how many were
never bought.

Everything comes from data collected by main.py. No estimates, no
model: just counts.
"""

import os
import sqlite3
from dataclasses import dataclass, asdict

DB_PATH = os.environ.get("PUMPWATCH_DB", "pumpwatch.db")

# A token counts as a rug if it had real liquidity at t+30s and lost
# more than half of it by t+10min.
MIN_LIQ = 1.0
RUG_DROP = 0.5

# Below this many tokens a profile is not informative.
MIN_TOKENS_FOR_VERDICT = 5


@dataclass
class WalletProfile:
    creator: str
    tokens_total: int          # tokens launched and observed
    tokens_measured: int       # with complete data (t+30s and t+600s)
    rugs: int                  # liquidity collapses
    with_liquidity: int        # reached at least MIN_LIQ SOL
    never_bought: int          # no holders at t+10min
    graduated: int
    rug_rate: float | None     # rugs / with_liquidity
    dead_rate: float | None    # never_bought / tokens_measured
    first_seen: str | None
    last_seen: str | None
    verdict: str
    verdict_code: str
    reasons: list[str]

    def to_dict(self):
        return asdict(self)


def _connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def profile_creator(creator: str, db=None) -> WalletProfile:
    own = db is None
    db = db or _connect()
    try:
        row = db.execute("""
            SELECT
              COUNT(DISTINCT m.mint) AS total,
              MIN(m.seen_at) AS first_seen,
              MAX(m.seen_at) AS last_seen
            FROM mints m WHERE m.creator = ?""", (creator,)).fetchone()

        total = row["total"] or 0
        if total == 0:
            return WalletProfile(creator, 0, 0, 0, 0, 0, 0, None, None,
                                 None, None, "Unknown", "unknown",
                                 ["No tokens from this wallet in the dataset."])

        stats = db.execute(f"""
            SELECT
              COUNT(*) AS measured,
              SUM(CASE WHEN a.real_liq_sol >= {MIN_LIQ} THEN 1 ELSE 0 END)
                AS with_liq,
              SUM(CASE WHEN a.real_liq_sol >= {MIN_LIQ}
                        AND b.real_liq_sol < a.real_liq_sol * {RUG_DROP}
                   THEN 1 ELSE 0 END) AS rugs,
              SUM(CASE WHEN b.holder_count <= 1 THEN 1 ELSE 0 END)
                AS never_bought,
              SUM(CASE WHEN b.graduated = 1 THEN 1 ELSE 0 END) AS graduated
            FROM mints m
            JOIN snapshots a ON a.mint = m.mint AND a.offset_seconds = 30
            JOIN snapshots b ON b.mint = m.mint AND b.offset_seconds = 600
            WHERE m.creator = ?""", (creator,)).fetchone()

        measured = stats["measured"] or 0
        with_liq = stats["with_liq"] or 0
        rugs = stats["rugs"] or 0
        never = stats["never_bought"] or 0
        grad = stats["graduated"] or 0

        rug_rate = (rugs / with_liq) if with_liq else None
        dead_rate = (never / measured) if measured else None

        code, verdict, reasons = _judge(total, measured, with_liq, rugs,
                                        never, grad, rug_rate, dead_rate)

        return WalletProfile(
            creator=creator, tokens_total=total, tokens_measured=measured,
            rugs=rugs, with_liquidity=with_liq, never_bought=never,
            graduated=grad, rug_rate=rug_rate, dead_rate=dead_rate,
            first_seen=row["first_seen"], last_seen=row["last_seen"],
            verdict=verdict, verdict_code=code, reasons=reasons)
    finally:
        if own:
            db.close()


def _judge(total, measured, with_liq, rugs, never, grad,
           rug_rate, dead_rate):
    """
    Explicit verdict. Every reason quotes the number behind it:
    the reader should be able to verify, not trust.
    Returns (code, label, reasons).
    """
    reasons = []

    if measured < MIN_TOKENS_FOR_VERDICT:
        reasons.append(
            f"Only {measured} tokens with complete data: too few for a "
            f"verdict (at least {MIN_TOKENS_FOR_VERDICT} needed).")
        if total > measured:
            reasons.append(
                f"{total} tokens seen in total, {total - measured} "
                f"without complete snapshots.")
        return "insufficient", "Not enough data", reasons

    # serial rugger
    if with_liq >= 3 and rug_rate is not None and rug_rate >= 0.5:
        reasons.append(
            f"{rugs} rugs out of {with_liq} tokens that had liquidity "
            f"({rug_rate:.0%}).")
        if total >= 20:
            reasons.append(f"Very active wallet: {total} tokens launched.")
        return "high_risk", "High risk", reasons

    # spam farm: launches a lot, nobody buys
    if total >= 20 and dead_rate is not None and dead_rate >= 0.8:
        reasons.append(
            f"{never} of {measured} tokens were never bought by anyone "
            f"within 10 minutes ({dead_rate:.0%}).")
        reasons.append(
            f"{total} launches in total: an automated farm, "
            f"not a project.")
        return "spam", "Spam", reasons

    if with_liq >= 3 and rug_rate is not None and rug_rate >= 0.25:
        reasons.append(
            f"{rugs} rugs out of {with_liq} tokens with liquidity "
            f"({rug_rate:.0%}).")
        return "medium_risk", "Medium risk", reasons

    if with_liq == 0:
        reasons.append(
            f"None of the {measured} measured tokens reached "
            f"{MIN_LIQ} SOL of liquidity: there was never a market.")
        return "no_market", "No market", reasons

    reasons.append(
        f"{rugs} rugs out of {with_liq} tokens with liquidity"
        + (f" ({rug_rate:.0%})." if rug_rate is not None else "."))
    if grad:
        reasons.append(f"{grad} tokens reached graduation.")
    reasons.append(
        "No negative signals in the collected data. Not a guarantee: "
        "the dataset only covers tokens this collector observed.")
    return "clean", "No negative signals", reasons


def profile_mint(mint: str, db=None):
    """Profile of the creator of a given token."""
    own = db is None
    db = db or _connect()
    try:
        row = db.execute(
            "SELECT creator FROM mints WHERE mint = ?", (mint,)).fetchone()
        if not row or not row["creator"]:
            return None
        return profile_creator(row["creator"], db)
    finally:
        if own:
            db.close()


def top_risky(limit=20, db=None):
    """Wallets with the most rugs in the dataset."""
    own = db is None
    db = db or _connect()
    try:
        rows = db.execute(f"""
            SELECT m.creator,
                   COUNT(*) AS measured,
                   SUM(CASE WHEN a.real_liq_sol >= {MIN_LIQ}
                             AND b.real_liq_sol < a.real_liq_sol * {RUG_DROP}
                        THEN 1 ELSE 0 END) AS rugs,
                   SUM(CASE WHEN a.real_liq_sol >= {MIN_LIQ} THEN 1 ELSE 0 END)
                     AS with_liq
            FROM mints m
            JOIN snapshots a ON a.mint = m.mint AND a.offset_seconds = 30
            JOIN snapshots b ON b.mint = m.mint AND b.offset_seconds = 600
            WHERE m.creator IS NOT NULL
            GROUP BY m.creator
            HAVING rugs > 0
            ORDER BY rugs DESC, with_liq DESC
            LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            db.close()


def last_24h(db=None):
    """
    What happened on pump.fun in the last 24 hours.
    Measurements only use tokens with snapshots at t+30s and t+600s.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    own = db is None
    db = db or _connect()
    try:
        born = db.execute(
            "SELECT COUNT(*) c FROM mints WHERE seen_at >= ?",
            (cutoff,)).fetchone()["c"]
        r = db.execute(f"""
            SELECT
              COUNT(*) AS measured,
              SUM(CASE WHEN a.real_liq_sol >= {MIN_LIQ} THEN 1 ELSE 0 END)
                AS with_liq,
              SUM(CASE WHEN a.real_liq_sol >= {MIN_LIQ}
                        AND b.real_liq_sol < a.real_liq_sol * {RUG_DROP}
                   THEN 1 ELSE 0 END) AS rugs,
              SUM(CASE WHEN b.holder_count <= 1 THEN 1 ELSE 0 END)
                AS never_bought,
              SUM(CASE WHEN a.progress >= 0.99 THEN 1 ELSE 0 END) AS bundled,
              SUM(CASE WHEN b.graduated = 1 THEN 1 ELSE 0 END) AS graduated
            FROM mints m
            JOIN snapshots a ON a.mint = m.mint AND a.offset_seconds = 30
            JOIN snapshots b ON b.mint = m.mint AND b.offset_seconds = 600
            WHERE m.seen_at >= ?""", (cutoff,)).fetchone()
        with_liq = r["with_liq"] or 0
        rugs = r["rugs"] or 0
        return {
            "born": born,
            "measured": r["measured"] or 0,
            "with_liq": with_liq,
            "rugs": rugs,
            "rug_rate": (rugs / with_liq) if with_liq else None,
            "never_bought": r["never_bought"] or 0,
            "bundled": r["bundled"] or 0,
            "graduated": r["graduated"] or 0,
        }
    finally:
        if own:
            db.close()


def dataset_stats(db=None):
    own = db is None
    db = db or _connect()
    try:
        r = db.execute("SELECT COUNT(*) c FROM mints").fetchone()
        s = db.execute("SELECT COUNT(DISTINCT mint) c FROM snapshots "
                       "WHERE offset_seconds=600").fetchone()
        w = db.execute("SELECT COUNT(DISTINCT creator) c FROM mints "
                       "WHERE creator IS NOT NULL").fetchone()
        last = db.execute("SELECT MAX(seen_at) t FROM mints").fetchone()
        return {"mints": r["c"], "measured": s["c"],
                "creators": w["c"], "last_seen": last["t"]}
    finally:
        if own:
            db.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python score.py <wallet|mint>")
        print("\nRiskiest wallets in the dataset:\n")
        for r in top_risky(10):
            print(f"  {r['creator']}  {r['rugs']} rugs "
                  f"out of {r['with_liq']} with liquidity")
        raise SystemExit

    key = sys.argv[1]
    p = profile_creator(key)
    if p.tokens_total == 0:
        p2 = profile_mint(key)
        if p2:
            print(f"(creator of token {key})\n")
            p = p2

    print(f"wallet   {p.creator}")
    print(f"verdict  {p.verdict.upper()}")
    print(f"tokens   {p.tokens_total} launched, {p.tokens_measured} measured")
    if p.with_liquidity:
        print(f"         {p.with_liquidity} with liquidity, {p.rugs} rugged")
    print()
    for r in p.reasons:
        print(f"  - {r}")
