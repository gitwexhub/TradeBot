import logging
import uuid
from datetime import datetime, timezone

from ..client import KalshiClient
from ..config import StrategyConfig
from ..db.connection import Database
from .signals import TradeSignal

logger = logging.getLogger(__name__)


class TradeExecutor:
    def __init__(self, client: KalshiClient, db: Database, config: StrategyConfig):
        self._client = client
        self._db = db
        self._max_positions = config.max_open_positions

    async def execute_signals(self, signals: list[TradeSignal]) -> int:
        """
        Execute trade signals in confidence order.
        Returns number of orders successfully placed.
        """
        placed = 0
        for signal in signals:
            # Re-check live position count before each order
            open_count = await self._db.open_trade_count()
            if open_count >= self._max_positions:
                logger.info("Max positions reached, stopping signal execution")
                break

            held = await self._db.held_tickers()
            if signal.market_ticker in held:
                logger.debug(f"Already holding {signal.market_ticker}, skipping")
                continue

            success = await self._place_order(signal)
            if success:
                placed += 1

        return placed

    async def _place_order(self, signal: TradeSignal) -> bool:
        # Insert as 'pending' first so the UNIQUE constraint catches any race
        try:
            trade_id = await self._db.insert_trade(
                market_ticker=signal.market_ticker,
                market_title=signal.market_title,
                side=signal.side,
                yes_ask_cents=signal.yes_ask_cents,
                contracts=signal.contracts,
                cost_cents=signal.cost_cents,
                close_time=signal.close_time,
                resolution_window_hours=signal.hours_to_resolution,
            )
        except Exception as e:
            # UNIQUE constraint violation or other DB error — already holding
            logger.debug(f"Skipping {signal.market_ticker}: {e}")
            return False

        client_order_id = str(uuid.uuid4())
        try:
            order = await self._client.place_order(
                ticker=signal.market_ticker,
                side=signal.side,
                contracts=signal.contracts,
                price_cents=signal.price_cents,
                client_order_id=client_order_id,
            )
            order_id = order.get("order_id") or order.get("id", client_order_id)
            ordered_at = datetime.now(timezone.utc).isoformat()
            await self._db.set_order_placed(trade_id, order_id, ordered_at)

            logger.info(
                f"ORDER PLACED | {signal.side.upper()} {signal.contracts}x "
                f"{signal.market_ticker} @ {signal.price_cents}¢ "
                f"(${signal.cost_cents / 100:.2f}) | "
                f"{signal.hours_to_resolution:.1f}h to close | "
                f"confidence={signal.confidence:.3f}"
            )
            return True

        except Exception as e:
            logger.error(f"Order failed for {signal.market_ticker}: {e}")
            await self._db.set_order_rejected(trade_id)
            return False
