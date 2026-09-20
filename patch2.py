#!/usr/bin/env python3
"""Fix 2: elimina i falsi positivi (USDC & co.) dal rilevamento del mint."""
import io, ast

OLD = '''    if not mint:
        for b in meta.get("postTokenBalances") or []:
            if b.get("mint"):
                mint = b["mint"]
                break'''

NEW = '''    if not mint:
        # Fallback: un mint NUOVO non puo' esistere prima della tx.
        # Senza questo controllo si finisce per registrare USDC o WSOL
        # solo perche' compaiono fra i token balance della transazione.
        pre = {b.get("mint") for b in (meta.get("preTokenBalances") or [])}
        for b in meta.get("postTokenBalances") or []:
            m = b.get("mint")
            if m and m not in pre:
                mint = m
                break'''

s = io.open("main.py", encoding="utf-8").read()
if "un mint NUOVO non puo' esistere" in s:
    print("  = fallback gia' corretto")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open("main.py", "w", encoding="utf-8").write(s)
    print("  + fallback ristretto ai mint non presenti prima della tx")
else:
    print("  ! blocco non trovato in main.py")

s = io.open("main.py", encoding="utf-8").read()
ast.parse(s)
print("sintassi OK")
