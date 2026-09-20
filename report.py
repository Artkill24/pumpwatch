"""Riepilogo del dataset raccolto. Uso: python report.py"""
import sqlite3, sys, os
db = sqlite3.connect(os.environ.get("PUMPWATCH_DB","pumpwatch.db"))
db.row_factory = sqlite3.Row
n = db.execute("SELECT COUNT(*) c FROM mints").fetchone()["c"]
s = db.execute("SELECT COUNT(*) c FROM snapshots").fetchone()["c"]
print(f"mint registrati: {n}   snapshot: {s}")
print("\n-- wallet con piu' lanci --")
for r in db.execute("""SELECT creator, COUNT(*) n FROM mints
                       WHERE creator IS NOT NULL
                       GROUP BY creator HAVING n>1 ORDER BY n DESC LIMIT 10"""):
    print(f"  {r['creator']}  {r['n']} lanci")
print("\n-- liquidita' persa fra t+30s e t+10min --")
for r in db.execute("""
    SELECT a.mint, a.real_liq_sol AS l30, b.real_liq_sol AS l600
    FROM snapshots a JOIN snapshots b ON a.mint=b.mint
    WHERE a.offset_seconds=30 AND b.offset_seconds=600
      AND a.real_liq_sol>0.5 AND b.real_liq_sol < a.real_liq_sol*0.5
    ORDER BY a.real_liq_sol DESC LIMIT 15"""):
    print(f"  {r['mint'][:16]}  {r['l30']:.2f} -> {r['l600']:.2f} SOL")

print("\n-- wallet con lanci mai comprati da nessuno (farm) --")
for r in db.execute("""
    SELECT m.creator, COUNT(DISTINCT m.mint) lanci,
           SUM(CASE WHEN s.holder_count<=1 THEN 1 ELSE 0 END) morti
    FROM mints m LEFT JOIN snapshots s
      ON s.mint=m.mint AND s.offset_seconds=600
    WHERE m.creator IS NOT NULL
    GROUP BY m.creator HAVING lanci>2
    ORDER BY lanci DESC LIMIT 15"""):
    print(f"  {r['creator']}  {r['lanci']} lanci, {r['morti']} senza compratori")

