# score — creator risk profile

Given a wallet (or a token mint), shows what happened to the tokens that
creator launched before: how many, how many rugged, how many were never
bought.

These are counts over data gathered by the collector. No model, no
price prediction.

## Usage

From the `score/` folder, with the collector's DB reachable:

```bash
PUMPWATCH_DB=../pumpwatch.db python score.py            # riskiest wallets
PUMPWATCH_DB=../pumpwatch.db python score.py <wallet>   # one profile
PUMPWATCH_DB=../pumpwatch.db python api.py              # web page + API on :8000
```

## Verdicts

| verdict | when |
|---|---|
| High risk | ≥50% of tokens with liquidity rugged (min. 3) |
| Medium risk | ≥25% rugged |
| Spam | ≥20 launches and ≥80% never bought by anyone |
| No market | no token ever reached 1 SOL of liquidity |
| No negative signals | none of the above |
| Not enough data | fewer than 5 tokens with complete data |
| Unknown | wallet not in the dataset |

Every verdict comes with the reasons and the numbers behind them. The
API also returns a stable `verdict_code` (`high_risk`, `medium_risk`,
`spam`, `no_market`, `clean`, `insufficient`, `unknown`).

## Market context (CoinMarketCap)

The page shows BTC, ETH and SOL next to the pump.fun numbers, using the
CoinMarketCap API:

```bash
export CMC_API_KEY=...      # free key at pro.coinmarketcap.com
```

The key stays on the server: the browser asks pumpwatch, never CMC.
Responses are cached for 60 seconds, and every 5 minutes a reading is
stored in the `market_snapshots` table, so pump.fun activity can later
be compared with how the market moved.

Without a key the page still works, just without prices.

## API

```
GET /api/wallet/<address>   creator profile
GET /api/mint/<mint>        profile of that token's creator
GET /api/risky              wallets with the most rugs
GET /api/stats              dataset size
GET /api/today              pump.fun in the last 24 hours
GET /api/market             BTC, ETH, SOL from CoinMarketCap
```

Standard library only: nothing to install.

## Limits

- Only covers tokens this collector observed: a wallet that isn't
  listed isn't clean, it just hasn't been seen.
- A rug is defined as real liquidity falling below half between t+30s
  and t+10min, starting from at least 1 SOL.
- "No negative signals" is not a guarantee about the future.
