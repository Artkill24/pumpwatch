"""
find_program_address in Python puro.
Serve solo hashlib: niente solders, niente Rust, gira su Termux.
"""

import hashlib

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# parametri curva ed25519
_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P


def b58decode(s: str) -> bytes:
    num = 0
    for ch in s:
        num = num * 58 + _B58.index(ch)
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big")
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


def b58encode(b: bytes) -> str:
    num = int.from_bytes(b, "big")
    out = ""
    while num:
        num, rem = divmod(num, 58)
        out = _B58[rem] + out
    pad = len(b) - len(b.lstrip(b"\x00"))
    return "1" * pad + out


def _is_on_curve(data: bytes) -> bool:
    """True se i 32 byte sono un punto ed25519 valido (quindi NON una PDA)."""
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= _P:
        return False

    yy = (y * y) % _P
    u = (yy - 1) % _P
    v = (_D * yy + 1) % _P
    try:
        vinv = pow(v, _P - 2, _P)
    except ValueError:
        return False

    xx = (u * vinv) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * pow(2, (_P - 1) // 4, _P)) % _P
    if (x * x - xx) % _P != 0:
        return False
    if x == 0 and sign:
        return False
    return True


def find_program_address(seeds: list[bytes], program_id: str) -> tuple[str, int]:
    pid = b58decode(program_id)
    for bump in range(255, -1, -1):
        h = hashlib.sha256()
        for s in seeds:
            h.update(s)
        h.update(bytes([bump]))
        h.update(pid)
        h.update(b"ProgramDerivedAddress")
        cand = h.digest()
        if not _is_on_curve(cand):
            return b58encode(cand), bump
    raise ValueError("nessuna PDA trovata")


PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def bonding_curve_pda(mint: str) -> str:
    pda, _ = find_program_address(
        [b"bonding-curve", b58decode(mint)], PUMP_PROGRAM_ID
    )
    return pda


TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"


def associated_token_address(owner: str, mint: str) -> str:
    """ATA di un owner per un mint. Serve per identificare (ed escludere)
    l'account della bonding curve dal conteggio degli holder."""
    ata, _ = find_program_address(
        [b58decode(owner), b58decode(TOKEN_PROGRAM_ID), b58decode(mint)],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    return ata
