import pytest

from src.config import StrategyConfig
from src.db.connection import Database


@pytest.fixture
def strategy_config():
    return StrategyConfig(
        resolution_window_hours=48.0,
        yes_buy_threshold_cents=70,
        no_buy_threshold_cents=30,
        trade_size_usd=10.0,
        max_open_positions=5,
    )


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()
