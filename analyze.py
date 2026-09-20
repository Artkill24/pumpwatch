"""
Analisi del dataset pumpwatch.
Prima misura quanto e' affidabile, poi cosa dice.
Uso: python analyze.py
"""

import os
import sqlite3

db = sqlite3.connect(os.environ.get("PUMPWATCH_DB", "pumpwatch.db"))
db.row_factory = sqlite3.Row
q = lambda s, *a: db.execute(s, a).fetchall()
one = lambda s, *a: db.execute(s, a).fetchone()

print("=" * 52)
print("1. AFFIDABILITA' DEL DATASET")
print("=" * 52)

tot = one("SELECT COUNT(*) c FROM mints")["c"]
print(f"mint registrati: {tot}")
for off in (30, 120, 600):
    n = one("SELECT COUNT(*) c FROM snapshots WHERE offset_seconds=?", off)["c"]
    print(f"  snapshot t+{off:<4}s: {n:>6}  ({n*100//max(tot,1)}% dei mint)")

full = one("""SELECT COUNT(*) c FROM (
    SELECT mint FROM snapshots GROUP BY mint
    HAVING COUNT(DISTINCT offset_seconds)=3)""")["c"]
print(f"\nmint con tutti e 3 gli snapshot: {full}  ({full*100//max(tot,1)}%)")
print("Le analisi sotto usano SOLO questi: il resto e' incompleto.")

print()
print("=" * 52)
print("2. COSA SUCCEDE AI TOKEN (solo dati completi)")
print("=" * 52)

BASE = """
FROM snapshots a
JOIN snapshots b ON b.mint=a.mint AND b.offset_seconds=600
WHERE a.offset_seconds=30
  AND a.mint IN (SELECT mint FROM snapshots GROUP BY mint
                 HAVING COUNT(DISTINCT offset_seconds)=3)
"""

n = one(f"SELECT COUNT(*) c {BASE}")["c"]
if n == 0:
    print("Nessun mint con dati completi: lascia girare piu' a lungo.")
    raise SystemExit

def pct(where, label):
    c = one(f"SELECT COUNT(*) c {BASE} AND {where}")["c"]
    print(f"  {label:<44} {c:>5}  {c*100/n:5.1f}%")

print(f"campione: {n} token\n")
pct("b.holder_count<=1", "mai comprati da nessuno (t+10min)")
pct("a.real_liq_sol<0.1 AND b.real_liq_sol<0.1", "liquidita' sempre nulla")
pct("a.real_liq_sol>=1 AND b.real_liq_sol < a.real_liq_sol*0.5",
    "liquidita' crollata >50% (RUG)")
pct("b.real_liq_sol > a.real_liq_sol*2", "liquidita' raddoppiata (crescita)")
pct("b.graduated=1", "graduati")

print()
print("=" * 52)
print("3. I SEGNALI SEPARANO DAVVERO?")
print("=" * 52)
print("Confronto il tasso di rug dentro e fuori ogni filtro.")
print("Un filtro utile ha un tasso molto diverso dalla base.\n")

RUG = "a.real_liq_sol>=1 AND b.real_liq_sol < a.real_liq_sol*0.5"
base_pool = one(f"SELECT COUNT(*) c {BASE} AND a.real_liq_sol>=1")["c"]
base_rug = one(f"SELECT COUNT(*) c {BASE} AND {RUG}")["c"]
print(f"BASE (tutti i token con >=1 SOL a t+30s): "
      f"{base_rug}/{base_pool} = {base_rug*100/max(base_pool,1):.1f}% rug\n")

for cond, label in [
    ("a.top1_share>0.5", "top1 > 50%"),
    ("a.top1_share>0.8", "top1 > 80%"),
    ("a.holder_count<10", "meno di 10 holder"),
    ("a.real_liq_sol>50", "liquidita' > 50 SOL"),
    ("a.real_liq_sol>50 AND a.holder_count<10",
     "liq > 50 SOL E meno di 10 holder"),
    ("a.top1_share>0.7 AND a.real_liq_sol>20",
     "top1 > 70% E liq > 20 SOL"),
]:
    pool = one(f"SELECT COUNT(*) c {BASE} AND a.real_liq_sol>=1 AND {cond}")["c"]
    rug = one(f"SELECT COUNT(*) c {BASE} AND {RUG} AND {cond}")["c"]
    if pool < 10:
        print(f"  {label:<36} campione troppo piccolo ({pool})")
        continue
    r = rug * 100 / pool
    delta = r - base_rug * 100 / max(base_pool, 1)
    print(f"  {label:<36} {rug:>4}/{pool:<5} = {r:5.1f}%  ({delta:+.1f} pt)")

print()
print("=" * 52)
print("4. WALLET: quanti dei loro token ruggano")
print("=" * 52)
rows = q(f"""
    SELECT m.creator,
           COUNT(*) n,
           SUM(CASE WHEN {RUG} THEN 1 ELSE 0 END) rug,
           SUM(CASE WHEN b.holder_count<=1 THEN 1 ELSE 0 END) morti
    {BASE} AND m.creator IS NOT NULL
    GROUP BY m.creator HAVING n>=5
    ORDER BY n DESC LIMIT 15""".replace(
        "FROM snapshots a", "FROM snapshots a JOIN mints m ON m.mint=a.mint"))
if not rows:
    print("Nessun wallet con >=5 token a dati completi ancora.")
for r in rows:
    print(f"  {r['creator'][:20]}...  {r['n']:>3} token, "
          f"{r['rug']:>3} rug, {r['morti']:>3} mai comprati")
