import asyncio
import logging
import time
from typing import AsyncIterator

import httpx

from .auth import load_private_key, sign_pss
from .config import KalshiConfig

logger = logging.getLogger(__name__)

_RETRY_STATUSES = {429, 502, 503, 504}
_MAX_RETRIES = 5


class KalshiClient:
    def __init__(self, config: KalshiConfig):
        self._api_key = config.api_key
        self._private_key = load_private_key(config.private_key_path)
        self._base_url = config.base_url.rstrip("/")
        # The path prefix used in signing (everything before the endpoint)
        self._path_prefix = "/trade-api/v2"
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=30.0,
            headers={"Content-Type": "application/json"},
        )

    def _auth_headers(self, method: str, endpoint: str) -> dict[str, str]:
        """Build the three Kalshi auth headers. endpoint is like /portfolio/balance."""
        full_path = f"{self._path_prefix}{endpoint}"
        ts = str(int(time.time() * 1000))
        sig = sign_pss(self._private_key, ts, method.upper(), full_path)
        return {
            "KALSHI-ACCESS-KEY": self._api_key,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
        auth: bool = True,
    ) -> dict:
        headers = self._auth_headers(method, endpoint) if auth else {}
        delay = 0.5
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._http.request(
                    method, endpoint, params=params, json=body, headers=headers
                )
                if resp.status_code in _RETRY_STATUSES:
                    wait = float(resp.headers.get("Retry-After", delay))
                    logger.warning(
                        f"HTTP {resp.status_code} on {method} {endpoint}, "
                        f"retrying in {wait:.1f}s (attempt {attempt + 1}/{_MAX_RETRIES})"
                    )
                    await asyncio.sleep(wait)
                    delay = min(delay * 2, 30)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error {e.response.status_code} on {method} {endpoint}: {e.response.text[:200]}")
                raise
            except httpx.RequestError as e:
                if attempt < _MAX_RETRIES - 1:
                    logger.warning(f"Request error on {method} {endpoint}: {e}, retrying...")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
                else:
                    raise
        raise RuntimeError(f"Max retries exceeded for {method} {endpoint}")

    # ── Public market endpoints (no auth required) ──────────────────────────

    async def get_markets(
        self,
        status: str = "open",
        limit: int = 200,
        cursor: str | None = None,
        series_ticker: str | None = None,
        min_close_ts: int | None = None,
        max_close_ts: int | None = None,
    ) -> dict:
        params: dict = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        if min_close_ts is not None:
            params["min_close_ts"] = min_close_ts
        if max_close_ts is not None:
            params["max_close_ts"] = max_close_ts
        return await self._request("GET", "/markets", params=params, auth=False)

    async def iter_series_markets(
        self,
        series_ticker: str,
        status: str = "open",
    ) -> AsyncIterator[dict]:
        """Yields all markets for a specific series ticker."""
        cursor: str | None = None
        while True:
            data = await self.get_markets(
                status=status,
                series_ticker=series_ticker,
                cursor=cursor,
            )
            for market in data.get("markets", []):
                yield market
            cursor = data.get("cursor")
            if not cursor:
                break
            await asyncio.sleep(0.1)

    async def iter_all_markets(
        self,
        status: str = "open",
        min_close_ts: int | None = None,
        max_close_ts: int | None = None,
    ) -> AsyncIterator[dict]:
        """Yields every market, handling cursor-based pagination."""
        cursor: str | None = None
        while True:
            data = await self.get_markets(
                status=status,
                cursor=cursor,
                min_close_ts=min_close_ts,
                max_close_ts=max_close_ts,
            )
            for market in data.get("markets", []):
                yield market
            cursor = data.get("cursor")
            if not cursor:
                break
            await asyncio.sleep(0.3)

    async def get_market(self, ticker: str) -> dict:
        data = await self._request("GET", f"/markets/{ticker}", auth=False)
        return data.get("market", data)

    # ── Authenticated portfolio endpoints ────────────────────────────────────

    async def get_balance(self) -> int:
        """Returns available balance in cents."""
        data = await self._request("GET", "/portfolio/balance")
        return data.get("balance", 0)

    async def get_positions(self) -> list[dict]:
        data = await self._request("GET", "/portfolio/positions")
        return data.get("market_positions", [])

    async def get_fills(self, limit: int = 100) -> list[dict]:
        data = await self._request("GET", "/portfolio/fills", params={"limit": limit})
        return data.get("fills", [])

    async def place_order(
        self,
        ticker: str,
        side: str,  # "yes" or "no"
        contracts: int,
        price_cents: int,
        client_order_id: str | None = None,
    ) -> dict:
        body: dict = {
            "ticker": ticker,
            "action": "buy",
            "type": "limit",
            "side": side,
            f"{side}_price": price_cents,
            "count": contracts,
        }
        if client_order_id:
            body["client_order_id"] = client_order_id
        data = await self._request("POST", "/portfolio/orders", body=body)
        return data.get("order", data)

    async def get_order(self, order_id: str) -> dict:
        data = await self._request("GET", f"/portfolio/orders/{order_id}")
        return data.get("order", data)

    async def cancel_order(self, order_id: str) -> dict:
        data = await self._request("DELETE", f"/portfolio/orders/{order_id}")
        return data.get("order", data)

    async def close(self):
        await self._http.aclose()
