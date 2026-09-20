"""
Esiti del tracking a lungo termine.
Risponde a: quanti token fanno 2x, 5x, 10x - e quali filtri li trovano.
Uso: python outcomes.py
"""

import os
import sqlite3

db = sqlite3.connect(os.environ.get("PUMPWATCH_DB", "pumpwatch.db"))
db.row_factory = sqlite3.Row
one = lambda s, *a: db.execute(s, a).fetchone()

try:
    tot = one("SELECT COUNT(*) c FROM outcomes")["c"]
except sqlite3.OperationalError:
    raise SystemExit("Nessuna tabella outcomes: fai girare tracker.py prima.")

if tot == 0:
    raise SystemExit("Tracker ancora senza dati: lascialo girare qualche ora.")

done = one("SELECT COUNT(*) c FROM outcomes WHERE next_check_at IS NULL")["c"]
print(f"token in tracking: {tot}   completati: {done}")
print("(i non completati hanno un massimo ancora provvisorio)\n")

print("=" * 52)
print("DISTRIBUZIONE DEI MOLTIPLICATORI")
print("=" * 52)
print("max_price / entry_price, cioe' il meglio che potevi ottenere")
print("entrando a t+10min e uscendo al picco.\n")

pool = one("SELECT COUNT(*) c FROM outcomes WHERE entry_price>0")["c"]
for t in (1.5, 2, 3, 5, 10, 20):
    c = one("SELECT COUNT(*) c FROM outcomes "
            "WHERE entry_price>0 AND max_price >= entry_price*?", t)["c"]
    print(f"  >= {t:>4}x   {c:>5}  {c*100/max(pool,1):5.1f}%")

med = one("""SELECT max_price/entry_price m FROM outcomes
             WHERE entry_price>0 ORDER BY m
             LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM outcomes
                             WHERE entry_price>0)""")
if med:
    print(f"\n  mediana: {med['m']:.2f}x")

r = one("""SELECT AVG(max_at_min) a FROM outcomes
           WHERE entry_price>0 AND max_price >= entry_price*2""")
if r and r["a"]:
    print(f"  chi fa almeno 2x tocca il picco in media a t+{int(r['a'])} min")

print()
print("=" * 52)
print("I FILTRI TROVANO I VINCENTI?")
print("=" * 52)
print("Per ogni filtro: quanti dei token che lo passano fanno >= 5x.\n")

BASE = """FROM outcomes o JOIN snapshots s
          ON s.mint=o.mint AND s.offset_seconds=30
          WHERE o.entry_price>0"""

bp = one(f"SELECT COUNT(*) c {BASE}")["c"]
bw = one(f"SELECT COUNT(*) c {BASE} AND o.max_price>=o.entry_price*5")["c"]
print(f"BASE: {bw}/{bp} = {bw*100/max(bp,1):.1f}% fanno 5x\n")

for cond, label in [
    ("s.real_liq_sol>50 AND s.holder_count<10", "liq>50 SOL e <10 holder"),
    ("s.real_liq_sol>50", "liq > 50 SOL a t+30s"),
    ("s.real_liq_sol BETWEEN 5 AND 50", "liq 5-50 SOL"),
    ("s.holder_count>=20", "20+ holder a t+30s"),
    ("s.top1_share<0.5", "top1 < 50%"),
    ("s.top1_share<0.5 AND s.holder_count>=20", "top1<50% e 20+ holder"),
]:
    p = one(f"SELECT COUNT(*) c {BASE} AND {cond}")["c"]
    w = one(f"SELECT COUNT(*) c {BASE} AND {cond} "
            f"AND o.max_price>=o.entry_price*5")["c"]
    if p < 10:
        print(f"  {label:<30} campione piccolo ({p})")
        continue
    print(f"  {label:<30} {w:>4}/{p:<5} = {w*100/p:5.1f}%  "
          f"({w*100/p - bw*100/max(bp,1):+.1f} pt)")

print()
print("=" * 52)
print("I MIGLIORI FINORA")
print("=" * 52)
for r in db.execute("""
    SELECT mint, max_price/entry_price m, max_at_min, last_liq, graduated
    FROM outcomes WHERE entry_price>0
    ORDER BY m DESC LIMIT 10"""):
    print(f"  {r['mint'][:16]}  {r['m']:7.2f}x  al minuto {r['max_at_min']:>4}"
          f"  liq ora {r['last_liq']:.1f} SOL"
          f"{'  GRADUATO' if r['graduated'] else ''}")
