import aiosqlite

CURRENT_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    market_ticker            TEXT    NOT NULL UNIQUE,
    market_title             TEXT    NOT NULL DEFAULT '',
    side                     TEXT    NOT NULL CHECK(side IN ('yes','no')),
    yes_ask_cents            INTEGER NOT NULL,
    contracts                INTEGER NOT NULL,
    cost_cents               INTEGER NOT NULL,
    close_time               TEXT    NOT NULL,
    resolution_window_hours  REAL    NOT NULL,
    order_id                 TEXT,
    order_status             TEXT    NOT NULL DEFAULT 'pending'
                                     CHECK(order_status IN ('pending','open','filled','cancelled','rejected')),
    resolved_yes             INTEGER,
    pnl_cents                INTEGER,
    signal_at                TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    ordered_at               TEXT,
    settled_at               TEXT,
    synced_at                TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_order_status ON trades(order_status);
CREATE INDEX IF NOT EXISTS idx_trades_close_time   ON trades(close_time);

CREATE TABLE IF NOT EXISTS performance_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    period_start   TEXT    NOT NULL,
    period_end     TEXT    NOT NULL,
    trades_total   INTEGER NOT NULL DEFAULT 0,
    trades_won     INTEGER NOT NULL DEFAULT 0,
    trades_lost    INTEGER NOT NULL DEFAULT 0,
    trades_pending INTEGER NOT NULL DEFAULT 0,
    gross_pnl_cents INTEGER NOT NULL DEFAULT 0,
    net_pnl_cents  INTEGER NOT NULL DEFAULT 0,
    open_positions INTEGER NOT NULL DEFAULT 0,
    balance_cents  INTEGER
);
"""


async def run_migrations(db: aiosqlite.Connection):
    await db.executescript(_SCHEMA)
    async with db.execute("SELECT MAX(version) FROM schema_version") as cur:
        row = await cur.fetchone()
        version = row[0] if row and row[0] else 0

    if version < CURRENT_VERSION:
        await db.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (CURRENT_VERSION,),
        )
    await db.commit()
