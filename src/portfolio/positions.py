import logging
from datetime import datetime, timezone

from ..client import KalshiClient
from ..db.connection import Database

logger = logging.getLogger(__name__)


class PositionSync:
    """
    Reconciles open trades in the DB against live Kalshi positions.
    Detects settlements and updates P&L.
    """

    def __init__(self, client: KalshiClient, db: Database):
        self._client = client
        self._db = db

    async def sync(self):
        open_trades = await self._db.get_open_trades()
        if not open_trades:
            logger.debug("No open trades to sync")
            return

        try:
            live_positions = await self._client.get_positions()
        except Exception as e:
            logger.error(f"Failed to fetch live positions: {e}")
            return

        # Build lookup: ticker → position dict
        live = {p["ticker"]: p for p in live_positions}
        now_iso = datetime.now(timezone.utc).isoformat()

        for trade in open_trades:
            ticker = trade["market_ticker"]
            position = live.get(ticker)

            if position is not None:
                # Still open — just update synced_at
                await self._db.set_synced_at(trade["id"], now_iso)
                continue

            # Not in live positions → may have settled or been cancelled
            await self._check_settlement(trade, now_iso)

    async def _check_settlement(self, trade: dict, now_iso: str):
        ticker = trade["market_ticker"]
        try:
            market = await self._client.get_market(ticker)
            status = market.get("status", "")
            result = market.get("result", "")  # "yes", "no", or "" / None

            if status not in ("settled", "finalized") or not result:
                # Market not yet resolved; position may just be empty (unfilled order)
                await self._db.set_synced_at(trade["id"], now_iso)
                return

            if result == "void":
                pnl_cents = 0
                resolved_yes = False  # doesn't matter for void
            else:
                won = result == trade["side"]
                resolved_yes = result == "yes"
                if won:
                    # Paid price_cents per contract, receive 100 per contract
                    pnl_cents = trade["contracts"] * (100 - trade["yes_ask_cents"])
                else:
                    pnl_cents = -trade["cost_cents"]

            await self._db.set_trade_settled(
                trade_id=trade["id"],
                resolved_yes=resolved_yes,
                pnl_cents=pnl_cents,
                settled_at=now_iso,
            )

            pnl_usd = pnl_cents / 100
            logger.info(
                f"SETTLED | {ticker} | side={trade['side'].upper()} | "
                f"result={result.upper()} | P&L: ${pnl_usd:+.2f}"
            )

        except Exception as e:
            logger.warning(f"Settlement check failed for {ticker}: {e}")
            await self._db.set_synced_at(trade["id"], now_iso)
