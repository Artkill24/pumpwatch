#!/usr/bin/env python3
"""
Fix 4: copertura degli snapshot.

Due problemi:
1. MAX_TRACKS=150 era tarato per un telefono. Su PC sta stretto.
2. Il semaforo veniva acquisito PRIMA di far partire il cronometro:
   con la coda piena un task campionava a t+90s ma salvava "t+30s".
   Snapshot sbagliati, non solo mancanti.
"""
import io, ast

fixes = [
 ("piu' track in parallelo", "MAX_TRACKS = 400",
  "MAX_TRACKS = 150", "MAX_TRACKS = 400"),
 ("cronometro prima della coda", "t0 = asyncio.get_event_loop().time()\n    async with",
  """async def track(db, mint):
    \"\"\"Snapshot agli offset previsti, poi il task termina.\"\"\"
    async with _slots:
        t0 = asyncio.get_event_loop().time()
        for off in SNAPSHOT_OFFSETS:
            wait = off - (asyncio.get_event_loop().time() - t0)
            if wait > 0:
                await asyncio.sleep(wait)""",
  """async def track(db, mint):
    \"\"\"
    Snapshot agli offset previsti, poi il task termina.

    Il cronometro parte SUBITO, prima di mettersi in coda per uno slot:
    altrimenti con la coda piena si campiona in ritardo salvando
    l'etichetta sbagliata. Un checkpoint mancato si salta, non si sposta.
    \"\"\"
    t0 = asyncio.get_event_loop().time()
    async with _slots:
        for off in SNAPSHOT_OFFSETS:
            elapsed = asyncio.get_event_loop().time() - t0
            wait = off - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            elif wait < -15:
                # troppo in ritardo: meglio nessun dato che dato falso
                continue"""),
]

s = io.open("main.py", encoding="utf-8").read()
for name, marker, old, new in fixes:
    if marker in s:
        print(f"  = {name} (gia' applicato)")
    elif old in s:
        s = s.replace(old, new, 1)
        print(f"  + {name}")
    else:
        print(f"  ! {name} NON TROVATO")
io.open("main.py", "w", encoding="utf-8").write(s)

ast.parse(io.open("main.py", encoding="utf-8").read())
print("sintassi OK")
print("\nRiavvia SOLO il collector:")
print("  pkill -f main.py")
print("  python main.py > log.txt 2>&1 &")
print("  disown -a")
