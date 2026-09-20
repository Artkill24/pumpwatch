"""
Wallet score: profilo di rischio di un creator pump.fun.

Non predice se un token salira'. Dice cosa ha fatto in passato chi
l'ha lanciato: quanti token, quanti ruggati, quanti mai comprati.

Tutto deriva dai dati raccolti da main.py. Nessuna stima, nessun
modello: sono conteggi.
"""

import os
import sqlite3
from dataclasses import dataclass, asdict

DB_PATH = os.environ.get("PUMPWATCH_DB", "pumpwatch.db")

# Un token conta come rug se aveva liquidita' vera a t+30s e ne ha
# persa piu' della meta' entro t+10min.
MIN_LIQ = 1.0
RUG_DROP = 0.5

# Sotto questo numero di token il profilo non e' informativo.
MIN_TOKENS_FOR_VERDICT = 5


@dataclass
class WalletProfile:
    creator: str
    tokens_total: int          # token lanciati e osservati
    tokens_measured: int       # con dati completi (t+30s e t+600s)
    rugs: int                  # crolli di liquidita'
    with_liquidity: int        # arrivati ad almeno MIN_LIQ SOL
    never_bought: int          # nessun holder a t+10min
    graduated: int
    rug_rate: float | None     # rugs / with_liquidity
    dead_rate: float | None    # never_bought / tokens_measured
    first_seen: str | None
    last_seen: str | None
    verdict: str
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
                                 None, None, "sconosciuto",
                                 ["Nessun token di questo wallet nel dataset."])

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

        verdict, reasons = _judge(total, measured, with_liq, rugs,
                                  never, grad, rug_rate, dead_rate)

        return WalletProfile(
            creator=creator, tokens_total=total, tokens_measured=measured,
            rugs=rugs, with_liquidity=with_liq, never_bought=never,
            graduated=grad, rug_rate=rug_rate, dead_rate=dead_rate,
            first_seen=row["first_seen"], last_seen=row["last_seen"],
            verdict=verdict, reasons=reasons)
    finally:
        if own:
            db.close()


def _judge(total, measured, with_liq, rugs, never, grad,
           rug_rate, dead_rate):
    """
    Verdetto esplicito. Ogni motivo cita il numero che lo sostiene:
    chi legge deve poter verificare, non fidarsi.
    """
    reasons = []

    if measured < MIN_TOKENS_FOR_VERDICT:
        reasons.append(
            f"Solo {measured} token con dati completi: troppo pochi per "
            f"un giudizio (ne servono almeno {MIN_TOKENS_FOR_VERDICT}).")
        if total > measured:
            reasons.append(
                f"{total} token visti in totale, ma {total - measured} "
                f"senza snapshot completi.")
        return "dati insufficienti", reasons

    # chi rugga in serie
    if with_liq >= 3 and rug_rate is not None and rug_rate >= 0.5:
        reasons.append(
            f"{rugs} rug su {with_liq} token che hanno avuto liquidità "
            f"({rug_rate:.0%}).")
        if total >= 20:
            reasons.append(f"Wallet molto attivo: {total} token lanciati.")
        return "alto rischio", reasons

    # farm di spam: lancia tanto, non lo compra nessuno
    if total >= 20 and dead_rate is not None and dead_rate >= 0.8:
        reasons.append(
            f"{never} token su {measured} non sono stati comprati da "
            f"nessuno entro 10 minuti ({dead_rate:.0%}).")
        reasons.append(
            f"{total} lanci complessivi: profilo da farm automatica, "
            f"non da progetto.")
        return "spam", reasons

    if with_liq >= 3 and rug_rate is not None and rug_rate >= 0.25:
        reasons.append(
            f"{rugs} rug su {with_liq} token con liquidità "
            f"({rug_rate:.0%}).")
        return "rischio medio", reasons

    # nessun segnale negativo
    if with_liq == 0:
        reasons.append(
            f"Nessuno dei {measured} token misurati ha raggiunto "
            f"{MIN_LIQ} SOL di liquidità: non c'è stato mercato.")
        return "nessun mercato", reasons

    reasons.append(
        f"{rugs} rug su {with_liq} token con liquidità"
        + (f" ({rug_rate:.0%})." if rug_rate is not None else "."))
    if grad:
        reasons.append(f"{grad} token arrivati alla graduation.")
    reasons.append(
        "Nessun segnale negativo nei dati raccolti. Non è una garanzia: "
        "il dataset copre solo i token osservati da questo collector.")
    return "nessun segnale negativo", reasons


def profile_mint(mint: str, db=None):
    """Profilo del creator di un dato token."""
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
    """I wallet con più rug nel dataset."""
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
        print("uso: python score.py <wallet|mint>")
        print("\nWallet più a rischio nel dataset:\n")
        for r in top_risky(10):
            print(f"  {r['creator']}  {r['rugs']} rug "
                  f"su {r['with_liq']} con liquidità")
        raise SystemExit

    key = sys.argv[1]
    p = profile_creator(key)
    if p.tokens_total == 0:
        p2 = profile_mint(key)
        if p2:
            print(f"(creator del token {key})\n")
            p = p2

    print(f"wallet   {p.creator}")
    print(f"verdetto {p.verdict.upper()}")
    print(f"token    {p.tokens_total} lanciati, {p.tokens_measured} misurati")
    if p.with_liquidity:
        print(f"         {p.with_liquidity} con liquidità, {p.rugs} ruggati")
    print()
    for r in p.reasons:
        print(f"  - {r}")
