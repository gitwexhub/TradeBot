"""
Signal evaluator — data-lag strategy.

Compares Kalshi market prices against BLS-implied probabilities.
Generates a trade signal when the market price lags the implied price
by more than lag_threshold_cents.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from ..config import StrategyConfig
from ..data_feeds.bls import BLSFeed, DataPoint
from ..data_feeds.fred import FredDataPoint, FREDFeed
from .matcher import parse_market
from .pricer import compute_implied_cents

logger = logging.getLogger(__name__)

# Minimum time before market close — don't trade if order likely won't fill
_MIN_MINUTES_TO_CLOSE = 5


@dataclass
class TradeSignal:
    market_ticker: str
    market_title: str
    side: Literal["yes", "no"]
    price_cents: int        # price we pay (yes_ask for YES, no_ask for NO)
    yes_ask_cents: int      # raw yes_ask for record-keeping
    contracts: int
    cost_cents: int
    close_time: str
    hours_to_resolution: float
    confidence: float       # 0.0–1.0
    implied_cents: int      # what we think the market should be at
    lag_cents: int          # how far market price lags implied


class SignalEvaluator:
    """
    Evaluates Kalshi markets against BLS data to find price lags.

    For each market:
    1. Parse the title to extract what data it's measuring (matcher)
    2. Compute what the price *should* be given BLS data (pricer)
    3. Compare to actual price — if gap >= lag_threshold, generate signal
    """

    def __init__(self, config: StrategyConfig, bls_feed: BLSFeed):
        self.cfg = config
        self._bls_feed = bls_feed

    def evaluate(
        self,
        markets: list[dict],
        bls_data: dict[str, DataPoint],
        held_tickers: set[str],
        current_open_count: int,
        fred_data: dict[str, FredDataPoint] | None = None,
    ) -> list[TradeSignal]:
        """
        Evaluate markets and return ranked signals.
        Respects position limits and skips already-held tickers.
        """
        slots = self.cfg.max_open_positions - current_open_count
        if slots <= 0:
            return []

        signals: list[TradeSignal] = []
        for market in markets:
            if market.get("ticker") in held_tickers:
                continue
            sig = self._evaluate_one(market, bls_data, fred_data)
            if sig:
                signals.append(sig)

        # Rank by confidence (largest lag wins)
        signals.sort(key=lambda s: s.confidence, reverse=True)
        return signals[:slots]

    def _evaluate_one(
        self, market: dict, bls_data: dict[str, DataPoint],
        fred_data: dict[str, FredDataPoint] | None = None,
    ) -> TradeSignal | None:
        try:
            ticker = market.get("ticker", "")
            if not ticker or market.get("status") != "open":
                return None

            # Parse market title into structured spec
            spec = parse_market(market)
            if spec is None:
                return None

            # Compute implied price from BLS/FRED data
            implied = compute_implied_cents(spec, bls_data, self._bls_feed, fred_data)
            if implied is None:
                return None

            yes_ask = market.get("yes_ask")
            no_ask = market.get("no_ask")
            if not yes_ask or not no_ask:
                return None
            if yes_ask <= 0 or yes_ask >= 100 or no_ask <= 0 or no_ask >= 100:
                return None

            # How far is the market lagging?
            lag = implied - yes_ask  # positive = market underpricing YES
            if abs(lag) < self.cfg.lag_threshold_cents:
                return None

            # Determine side
            side: Literal["yes", "no"] = "yes" if lag > 0 else "no"
            price_cents = yes_ask if side == "yes" else no_ask

            # Check time to close
            close_time_str = market.get("close_time") or market.get("expiration_time")
            if not close_time_str:
                return None
            close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if close_time <= now:
                return None
            minutes_to_close = (close_time - now).total_seconds() / 60
            if minutes_to_close < _MIN_MINUTES_TO_CLOSE:
                return None
            hours_to_resolution = minutes_to_close / 60

            contracts = int((self.cfg.trade_size_usd * 100) / price_cents)
            if contracts < 1:
                return None

            cost_cents = contracts * price_cents
            # Confidence = normalized lag size (more lag = more confident)
            confidence = round(min(abs(lag) / 90, 1.0), 4)

            logger.info(
                f"SIGNAL | {side.upper()} {ticker} | "
                f"yes_ask={yes_ask}¢ implied={implied}¢ lag={lag:+d}¢ | "
                f"'{spec.data_type}' {spec.period_year}-{spec.period_month:02d} "
                f"actual vs threshold {spec.threshold}%"
            )

            return TradeSignal(
                market_ticker=ticker,
                market_title=market.get("title", ""),
                side=side,
                price_cents=price_cents,
                yes_ask_cents=yes_ask,
                contracts=contracts,
                cost_cents=cost_cents,
                close_time=close_time_str,
                hours_to_resolution=round(hours_to_resolution, 2),
                confidence=confidence,
                implied_cents=implied,
                lag_cents=lag,
            )

        except Exception as e:
            logger.warning(f"Signal eval error for {market.get('ticker')}: {e}")
            return None
