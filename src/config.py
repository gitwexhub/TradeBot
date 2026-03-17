import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class KalshiConfig(BaseModel):
    base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    api_key: str = ""
    private_key_path: Path = Path("./keys/kalshi_private.pem")


class StrategyConfig(BaseModel):
    lag_threshold_cents: int = 15       # Min gap between implied and market price to trade
    trade_size_usd: float = 10.00
    max_open_positions: int = 5


class DataFeedsConfig(BaseModel):
    bls_api_key: str = ""               # Free key: data.bls.gov/registrationEngine/
    fred_api_key: str = ""              # Free key: fred.stlouisfed.org/docs/api/api_key.html
    poll_interval_minutes: int = 5      # Normal polling frequency
    release_poll_seconds: int = 30      # Fast polling during release windows


class SchedulerConfig(BaseModel):
    scan_interval_minutes: int = 5      # How often to scan markets against latest data
    sync_interval_minutes: int = 60
    snapshot_interval_days: int = 3


class DatabaseConfig(BaseModel):
    path: Path = Path("./data/tradebot.db")


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: Path = Path("./logs/tradebot.log")
    max_bytes: int = 10_485_760
    backup_count: int = 3


class Config(BaseModel):
    kalshi: KalshiConfig = KalshiConfig()
    strategy: StrategyConfig = StrategyConfig()
    data_feeds: DataFeedsConfig = DataFeedsConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    database: DatabaseConfig = DatabaseConfig()
    logging: LoggingConfig = LoggingConfig()

    @classmethod
    def load(cls, config_path: Path = Path("config.yaml")) -> "Config":
        raw: dict = {}
        if config_path.exists():
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}

        config = cls.model_validate(raw)

        # Overlay secrets from env
        api_key = os.environ.get("KALSHI_API_KEY", "")
        key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
        bls_key = os.environ.get("BLS_API_KEY", "")
        fred_key = os.environ.get("FRED_API_KEY", "")
        if api_key:
            config.kalshi.api_key = api_key
        if key_path:
            config.kalshi.private_key_path = Path(key_path)
        if bls_key:
            config.data_feeds.bls_api_key = bls_key
        if fred_key:
            config.data_feeds.fred_api_key = fred_key

        return config
