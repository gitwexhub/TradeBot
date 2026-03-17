from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass
class Trade:
    id: int | None
    market_ticker: str
    market_title: str
    side: Literal["yes", "no"]
    yes_ask_cents: int        # price at signal time
    contracts: int
    cost_cents: int           # contracts * yes_ask_cents (or no_ask for NO trades)
    close_time: str           # ISO-8601 UTC
    resolution_window_hours: float
    order_id: str | None
    order_status: str         # pending | open | filled | cancelled | rejected
    resolved_yes: bool | None
    pnl_cents: int | None
    signal_at: str
    ordered_at: str | None
    settled_at: str | None
    synced_at: str | None


@dataclass
class PerformanceSnapshot:
    id: int | None
    snapshot_at: str
    period_start: str
    period_end: str
    trades_total: int
    trades_won: int
    trades_lost: int
    trades_pending: int
    gross_pnl_cents: int
    net_pnl_cents: int
    open_positions: int
    balance_cents: int | None
