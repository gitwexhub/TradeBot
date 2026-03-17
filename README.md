# TradeBot

Automated trading bot for [Kalshi](https://kalshi.com) prediction markets. Trades economic data markets (CPI, unemployment, nonfarm payrolls, Fed funds rate) by detecting price lags relative to publicly available government data.

## Strategy

The bot monitors Kalshi markets for BLS and FRED economic series and compares market prices against what they *should* be given official data:

1. Fetch latest data from BLS (CPI, unemployment, NFP) and FRED (Fed funds rate) on a regular poll interval
2. For each open Kalshi market, compute the implied YES price based on whether the actual data value is above or below the market's threshold
3. If the market price lags the implied price by ≥15¢, generate a trade signal
4. During release windows (8–9am ET for BLS, 1:55–3pm ET for FOMC), switch to 30-second polling to catch new data the moment it drops

**Monitored Kalshi series:** `KXCPIYOY`, `KXCPICOREYOY`, `KXUNEMPLOYMENT`, `KXPAYROLLS`, `KXFED`

**Trade sizing:** $10/trade, max 5 open positions.

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/gitwexhub/TradeBot.git
cd TradeBot
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Kalshi API credentials

1. Create a Kalshi account at [kalshi.com](https://kalshi.com)
2. Go to **Settings → API** and generate an API key pair
3. Download your private key PEM file and save it to `./keys/kalshi_private.pem`
4. Set permissions: `chmod 600 ./keys/kalshi_private.pem`

### 3. Data feed API keys (free)

- **BLS API key** (recommended): Register at [data.bls.gov/registrationEngine](https://data.bls.gov/registrationEngine/) — free, raises daily limit from 25 to 500 requests
- **FRED API key** (required for Fed rate markets): Register at [fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html) — free

### 4. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
KALSHI_API_KEY=your-api-key-uuid
KALSHI_PRIVATE_KEY_PATH=./keys/kalshi_private.pem
BLS_API_KEY=your-bls-key        # optional but recommended
FRED_API_KEY=your-fred-key      # required for Fed funds rate markets
```

```bash
cp config.yaml.example config.yaml
```

The defaults in `config.yaml` are sensible — adjust `lag_threshold_cents` or `trade_size_usd` as needed.

## Running

```bash
source venv/bin/activate
python -m src.main
```

Run in the background:

```bash
nohup venv/bin/python -m src.main >> logs/tradebot.log 2>&1 &
```

Logs are written to `./logs/tradebot.log` (rotating, 10MB max).

## Configuration reference

`config.yaml`:

```yaml
strategy:
  lag_threshold_cents: 15     # Min price lag (in cents) to generate a signal
  trade_size_usd: 10.00       # Dollar amount per trade
  max_open_positions: 5       # Max simultaneous open positions

data_feeds:
  poll_interval_minutes: 5    # Normal polling interval
  release_poll_seconds: 30    # Fast polling during release windows

scheduler:
  scan_interval_minutes: 5    # How often to scan markets
  sync_interval_minutes: 60   # How often to sync positions with Kalshi
  snapshot_interval_days: 3   # How often to log P&L snapshots
```

## Project structure

```
src/
├── main.py              # Entry point: startup checks, scheduler loop
├── config.py            # Pydantic config models (config.yaml + .env)
├── auth.py              # RSA-PSS request signing
├── client.py            # Async Kalshi API client (httpx)
├── scheduler.py         # APScheduler: data poll, scan, sync, snapshot jobs
├── data_feeds/
│   ├── bls.py           # BLS API: CPI, unemployment, nonfarm payrolls
│   └── fred.py          # FRED API: Fed funds target rate
├── strategy/
│   ├── matcher.py       # Parses Kalshi market titles into structured specs
│   ├── pricer.py        # Computes implied YES price from economic data
│   ├── signals.py       # Signal evaluation: lag detection, trade sizing
│   ├── scanner.py       # Fetches open markets by series ticker
│   └── executor.py      # Order placement + position limit enforcement
├── db/
│   ├── connection.py    # aiosqlite wrapper
│   ├── migrations.py    # Schema creation
│   └── models.py        # DB row dataclasses
└── portfolio/
    ├── positions.py     # Syncs open positions with Kalshi API
    └── performance.py   # P&L snapshots
```

## When will it trade?

Markets for upcoming BLS releases typically open 1–2 weeks before the release date. The bot will have tradeable opportunities around:

- **NFP / unemployment**: First Friday of each month (data released ~8:30am ET)
- **CPI**: Mid-month (data released ~8:30am ET)
- **Fed funds rate**: After each FOMC meeting (~2pm ET, 8x/year)

The bot will log `no signals` until markets open and have liquidity.

## Running tests

```bash
pytest tests/ -v
```
