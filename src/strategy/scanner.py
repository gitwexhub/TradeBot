"""
Market scanner — fetches only BLS-related Kalshi markets.

Fetches by series ticker so we avoid paginating through all 600k+ markets.
Known BLS series on Kalshi:
  KXCPIYOY      — CPI year-over-year
  KXCPICOREYOY  — Core CPI year-over-year
  KXUNEMPLOYMENT — Unemployment rate
"""

from __future__ import annotations

import logging

from ..client import KalshiClient

logger = logging.getLogger(__name__)

# Kalshi series tickers for BLS + FRED economic data
BLS_SERIES = [
    "KXCPIYOY",
    "KXCPICOREYOY",
    "KXUNEMPLOYMENT",
    "KXPAYROLLS",   # Nonfarm payrolls
    "KXFED",        # Fed funds target rate
]


class MarketScanner:
    def __init__(self, client: KalshiClient):
        self._client = client

    async def scan_markets(self) -> list[dict]:
        """Fetch all open BLS-related Kalshi markets."""
        markets: list[dict] = []
        for series in BLS_SERIES:
            count = 0
            async for market in self._client.iter_series_markets(series, status="open"):
                markets.append(market)
                count += 1
            logger.debug(f"Series {series}: {count} open markets")

        logger.info(f"Fetched {len(markets)} open BLS markets across {len(BLS_SERIES)} series")
        return markets
