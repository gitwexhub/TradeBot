import logging
from datetime import datetime, timedelta, timezone

from ..client import KalshiClient
from ..db.connection import Database

logger = logging.getLogger(__name__)


class PerformanceTracker:
    def __init__(self, client: KalshiClient, db: Database):
        self._client = client
        self._db = db

    async def snapshot(self, period_days: int = 3):
        now = datetime.now(timezone.utc)
        period_end = now.isoformat()
        period_start = (now - timedelta(days=period_days)).isoformat()

        settled = await self._db.get_settled_in_window(period_start, period_end)
        open_count = await self._db.open_trade_count()

        trades_won = sum(1 for t in settled if (t.get("pnl_cents") or 0) > 0)
        trades_lost = sum(1 for t in settled if (t.get("pnl_cents") or 0) <= 0)
        gross_pnl = sum(t.get("pnl_cents") or 0 for t in settled)

        balance_cents: int | None = None
        try:
            balance_cents = await self._client.get_balance()
        except Exception as e:
            logger.warning(f"Could not fetch balance for snapshot: {e}")

        await self._db.execute(
            """INSERT INTO performance_snapshots
               (period_start, period_end, trades_total, trades_won, trades_lost,
                trades_pending, gross_pnl_cents, net_pnl_cents, open_positions, balance_cents)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                period_start, period_end,
                len(settled), trades_won, trades_lost,
                open_count,  # pending/open not yet settled
                gross_pnl, gross_pnl,  # no fee data; net = gross for now
                open_count, balance_cents,
            ),
        )

        win_rate = trades_won / len(settled) if settled else 0.0
        balance_str = f"${balance_cents / 100:.2f}" if balance_cents is not None else "n/a"

        logger.info(
            f"[SNAPSHOT] {period_days}-day window | "
            f"Settled: {len(settled)} | "
            f"W/L: {trades_won}/{trades_lost} ({win_rate:.0%}) | "
            f"P&L: ${gross_pnl / 100:+.2f} | "
            f"Open: {open_count} | Balance: {balance_str}"
        )
