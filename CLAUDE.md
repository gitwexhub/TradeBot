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

Every 30 minutes:
1. Fetch all open Kalshi markets
2. Filter: closing within `resolution_window_hours` (default 48h)
3. Signal YES: `yes_ask >= 70¢` → price should converge to ~100¢ at resolution
4. Signal NO: `yes_ask <= 30¢` → equivalent to `no_ask >= 70¢`
5. Place $10 limit orders, max 5 simultaneous open positions

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
