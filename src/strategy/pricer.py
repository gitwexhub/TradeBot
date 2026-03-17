"""
Implied price calculator.

Given a MarketSpec and the latest data, computes what the YES contract
*should* be trading at based on public data (BLS or FRED).

Returns:
  95  — data clearly shows YES wins  (leave 5¢ margin for data revisions)
   5  — data clearly shows YES loses
None  — data period doesn't match, or value is too close to threshold to call
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..data_feeds.bls import DataPoint, SERIES_UNEMPLOYMENT, SERIES_CPI, SERIES_NFP, BLSFeed
from ..data_feeds.fred import FredDataPoint, SERIES_FED_UPPER, FREDFeed
from .matcher import MarketSpec

logger = logging.getLogger(__name__)

# Margin before we refuse to call it (too close to threshold)
_CALL_MARGIN_PCT = 0.05       # percent — for unemployment, CPI, fed rate
_CALL_MARGIN_NFP = 25.0       # thousands of jobs — for NFP

# Implied price when data is clear
_IMPLIED_WIN = 95
_IMPLIED_LOSE = 5


def compute_implied_cents(
    spec: MarketSpec,
    bls_data: dict[str, DataPoint],
    bls_feed: BLSFeed,
    fred_data: dict[str, FredDataPoint] | None = None,
) -> int | None:
    """
    Returns implied YES price in cents, or None if we can't determine.
    """
    if spec.data_type == "unemployment":
        dp = bls_data.get(SERIES_UNEMPLOYMENT)
        if dp is None:
            return None
        actual_value = dp.value
        data_year, data_month = dp.year, dp.month
        margin = _CALL_MARGIN_PCT

    elif spec.data_type == "cpi_yoy":
        yoy = bls_feed.compute_cpi_yoy(bls_data)
        if yoy is None:
            return None
        dp = bls_data.get(SERIES_CPI)
        if dp is None:
            return None
        actual_value = yoy
        data_year, data_month = dp.year, dp.month
        margin = _CALL_MARGIN_PCT

    elif spec.data_type == "nfp":
        mom = bls_feed.compute_nfp_mom(bls_data)
        if mom is None:
            return None
        dp = bls_data.get(SERIES_NFP)
        if dp is None:
            return None
        actual_value = mom   # thousands of jobs MoM
        data_year, data_month = dp.year, dp.month
        margin = _CALL_MARGIN_NFP

    elif spec.data_type == "fed_rate":
        if not fred_data:
            return None
        dp_fred = fred_data.get(SERIES_FED_UPPER)
        if dp_fred is None:
            return None
        actual_value = dp_fred.value
        # Fed rate: parse year/month from the FRED date string "2026-03-20"
        parts = dp_fred.date.split("-")
        data_year, data_month = int(parts[0]), int(parts[1])
        margin = _CALL_MARGIN_PCT

    else:
        return None

    # Period must match (market asks about a specific reference month)
    if data_year != spec.period_year or data_month != spec.period_month:
        logger.debug(
            f"{spec.ticker}: data {data_year}-{data_month:02d} "
            f"≠ market period {spec.period_year}-{spec.period_month:02d}"
        )
        return None

    # Too close to threshold — skip
    distance = abs(actual_value - spec.threshold)
    if distance < margin:
        logger.debug(
            f"{spec.ticker}: actual={actual_value} too close to threshold={spec.threshold} "
            f"(distance={distance:.3f} < margin={margin})"
        )
        return None

    yes_wins = (actual_value > spec.threshold) == spec.yes_wins_if_above
    implied = _IMPLIED_WIN if yes_wins else _IMPLIED_LOSE

    logger.debug(
        f"{spec.ticker}: actual={actual_value} threshold={spec.threshold} "
        f"yes_wins_if_above={spec.yes_wins_if_above} → implied={implied}¢"
    )
    return implied
