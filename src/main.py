import asyncio
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path

from .auth import load_private_key
from .client import KalshiClient
from .config import Config
from .db.connection import Database
from .scheduler import build_scheduler


def setup_logging(config: Config):
    config.logging.file.parent.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            config.logging.file,
            maxBytes=config.logging.max_bytes,
            backupCount=config.logging.backup_count,
        ),
    ]
    logging.basicConfig(
        level=config.logging.level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=handlers,
    )


def startup_checks(config: Config):
    """Fail fast with clear messages if configuration is invalid."""
    if not config.kalshi.api_key:
        sys.exit("ERROR: KALSHI_API_KEY is not set. Add it to your .env file.")

    key_path = config.kalshi.private_key_path
    if not key_path.exists():
        sys.exit(
            f"ERROR: Private key not found at {key_path}. "
            "Generate a key pair in the Kalshi dashboard and save the PEM file there."
        )

    # Check key file permissions (warn if readable by others)
    mode = os.stat(key_path).st_mode
    if mode & 0o077:
        logging.warning(
            f"Private key {key_path} has broad permissions ({oct(mode)}). "
            "Run: chmod 600 %s", key_path
        )

    # Verify the key loads and can sign
    try:
        load_private_key(key_path)
    except Exception as e:
        sys.exit(f"ERROR: Cannot load private key from {key_path}: {e}")

    logging.getLogger(__name__).info("Startup checks passed")


async def main():
    config = Config.load()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    logger.info("TradeBot starting up")
    startup_checks(config)

    db = Database(config.database.path)
    await db.connect()

    client = KalshiClient(config.kalshi)

    # Quick connectivity check
    try:
        balance = await client.get_balance()
        logger.info(f"Kalshi connected | Balance: ${balance / 100:.2f}")
    except Exception as e:
        logger.warning(f"Balance check failed (proceeding anyway): {e}")

    scheduler = build_scheduler(client, db, config)
    scheduler.start()

    # Run scan immediately on startup instead of waiting for first interval
    logger.info("Running initial scan on startup...")
    from .strategy.executor import TradeExecutor
    from .strategy.scanner import MarketScanner
    from .strategy.signals import SignalEvaluator

    from .data_feeds.bls import BLSFeed
    from .data_feeds.fred import FREDFeed
    bls_feed = BLSFeed(api_key=config.data_feeds.bls_api_key)
    fred_feed = FREDFeed(api_key=config.data_feeds.fred_api_key)
    scanner = MarketScanner(client)
    evaluator = SignalEvaluator(config.strategy, bls_feed)
    executor = TradeExecutor(client, db, config.strategy)

    try:
        bls_data = await bls_feed.fetch_latest()
        fred_data = await fred_feed.fetch_latest() if config.data_feeds.fred_api_key else {}
        markets = await scanner.scan_markets()
        held = await db.held_tickers()
        open_count = await db.open_trade_count()
        signals = evaluator.evaluate(markets, bls_data, held, open_count, fred_data)
        if signals:
            placed = await executor.execute_signals(signals)
            logger.info(f"Initial scan: {placed} order(s) placed")
        else:
            logger.info("Initial scan: no signals found")
    except Exception as e:
        logger.error(f"Initial scan error: {e}", exc_info=True)

    # Wait for shutdown signal
    stop = asyncio.Event()

    def handle_signal(*_):
        logger.info("Shutdown signal received")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_signal)

    await stop.wait()

    logger.info("Shutting down...")
    scheduler.shutdown(wait=True)
    await client.close()
    await db.close()
    logger.info("TradeBot stopped")


if __name__ == "__main__":
    asyncio.run(main())
