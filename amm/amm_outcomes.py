"""
Esiti post-graduation: i moltiplicatori grossi avvengono sull'AMM?
Uso: python amm_outcomes.py
"""
import os, sqlite3

db = sqlite3.connect(os.environ.get("PUMPWATCH_DB", "pumpwatch.db"))
db.row_factory = sqlite3.Row
one = lambda s, *a: db.execute(s, a).fetchone()

try:
    tot = one("SELECT COUNT(*) c FROM amm_outcomes")["c"]
except sqlite3.OperationalError:
    raise SystemExit("Nessuna tabella amm_outcomes: avvia amm_tracker.py.")
if tot == 0:
    raise SystemExit("Nessun token graduato ancora in tracking.")

withp = one("SELECT COUNT(*) c FROM amm_outcomes WHERE entry_price>0")["c"]
nopool = one("SELECT COUNT(*) c FROM amm_outcomes WHERE note IS NOT NULL")["c"]
print(f"token graduati in tracking: {tot}")
print(f"  con prezzo AMM letto: {withp}")
print(f"  pool non ancora trovato: {nopool}\n")

if withp == 0:
    raise SystemExit("Ancora nessun prezzo AMM: lascia girare qualche ora.")

print("="*52)
print("MOLTIPLICATORI DOPO LA GRADUATION")
print("="*52)
print("max sull'AMM / primo prezzo letto dopo la graduation\n")
for t in (1.5, 2, 3, 5, 10, 20, 50):
    c = one("SELECT COUNT(*) c FROM amm_outcomes "
            "WHERE entry_price>0 AND max_price>=entry_price*?", t)["c"]
    print(f"  >= {t:>4}x   {c:>4}  {c*100/withp:5.1f}%")

r = one("""SELECT AVG(max_at_hours) a FROM amm_outcomes
           WHERE entry_price>0 AND max_price>=entry_price*2""")
if r and r["a"]:
    print(f"\n  chi fa 2x tocca il picco in media a t+{r['a']:.1f}h "
          f"dalla graduation")

print("\n" + "="*52)
print("I MIGLIORI")
print("="*52)
for r in db.execute("""
    SELECT mint, max_price/entry_price m, max_at_hours, last_liq, checks_done
    FROM amm_outcomes WHERE entry_price>0
    ORDER BY m DESC LIMIT 15"""):
    print(f"  {r['mint'][:16]}  {r['m']:8.2f}x  a t+{r['max_at_hours'] or 0:6.1f}h"
          f"  liq ora {r['last_liq'] or 0:9.2f} SOL  ({r['checks_done']} check)")
