import logging
from pathlib import Path

import aiosqlite

from .migrations import run_migrations

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, path: Path):
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._path))
        self._conn.row_factory = aiosqlite.Row
        await run_migrations(self._conn)
        logger.info(f"Database ready at {self._path}")

    async def close(self):
        if self._conn:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("Database.connect() not called")
        return self._conn

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # ── Trade helpers ─────────────────────────────────────────────────────

    async def insert_trade(
        self,
        *,
        market_ticker: str,
        market_title: str,
        side: str,
        yes_ask_cents: int,
        contracts: int,
        cost_cents: int,
        close_time: str,
        resolution_window_hours: float,
    ) -> int:
        cur = await self.execute(
            """INSERT INTO trades
               (market_ticker, market_title, side, yes_ask_cents, contracts,
                cost_cents, close_time, resolution_window_hours, order_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                market_ticker, market_title, side, yes_ask_cents, contracts,
                cost_cents, close_time, resolution_window_hours,
            ),
        )
        return cur.lastrowid  # type: ignore[return-value]

    async def set_order_placed(self, trade_id: int, order_id: str, ordered_at: str):
        await self.execute(
            "UPDATE trades SET order_id=?, order_status='open', ordered_at=? WHERE id=?",
            (order_id, ordered_at, trade_id),
        )

    async def set_order_rejected(self, trade_id: int):
        await self.execute(
            "UPDATE trades SET order_status='rejected' WHERE id=?",
            (trade_id,),
        )

    async def set_trade_settled(
        self,
        trade_id: int,
        resolved_yes: bool,
        pnl_cents: int,
        settled_at: str,
    ):
        await self.execute(
            """UPDATE trades
               SET order_status='filled', resolved_yes=?, pnl_cents=?,
                   settled_at=?, synced_at=?
               WHERE id=?""",
            (int(resolved_yes), pnl_cents, settled_at, settled_at, trade_id),
        )

    async def set_synced_at(self, trade_id: int, synced_at: str):
        await self.execute(
            "UPDATE trades SET synced_at=? WHERE id=?",
            (synced_at, trade_id),
        )

    async def get_open_trades(self) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM trades WHERE order_status IN ('pending','open')"
        )

    async def open_trade_count(self) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) as n FROM trades WHERE order_status IN ('pending','open')"
        )
        return row["n"] if row else 0

    async def held_tickers(self) -> set[str]:
        rows = await self.fetchall(
            "SELECT market_ticker FROM trades WHERE order_status IN ('pending','open')"
        )
        return {r["market_ticker"] for r in rows}

    async def get_settled_in_window(self, period_start: str, period_end: str) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM trades WHERE settled_at >= ? AND settled_at < ?",
            (period_start, period_end),
        )
