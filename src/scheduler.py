"""
Scheduler — coordinates BLS polling and market scanning.

Jobs:
  data_poll  — fetches latest BLS data on a regular interval.
               Detects when new data has been released (period changed).
               Triggers an immediate market scan when new data is found.

  scan       — compares all open Kalshi markets against latest BLS data.
               Generates and executes signals when price lags implied by
               >= lag_threshold_cents.

  sync       — reconciles open positions with Kalshi API, records P&L.

  snapshot   — periodic performance summary.

Release-window fast mode:
  BLS releases CPI and unemployment data at 8:30 AM ET on scheduled dates.
  Around those windows the data_poll job runs every release_poll_seconds
  instead of every poll_interval_minutes, so we catch new data as fast
  as possible and trade the lag before the market catches up.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .client import KalshiClient
from .config import Config
from .data_feeds.bls import BLSFeed, DataPoint
from .data_feeds.fred import FREDFeed, FredDataPoint
from .db.connection import Database
from .portfolio.performance import PerformanceTracker
from .portfolio.positions import PositionSync
from .strategy.executor import TradeExecutor
from .strategy.scanner import MarketScanner
from .strategy.signals import SignalEvaluator

logger = logging.getLogger(__name__)

_ET = pytz.timezone("America/New_York")

# BLS releases at 8:30 AM ET. Fast-poll window: 8:00–9:00 AM ET.
_BLS_WINDOW_START = time(8, 0)
_BLS_WINDOW_END = time(9, 0)

# FOMC announces at 2:00 PM ET. Fast-poll window: 1:55–3:00 PM ET.
_FOMC_WINDOW_START = time(13, 55)
_FOMC_WINDOW_END = time(15, 0)


def _in_bls_window() -> bool:
    now_et = datetime.now(_ET).time()
    return _BLS_WINDOW_START <= now_et <= _BLS_WINDOW_END


def _in_fomc_window() -> bool:
    now_et = datetime.now(_ET).time()
    return _FOMC_WINDOW_START <= now_et <= _FOMC_WINDOW_END


def _in_any_release_window() -> bool:
    return _in_bls_window() or _in_fomc_window()


def build_scheduler(client: KalshiClient, db: Database, config: Config) -> AsyncIOScheduler:
    bls_feed = BLSFeed(api_key=config.data_feeds.bls_api_key)
    fred_feed = FREDFeed(api_key=config.data_feeds.fred_api_key)
    scanner = MarketScanner(client)
    evaluator = SignalEvaluator(config.strategy, bls_feed)
    executor = TradeExecutor(client, db, config.strategy)
    sync = PositionSync(client, db)
    tracker = PerformanceTracker(client, db)

    scheduler = AsyncIOScheduler()
    sched_cfg = config.scheduler
    feeds_cfg = config.data_feeds

    # Shared state between jobs
    _state: dict = {
        "bls_data": {},         # latest fetched BLS data
        "fred_data": {},        # latest fetched FRED data
        "last_period": {},      # {series_id: (year, month)} — detect new releases
        "last_fred_date": {},   # {series_id: date_str} — detect new FRED releases
    }

    async def _fetch_all_data() -> bool:
        """
        Fetch latest BLS and FRED data. Returns True if any new release detected.
        """
        new_release = False

        # BLS
        try:
            bls_new = await bls_feed.fetch_latest()
            _state["bls_data"] = bls_new
            for sid, dp in bls_new.items():
                if "prior" in sid:
                    continue
                prev = _state["last_period"].get(sid)
                current = (dp.year, dp.month)
                if prev and prev != current:
                    logger.info(
                        f"NEW BLS RELEASE: {sid} "
                        f"{prev[0]}-{prev[1]:02d} → {current[0]}-{current[1]:02d} "
                        f"value={dp.value}"
                    )
                    new_release = True
                _state["last_period"][sid] = current
        except Exception as e:
            logger.error(f"BLS fetch failed: {e}")

        # FRED
        if feeds_cfg.fred_api_key:
            try:
                fred_new = await fred_feed.fetch_latest()
                _state["fred_data"] = fred_new
                for sid, dp in fred_new.items():
                    prev_date = _state["last_fred_date"].get(sid)
                    if prev_date and prev_date != dp.date:
                        logger.info(
                            f"NEW FRED RELEASE: {sid} "
                            f"{prev_date} → {dp.date} value={dp.value}%"
                        )
                        new_release = True
                    _state["last_fred_date"][sid] = dp.date
            except Exception as e:
                logger.error(f"FRED fetch failed: {e}")

        return new_release

    async def data_poll_job():
        """Fetch all data feeds and scan immediately if new release detected."""
        in_window = _in_any_release_window()

        new_release = await _fetch_all_data()

        if new_release or in_window:
            logger.info(
                "Triggering immediate market scan "
                f"({'new release' if new_release else 'release window'})"
            )
            await _run_scan()

        # Fast-poll during release windows
        if in_window:
            scheduler.reschedule_job(
                "data_poll",
                trigger=IntervalTrigger(seconds=feeds_cfg.release_poll_seconds),
            )
        else:
            scheduler.reschedule_job(
                "data_poll",
                trigger=IntervalTrigger(minutes=feeds_cfg.poll_interval_minutes),
            )

    async def _run_scan():
        """Core scan: compare markets against latest BLS data, execute signals."""
        bls_data: dict[str, DataPoint] = _state["bls_data"]
        if not bls_data:
            logger.info("Scan skipped: no BLS data yet")
            return

        try:
            markets = await scanner.scan_markets()
            held = await db.held_tickers()
            open_count = await db.open_trade_count()
            fred_data: dict[str, FredDataPoint] = _state["fred_data"]
            signals = evaluator.evaluate(markets, bls_data, held, open_count, fred_data)

            if signals:
                logger.info(f"Found {len(signals)} signal(s) to execute")
                placed = await executor.execute_signals(signals)
                logger.info(f"Scan complete: {placed}/{len(signals)} orders placed")
            else:
                logger.info("Scan complete: no signals")
        except Exception as e:
            logger.error(f"Scan error: {e}", exc_info=True)

    async def scan_job():
        logger.info("── Scheduled scan starting ──")
        await _run_scan()

    async def sync_job():
        logger.debug("── Position sync starting ──")
        try:
            await sync.sync()
        except Exception as e:
            logger.error(f"Sync job error: {e}", exc_info=True)

    async def snapshot_job():
        logger.info("── Performance snapshot starting ──")
        try:
            await tracker.snapshot(period_days=sched_cfg.snapshot_interval_days)
        except Exception as e:
            logger.error(f"Snapshot job error: {e}", exc_info=True)

    # Data poll: starts at normal interval, switches to fast during release window
    scheduler.add_job(
        data_poll_job,
        IntervalTrigger(minutes=feeds_cfg.poll_interval_minutes),
        id="data_poll",
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        scan_job,
        IntervalTrigger(minutes=sched_cfg.scan_interval_minutes),
        id="scan",
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        sync_job,
        IntervalTrigger(minutes=sched_cfg.sync_interval_minutes),
        id="sync",
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        snapshot_job,
        IntervalTrigger(days=sched_cfg.snapshot_interval_days),
        id="snapshot",
        max_instances=1,
        replace_existing=True,
    )

    logger.info(
        f"Scheduler configured: "
        f"data_poll every {feeds_cfg.poll_interval_minutes}m "
        f"(fast={feeds_cfg.release_poll_seconds}s during 8–9am ET) | "
        f"scan every {sched_cfg.scan_interval_minutes}m | "
        f"sync every {sched_cfg.sync_interval_minutes}m"
    )
    return scheduler
