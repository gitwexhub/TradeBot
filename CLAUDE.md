# CLAUDE.md — TradeBot

Kalshi prediction market trading bot. Near-resolution mispricing strategy.

## Commands

```bash
# Install deps
pip install -r requirements.txt

# Run the bot
python -m src.main

# Run tests
pytest tests/ -v
```

## Setup

1. Copy `.env.example` → `.env`, fill in `KALSHI_API_KEY` and `KALSHI_PRIVATE_KEY_PATH`
2. Generate RSA key pair in Kalshi dashboard → save private key to `./keys/kalshi_private.pem`
3. `chmod 600 ./keys/kalshi_private.pem`
4. Copy `config.yaml.example` → `config.yaml`
5. Run `python -m src.main`

## Architecture

```
src/
├── main.py            # Entrypoint: startup checks, scheduler loop
├── config.py          # Pydantic config models, loads config.yaml + .env
├── auth.py            # RSA-PSS request signer (Kalshi auth)
├── client.py          # KalshiClient: async httpx wrapper for all API calls
├── scheduler.py       # APScheduler wiring: scan / sync / snapshot jobs
├── db/
│   ├── connection.py  # aiosqlite wrapper, Database class
│   ├── migrations.py  # Schema creation
│   └── models.py      # Dataclasses for DB rows
├── strategy/
│   ├── signals.py     # Pure signal evaluation (price thresholds)
│   ├── scanner.py     # Paginated market scan + filtering
│   └── executor.py    # Order placement + position limit enforcement
└── portfolio/
    ├── positions.py   # Syncs open positions with Kalshi API
    └── performance.py # 3-day P&L snapshots
```

## Strategy

**Current: BLS data-lag strategy** — exploit Kalshi markets that haven't priced in already-released economic data.

Every 5 minutes (fast-poll 30s during 8–9am ET release windows):
1. Fetch BLS/FRED data for unemployment, CPI YoY, NFP, and Fed funds rate
2. Scan all open Kalshi markets, parse titles into structured specs (matcher.py)
3. Compute implied probability from actual data vs. market threshold (pricer.py)
4. If `abs(implied - market_price) >= lag_threshold_cents` (default 15¢), generate signal
5. Rank signals by confidence (largest lag first), place $10 limit orders, max 5 open positions

**Supported market types**: unemployment rate, CPI YoY, nonfarm payrolls, Fed funds rate upper bound.

**Why this strategy**: The original approach (buy YES at 70¢+, buy NO at 30¢−, near-resolution) made zero trades — those extreme-priced markets are either already correctly priced or illiquid. The data-lag approach targets markets where Kalshi prices haven't caught up to a BLS release that happened minutes ago.

**Known failure modes**:
- Markets that match the title parser but use non-standard phrasing get skipped (`parse_market` returns None) — check logs for "no match" to find missed opportunities
- BLS data can be revised post-release; the bot trades on the initial release value
- Fed rate markets resolve on the FOMC decision date, not the release date — timing matters

## Auth

Kalshi uses RSA-PSS signed API keys. Message to sign:
`f"{timestamp_ms}{METHOD}{/trade-api/v2/path}"`

Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, `KALSHI-ACCESS-TIMESTAMP`

## Key API Notes

- Base URL: `https://api.elections.kalshi.com/trade-api/v2`
- Market endpoints are public (no auth). Portfolio endpoints require auth.
- Pagination via `cursor` field in response. Iterate until cursor is None/empty.
- Order body: `{ticker, action:"buy", type:"limit", side:"yes"|"no", yes_price|no_price, count}`
- Positions response: `market_positions[].position` (positive=YES held, negative=NO held)

## Known Gotchas

- Sign with the FULL path including `/trade-api/v2` prefix (not just the endpoint)
- `yes_ask` and `no_ask` are in cents (1–99). Skip markets where either is 0 or None.
- The `UNIQUE` constraint on `trades.market_ticker` prevents double-entry at the DB level
- APScheduler jobs use `max_instances=1` to prevent overlapping runs
- BLS API is rate-limited: 25 req/day normally, 500 req/day during release windows. The fast-poll mode (`release_poll_seconds: 30`) during 8–9am ET is the window where the edge actually exists — don't reduce polling there.
- NFP threshold in the matcher is stored in raw job count (e.g. 200000), but BLS returns values in thousands — conversion happens in `pricer.py`. Don't "fix" this apparent mismatch.
- The old near-resolution strategy (70¢/30¢ threshold) was replaced because it never triggered — those prices reflect genuine market consensus, not mispricing.
