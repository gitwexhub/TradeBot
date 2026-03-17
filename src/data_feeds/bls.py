"""
BLS (Bureau of Labor Statistics) data feed.

Fetches CPI, unemployment, and nonfarm payrolls from the free BLS public API.
No API key required, but registering for a free key raises the rate limit
from 25 to 500 requests/day: https://data.bls.gov/registrationEngine/

Series used:
  LNS14000000  — Unemployment rate (seasonally adjusted), percent
  CUUR0000SA0  — CPI-U, not seasonally adjusted, index value
  CES0000000001 — Total nonfarm payrolls (seasonally adjusted, thousands)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

SERIES_UNEMPLOYMENT = "LNS14000000"
SERIES_CPI = "CUUR0000SA0"
SERIES_NFP = "CES0000000001"   # Total nonfarm payrolls, thousands

# All series we fetch in a single request
_ALL_SERIES = [SERIES_UNEMPLOYMENT, SERIES_CPI, SERIES_NFP]


@dataclass
class DataPoint:
    series_id: str
    year: int
    month: int       # 1–12
    value: float
    fetched_at: datetime


class BLSFeed:
    """Fetches latest BLS data points for CPI and unemployment."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key

    async def fetch_latest(self) -> dict[str, DataPoint]:
        """
        Returns a dict keyed by series_id with the most recent data point.
        Also includes prior-year CPI under key f"{SERIES_CPI}_prior_year"
        so callers can compute YoY percent change.
        """
        now = datetime.now(timezone.utc)
        current_year = now.year
        # Fetch 2 years so we can compute CPI year-over-year
        start_year = str(current_year - 1)
        end_year = str(current_year)

        body: dict = {
            "seriesid": _ALL_SERIES,
            "startyear": start_year,
            "endyear": end_year,
            "latest": False,
        }
        if self._api_key:
            body["registrationkey"] = self._api_key

        fetched_at = datetime.now(timezone.utc)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(_BLS_API, json=body)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.error(f"BLS API request failed: {e}")
            raise

        if data.get("status") != "REQUEST_SUCCEEDED":
            msg = data.get("message", ["Unknown BLS error"])
            raise RuntimeError(f"BLS API error: {msg}")

        result: dict[str, DataPoint] = {}

        for series in data.get("Results", {}).get("series", []):
            sid = series["seriesID"]
            observations = series.get("data", [])
            if not observations:
                logger.warning(f"No data returned for BLS series {sid}")
                continue

            # BLS returns newest first
            # Find most recent monthly observation (exclude annual M13)
            monthly = [
                o for o in observations
                if o.get("period", "").startswith("M") and o["period"] != "M13"
            ]
            if not monthly:
                continue

            latest = monthly[0]
            latest_year = int(latest["year"])
            latest_month = int(latest["period"][1:])  # "M03" → 3

            try:
                latest_value = float(latest["value"])
            except (ValueError, KeyError):
                logger.warning(f"Could not parse value for {sid}: {latest}")
                continue

            result[sid] = DataPoint(
                series_id=sid,
                year=latest_year,
                month=latest_month,
                value=latest_value,
                fetched_at=fetched_at,
            )

            # For NFP, also store prior month to compute MoM change
            if sid == SERIES_NFP and len(monthly) >= 2:
                prior_month_obs = monthly[1]
                try:
                    result[f"{SERIES_NFP}_prior_month"] = DataPoint(
                        series_id=sid,
                        year=int(prior_month_obs["year"]),
                        month=int(prior_month_obs["period"][1:]),
                        value=float(prior_month_obs["value"]),
                        fetched_at=fetched_at,
                    )
                except (ValueError, KeyError):
                    pass

            # For CPI, also store the same month from prior year for YoY calc
            if sid == SERIES_CPI:
                prior_year_str = str(latest_year - 1)
                period_str = latest["period"]
                prior = next(
                    (o for o in monthly if o["year"] == prior_year_str and o["period"] == period_str),
                    None,
                )
                if prior:
                    try:
                        result[f"{SERIES_CPI}_prior_year"] = DataPoint(
                            series_id=sid,
                            year=int(prior["year"]),
                            month=latest_month,
                            value=float(prior["value"]),
                            fetched_at=fetched_at,
                        )
                    except (ValueError, KeyError):
                        pass

        logger.info(
            f"BLS fetch complete: "
            + ", ".join(
                f"{sid.split('_')[0]}={dp.value} ({dp.year}-{dp.month:02d})"
                for sid, dp in result.items()
                if "prior" not in sid
            )
        )
        return result

    def compute_cpi_yoy(self, data: dict[str, DataPoint]) -> float | None:
        """Compute CPI year-over-year percent change from fetched data."""
        current = data.get(SERIES_CPI)
        prior = data.get(f"{SERIES_CPI}_prior_year")
        if not current or not prior or prior.value == 0:
            return None
        return round((current.value - prior.value) / prior.value * 100, 2)

    def compute_nfp_mom(self, data: dict[str, DataPoint]) -> float | None:
        """Compute NFP month-over-month change in thousands of jobs."""
        current = data.get(SERIES_NFP)
        prior = data.get(f"{SERIES_NFP}_prior_month")
        if not current or not prior:
            return None
        return round(current.value - prior.value, 1)
