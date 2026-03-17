"""
FRED (Federal Reserve Economic Data) feed.

Fetches the Fed funds target rate upper bound after FOMC meetings.
Free API key required: https://fred.stlouisfed.org/docs/api/api_key.html

Series used:
  DFEDTARU — Fed funds target rate upper bound (updated after each FOMC meeting)
  DFEDTARL — Fed funds target rate lower bound
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_FRED_API = "https://api.stlouisfed.org/fred/series/observations"

SERIES_FED_UPPER = "DFEDTARU"
SERIES_FED_LOWER = "DFEDTARL"


@dataclass
class FredDataPoint:
    series_id: str
    date: str        # e.g. "2026-03-20"
    value: float     # percent, e.g. 4.5
    fetched_at: datetime


class FREDFeed:
    """Fetches the current Fed funds target rate from FRED."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def fetch_latest(self) -> dict[str, FredDataPoint]:
        """
        Returns latest upper and lower bound of Fed funds target rate.
        Keys: SERIES_FED_UPPER, SERIES_FED_LOWER
        """
        if not self._api_key:
            raise RuntimeError("FRED_API_KEY not configured")

        fetched_at = datetime.now(timezone.utc)
        result: dict[str, FredDataPoint] = {}

        async with httpx.AsyncClient(timeout=15.0) as client:
            for series_id in [SERIES_FED_UPPER, SERIES_FED_LOWER]:
                params = {
                    "series_id": series_id,
                    "api_key": self._api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 1,
                }
                try:
                    resp = await client.get(_FRED_API, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    observations = data.get("observations", [])
                    if not observations:
                        logger.warning(f"No FRED data for {series_id}")
                        continue
                    obs = observations[0]
                    value_str = obs.get("value", ".")
                    if value_str == ".":
                        logger.warning(f"FRED {series_id} has no value")
                        continue
                    result[series_id] = FredDataPoint(
                        series_id=series_id,
                        date=obs["date"],
                        value=float(value_str),
                        fetched_at=fetched_at,
                    )
                except Exception as e:
                    logger.error(f"FRED fetch failed for {series_id}: {e}")

        if result:
            upper = result.get(SERIES_FED_UPPER)
            lower = result.get(SERIES_FED_LOWER)
            if upper and lower:
                logger.info(
                    f"FRED fetch complete: Fed funds target {lower.value}–{upper.value}% "
                    f"(as of {upper.date})"
                )

        return result
