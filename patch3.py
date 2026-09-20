#!/usr/bin/env python3
"""Fix 3: checkpoint fitti nei primi minuti, dove sta il picco."""
import io, ast

OLD = "CHECKPOINTS_MIN = [30, 60, 120, 240, 480, 720, 1440]"
NEW = """CHECKPOINTS_MIN = [12, 15, 20, 25, 30, 40, 50, 60, 90, 120,
                   180, 240, 360, 480, 720, 1440]"""

s = io.open("tracker.py", encoding="utf-8").read()
if "CHECKPOINTS_MIN = [12," in s:
    print("  = checkpoint gia' infittiti")
elif OLD in s:
    io.open("tracker.py", "w", encoding="utf-8").write(s.replace(OLD, NEW, 1))
    print("  + checkpoint infittiti: 16 punti invece di 7")
    print("    (12,15,20,25,30,40,50,60,90,120,180,240,360,480,720,1440 min)")
else:
    print("  ! CHECKPOINTS_MIN non trovato")

ast.parse(io.open("tracker.py", encoding="utf-8").read())
print("sintassi OK")
print("\nOra riavvia SOLO il tracker (il collector lascialo stare):")
print("  pkill -f tracker.py")
print("  nohup python tracker.py > tracker.log 2>&1 &")
