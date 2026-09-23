import struct
from dataclasses import dataclass
from pda import b58decode, b58encode, find_program_address

LAUNCHLAB_PROGRAM = "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"
WSOL_MINT = "So11111111111111111111111111111111111111112"
POOL_STATE_LEN = 429
O_STATUS = 17
O_BASE_DECIMALS = 18
O_QUOTE_DECIMALS = 19
O_NUMBERS = 21
O_BASE_MINT = 205
O_QUOTE_MINT = 237
O_BASE_VAULT = 269
O_QUOTE_VAULT = 301
O_CREATOR = 333

@dataclass
class BonkPool:
    status: int; base_decimals: int; quote_decimals: int
    supply: int; total_base_sell: int; virtual_base: int
    virtual_quote: int; real_base: int; real_quote: int
    base_mint: str; quote_mint: str; creator: str

    @property
    def liq_sol(self):
        return self.real_quote / 10 ** self.quote_decimals

    @property
    def price_sol(self):
        base = (self.virtual_base - self.real_base) / 10 ** self.base_decimals
        quote = (self.virtual_quote + self.real_quote) / 10 ** self.quote_decimals
        return quote / base if base > 0 else 0.0

    @property
    def progress(self):
        if self.total_base_sell <= 0:
            return 0.0
        return min(max(self.real_base / self.total_base_sell, 0.0), 1.0)

    @property
    def market_cap_sol(self):
        return self.price_sol * (self.supply / 10 ** self.base_decimals)

    @property
    def complete(self):
        return self.status != 0

def decode_pool(raw):
    if len(raw) < O_CREATOR + 32:
        raise ValueError(f"account too short: {len(raw)} bytes")
    n = struct.unpack_from("<10Q", raw, O_NUMBERS)
    k = lambda o: b58encode(raw[o:o + 32])
    return BonkPool(raw[O_STATUS], raw[O_BASE_DECIMALS], raw[O_QUOTE_DECIMALS],
                    n[0], n[1], n[2], n[3], n[4], n[5],
                    k(O_BASE_MINT), k(O_QUOTE_MINT), k(O_CREATOR))

def pool_pda(base_mint, quote_mint=WSOL_MINT):
    addr, _ = find_program_address(
        [b"pool", b58decode(base_mint), b58decode(quote_mint)],
        LAUNCHLAB_PROGRAM)
    return addr
