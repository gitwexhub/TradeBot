"""
Market title parser — extracts structured specs from Kalshi market titles.

Handles unemployment, CPI, NFP, and Fed rate markets phrased as:
  "Will the U.S. unemployment rate be X% or lower for January 2026?"
  "Will CPI increase exceed 3.0% year-over-year for February 2026?"
  "Will above 200000 jobs be added in February 2026?"
  "Will the upper bound of the federal funds rate be above 4.25% following the Fed's Mar 19, 2026 meeting?"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Patterns to detect data type from market title
_UNEMPLOYMENT_PATTERNS = [
    r"unemployment\s+rate",
    r"unemployment",
    r"jobless\s+rate",
]
_CPI_YOY_PATTERNS = [
    r"cpi.{0,30}year.over.year",
    r"cpi.{0,30}yoy",
    r"consumer\s+price.{0,30}year.over.year",
    r"inflation.{0,30}year.over.year",
    r"inflation\s+rate",
    r"\bcpi\b",
    r"consumer\s+price\s+index",
]
_NFP_PATTERNS = [
    r"nonfarm\s+payroll",
    r"non.farm\s+payroll",
    r"jobs\s+be\s+added",
    r"jobs\s+added",
    r"payrolls",
]
_FED_RATE_PATTERNS = [
    r"federal\s+funds\s+rate",
    r"fed\s+funds\s+rate",
    r"upper\s+bound.{0,30}federal",
    r"federal.{0,30}upper\s+bound",
    r"fomc.{0,30}rate",
]

# Threshold: matches "3.5%", "4%", "4.20%"
_THRESHOLD_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")

# NFP threshold: matches job counts like "200000", "90,000", "-25000"
_NFP_THRESHOLD_RE = re.compile(r"(-?\d[\d,]*)\s+jobs")

# Direction: which side of threshold makes YES win
_ABOVE_RE = re.compile(
    r"\b(above|exceed|exceeds|more\s+than|higher\s+than|over|greater\s+than)\b",
    re.IGNORECASE,
)
_BELOW_RE = re.compile(
    r"\b(below|under|at\s+or\s+below|lower\s+than|less\s+than|not\s+exceed|not\s+above|or\s+lower|or\s+less)\b",
    re.IGNORECASE,
)

# Month + year: "January 2026", "Jan 2026", "January '26"
_PERIOD_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+'?(\d{2,4})\b",
    re.IGNORECASE,
)


@dataclass
class MarketSpec:
    """Structured representation of what a Kalshi market is measuring."""
    ticker: str
    title: str
    data_type: str          # "unemployment" | "cpi_yoy" | "nfp" | "fed_rate"
    period_year: int
    period_month: int
    threshold: float        # percent for unemployment/CPI/fed; thousands of jobs for NFP
    yes_wins_if_above: bool # True  → YES wins if actual > threshold
                            # False → YES wins if actual <= threshold


def parse_market(market: dict) -> MarketSpec | None:
    """
    Try to parse a Kalshi market dict into a structured MarketSpec.
    Returns None if the market doesn't match any supported data type.
    """
    ticker = market.get("ticker", "")
    title = market.get("title", "") or market.get("market_title", "")
    if not title:
        return None

    title_lower = title.lower()

    # Detect data type (order matters — check NFP before unemployment to avoid overlap)
    data_type: str | None = None
    for pat in _NFP_PATTERNS:
        if re.search(pat, title_lower):
            data_type = "nfp"
            break
    if data_type is None:
        for pat in _UNEMPLOYMENT_PATTERNS:
            if re.search(pat, title_lower):
                data_type = "unemployment"
                break
    if data_type is None:
        for pat in _CPI_YOY_PATTERNS:
            if re.search(pat, title_lower):
                data_type = "cpi_yoy"
                break
    if data_type is None:
        for pat in _FED_RATE_PATTERNS:
            if re.search(pat, title_lower):
                data_type = "fed_rate"
                break

    if data_type is None:
        return None

    # Extract period (month + year) — present in all types
    period_match = _PERIOD_RE.search(title_lower)
    if not period_match:
        return None
    month_str = period_match.group(1)
    year_str = period_match.group(2)
    month = _MONTHS.get(month_str)
    if not month:
        return None
    year = int(year_str)
    if year < 100:
        year += 2000

    # Extract threshold (type-specific)
    if data_type == "nfp":
        nfp_match = _NFP_THRESHOLD_RE.search(title_lower)
        if not nfp_match:
            return None
        # e.g. "90,000 jobs" → 90000.0 (in thousands for BLS: 90.0)
        raw = nfp_match.group(1).replace(",", "")
        threshold = float(raw) / 1000  # convert to thousands to match BLS data
    else:
        threshold_match = _THRESHOLD_RE.search(title)
        if not threshold_match:
            return None
        threshold = float(threshold_match.group(1))

    # Determine direction
    above_match = _ABOVE_RE.search(title)
    below_match = _BELOW_RE.search(title)

    if above_match and not below_match:
        yes_wins_if_above = True
    elif below_match and not above_match:
        yes_wins_if_above = False
    else:
        logger.debug(f"Cannot determine direction for: {title}")
        return None

    return MarketSpec(
        ticker=ticker,
        title=title,
        data_type=data_type,
        period_year=year,
        period_month=month,
        threshold=threshold,
        yes_wins_if_above=yes_wins_if_above,
    )
