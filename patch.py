#!/usr/bin/env python3
"""Applica i 3 fix ai file esistenti. Idempotente: rieseguirlo non fa danni."""
import io, re, sys

def edit(path, pairs):
    s = io.open(path, encoding="utf-8").read()
    done = []
    for name, marker, old, new in pairs:
        if marker in s:
            done.append(f"  = {name} (gia' applicato)"); continue
        if old not in s:
            done.append(f"  ! {name} NON TROVATO - controlla il file"); continue
        s = s.replace(old, new, 1); done.append(f"  + {name}")
    io.open(path, "w", encoding="utf-8").write(s)
    print(path); [print(d) for d in done]

edit("main.py", [
 ("versione tx", '"maxSupportedTransactionVersion": 1}])',
  '"maxSupportedTransactionVersion": 0}])',
  '"maxSupportedTransactionVersion": 1}])'),
 ("silenzia httpx (nasconde la API key)", 'getLogger("httpx")',
  'log = logging.getLogger("pumpwatch")',
  'logging.getLogger("httpx").setLevel(logging.WARNING)\n'
  'logging.getLogger("httpcore").setLevel(logging.WARNING)\n'
  'log = logging.getLogger("pumpwatch")'),
 ("import ATA", 'from pda import associated_token_address',
  'from curve import decode_curve, bonding_curve_pda',
  'from curve import decode_curve, bonding_curve_pda\n'
  'from pda import associated_token_address'),
 ("holder senza account curva", 'curve_ata =',
  '''    largest = await rpc("getTokenLargestAccounts", [mint])
    supply = await rpc("getTokenSupply", [mint])
    total = float(supply["value"]["uiAmount"] or 0)
    accts = (largest or {}).get("value", [])
    if total <= 0 or not accts:
        return {"top1": None, "top5": None, "count": len(accts)}
    amts = [float(a.get("uiAmount") or 0) for a in accts]
    return {"top1": amts[0] / total,
            "top5": sum(amts[:5]) / total,
            "count": len(accts)}''',
  '''    largest = await rpc("getTokenLargestAccounts", [mint])
    accts = (largest or {}).get("value", [])
    if not accts:
        return {"top1": None, "top5": None, "count": 0}
    curve_ata = associated_token_address(bonding_curve_pda(mint), mint)
    holders = [float(a.get("uiAmount") or 0) for a in accts
               if a.get("address") != curve_ata
               and float(a.get("uiAmount") or 0) > 0]
    circ = sum(holders)
    if circ <= 0:
        return {"top1": None, "top5": None, "count": 0}
    amts = sorted(holders, reverse=True)
    return {"top1": amts[0] / circ,
            "top5": sum(amts[:5]) / circ,
            "count": len(amts)}'''),
 ("log leggibile", 'hold=%3s',
  '''                log.info("t+%-4ss %s  top1=%5s%%  liq=%6s SOL  prog=%4s%%",
                         off, mint[:8],
                         round((holders["top1"] or 0) * 100, 1),
                         round(curve.get("liq") or 0, 2),
                         round((curve.get("progress") or 0) * 100, 1))''',
  '''                t1 = holders["top1"]
                log.info("t+%-4ss %s  top1=%6s  hold=%3s  liq=%7.2f SOL  prog=%5.1f%%",
                         off, mint[:8],
                         f"{t1*100:.1f}%" if t1 is not None else "  -  ",
                         holders["count"], curve.get("liq") or 0,
                         (curve.get("progress") or 0) * 100)'''),
])

edit("curve.py", [
 ("costante token vendibili", 'CURVE_SELLABLE',
  'TRADE_FEE = 0.01',
  'TRADE_FEE = 0.01\nCURVE_SELLABLE = 793_100_000 * 10 ** 6'),
 ("progress normalizzato", 'base = max(CURVE_SELLABLE',
  '''        sold = self.token_total_supply - self.real_token_reserves
        return min(max(sold / self.token_total_supply, 0.0), 1.0)''',
  '''        base = max(CURVE_SELLABLE, self.real_token_reserves)
        if base == 0:
            return 0.0
        sold = base - self.real_token_reserves
        return min(max(sold / base, 0.0), 1.0)'''),
])

need = 'def associated_token_address'
s = io.open("pda.py", encoding="utf-8").read()
if need in s:
    print("pda.py\n  = ATA (gia' presente)")
else:
    io.open("pda.py", "a", encoding="utf-8").write('''

TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"


def associated_token_address(owner: str, mint: str) -> str:
    ata, _ = find_program_address(
        [b58decode(owner), b58decode(TOKEN_PROGRAM_ID), b58decode(mint)],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    return ata
''')
    print("pda.py\n  + ATA aggiunta")

import ast
for f in ("main.py", "curve.py", "pda.py"):
    ast.parse(io.open(f, encoding="utf-8").read())
print("\nsintassi OK - ora: rm pumpwatch.db* && python main.py")
