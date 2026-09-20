"""
Bonding curve pump.fun: decodifica e metriche.
Nessuna dipendenza esterna (usa pda.py).
"""

import struct
from dataclasses import dataclass

from pda import b58encode, bonding_curve_pda  # noqa: F401  (riesportato)

LAMPORTS_PER_SOL = 1_000_000_000
TOKEN_UNIT = 10 ** 6          # pump.fun usa 6 decimali
TRADE_FEE = 0.01              # 1% in entrata, 1% in uscita

# Una curva pump.fun nasce con 793.1M token vendibili su 1B di supply:
# i restanti 206.9M sono riservati alla migrazione, NON sono in vendita.
# Il progress va misurato su questa base, altrimenti ogni token nuovo
# sembra gia' al 20.7%.
CURVE_SELLABLE = 793_100_000 * TOKEN_UNIT


@dataclass
class CurveState:
    virtual_token_reserves: int
    virtual_sol_reserves: int
    real_token_reserves: int
    real_sol_reserves: int
    token_total_supply: int
    complete: bool
    creator: str | None = None

    @property
    def price_sol(self) -> float:
        if self.virtual_token_reserves == 0:
            return 0.0
        return (self.virtual_sol_reserves / LAMPORTS_PER_SOL) / (
            self.virtual_token_reserves / TOKEN_UNIT
        )

    @property
    def market_cap_sol(self) -> float:
        return self.price_sol * (self.token_total_supply / TOKEN_UNIT)

    @property
    def real_liquidity_sol(self) -> float:
        """I SOL VERI nella curva: tutto cio' che puo' davvero uscire."""
        return self.real_sol_reserves / LAMPORTS_PER_SOL

    @property
    def progress(self) -> float:
        """Avanzamento verso la graduation. 0.0 = token appena nato."""
        base = max(CURVE_SELLABLE, self.real_token_reserves)
        if base == 0:
            return 0.0
        sold = base - self.real_token_reserves
        return min(max(sold / base, 0.0), 1.0)

    def tokens_out(self, sol_in: float) -> float:
        net = sol_in * (1 - TRADE_FEE) * LAMPORTS_PER_SOL
        k = self.virtual_sol_reserves * self.virtual_token_reserves
        new_tok = k / (self.virtual_sol_reserves + net)
        return (self.virtual_token_reserves - new_tok) / TOKEN_UNIT

    def sol_out(self, tokens_in: float) -> float:
        amt = tokens_in * TOKEN_UNIT
        k = self.virtual_sol_reserves * self.virtual_token_reserves
        new_sol = k / (self.virtual_token_reserves + amt)
        return ((self.virtual_sol_reserves - new_sol) * (1 - TRADE_FEE)) / LAMPORTS_PER_SOL

    def round_trip_loss(self, sol_in: float) -> float:
        """Costo minimo garantito di un trade completo, prezzo fermo."""
        return (sol_in - self.sol_out(self.tokens_out(sol_in))) / sol_in


def decode_curve(raw: bytes) -> CurveState:
    """
    8 byte discriminator Anchor, poi 5 u64 LE, 1 bool, opzionale pubkey creator.
    ATTENZIONE: il layout e' cambiato tra versioni del programma.
    Verifica contro l'IDL corrente prima di fidarti in produzione.
    """
    if len(raw) < 49:
        raise ValueError(f"account troppo corto: {len(raw)} byte")
    body = raw[8:]
    vtok, vsol, rtok, rsol, supply = struct.unpack_from("<QQQQQ", body, 0)
    complete = bool(body[40])
    creator = None
    if len(body) >= 73 and any(body[41:73]):
        creator = b58encode(body[41:73])
    return CurveState(vtok, vsol, rtok, rsol, supply, complete, creator)
