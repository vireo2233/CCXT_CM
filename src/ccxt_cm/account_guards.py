"""Testnet identity and read-evidence guards using official implicit endpoints."""

import asyncio
import math
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from ccxt import AuthenticationError, NotSupported, PermissionDenied


def _require_bybit_testnet_spot(exchange) -> None:
    """Check existing REST/Pro routing locally, before any network request."""
    if exchange.id != "bybit" or exchange.isSandboxModeEnabled is not True:
        raise NotSupported("Bybit Testnet sandbox routing is required")
    if exchange.options.get("defaultType") != "spot":
        raise NotSupported("The account guard is restricted to spot")
    endpoints = exchange.urls.get("api", {})
    if not isinstance(endpoints, dict) or not {"public", "private"} <= endpoints.keys():
        raise NotSupported("Official Testnet REST endpoints are required")

    def check_endpoint(endpoint, websocket=False):
        if isinstance(endpoint, dict):
            for value in endpoint.values():
                check_endpoint(value, websocket)
            return
        if not isinstance(endpoint, str):
            raise NotSupported("Unexpected endpoint declaration")
        parsed = urlsplit(endpoint.replace("{hostname}", exchange.hostname))
        expected = (
            ("wss", "stream-testnet.bybit.com") if websocket else ("https", "api-testnet.bybit.com")
        )
        if (parsed.scheme, parsed.netloc) != expected:
            raise NotSupported("Only official Bybit Testnet endpoints are accepted")

    for name, endpoint in endpoints.items():
        check_endpoint(endpoint, websocket=name == "ws")


async def fetch_bybit_testnet_spot_public_window(exchange, symbol, *, start_ms, end_ms):
    """Keep a raw latest-60 suffix before filtering a finite [start, end) window.

    Coverage uses the endpoint's normal latest-suffix snapshot semantics. It is
    not a server completion watermark or a guarantee of zero publication delay.
    A strict predecessor is required even when fewer than 60 rows are returned.
    """
    _require_bybit_testnet_spot(exchange)

    def require(condition, code):
        if not condition:
            raise ValueError("bybit_public_window:" + code)

    require(type(start_ms) is int and type(end_ms) is int and 0 <= start_ms < end_ms,
            "invalid_window")
    market = exchange.market(symbol)
    require(isinstance(market, dict) and market.get("spot") is True
            and isinstance(market.get("id"), str) and bool(market["id"])
            and isinstance(market.get("symbol"), str) and bool(market["symbol"]), "spot_market")
    timeout_ms = exchange.timeout
    require(type(timeout_ms) in (int, float) and math.isfinite(timeout_ms) and timeout_ms > 0,
            "invalid_timeout")
    read_start = exchange.milliseconds()
    require(type(read_start) is int and read_start >= 0 and end_ms - 1 <= read_start,
            "cutoff_not_reached")
    params = {"category": "spot", "symbol": market["id"], "limit": 60}
    async with asyncio.timeout(timeout_ms / 1000):
        raw = await exchange.public_get_v5_market_recent_trade(dict(params))
    read_end = exchange.milliseconds()
    require(type(read_end) is int and read_start <= read_end
            and read_end - read_start <= timeout_ms, "read_clock_order")
    result = raw.get("result") if isinstance(raw, dict) else None
    require(isinstance(raw, dict) and type(raw.get("retCode")) is int and raw["retCode"] == 0
            and isinstance(result, dict) and result.get("category") == "spot"
            and isinstance(result.get("list"), list) and len(result["list"]) <= 60,
            "response_shape")
    source = raw.get("time")
    require(type(source) is int and read_start <= source <= read_end, "source_clock_order")
    seen, times = set(), []
    for row in result["list"]:
        require(isinstance(row, dict) and isinstance(row.get("execId"), str)
                and bool(row["execId"]) and row["execId"].strip() == row["execId"]
                and row["execId"] not in seen and row.get("symbol") == market["id"]
                and row.get("side") in ("Buy", "Sell"), "trade_identity")
        seen.add(row["execId"])
        for field in ("price", "size"):
            value = row.get(field)
            require(isinstance(value, str) and bool(value) and value.strip() == value,
                    "trade_number")
            try:
                number = Decimal(value)
            except InvalidOperation:
                raise ValueError("bybit_public_window:trade_number") from None
            require(number.is_finite() and number > 0, "trade_number")
        value = row.get("time")
        require(type(value) is int or (isinstance(value, str) and value.isascii()
                and value.isdecimal()), "trade_time")
        at = int(value)
        require(0 < at <= source, "trade_time")
        times.append(at)
    oldest = min(times, default=None)
    complete = bool(end_ms - start_ms <= 60000 and end_ms <= source
                    and oldest is not None and oldest < start_ms)
    return {
        "symbol": market["symbol"], "query": params,
        "window_start_ms": start_ms, "window_end_ms": end_ms,
        "read_start_ms": read_start, "read_end_ms": read_end, "source_time_ms": source,
        "oldest_trade_ms": oldest, "complete": complete,
        "coverage_basis": "endpoint_latest_suffix_normal_api_semantics_inference",
        "source_time_is_completion_watermark": False, "zero_latency_guaranteed": False,
        "native_response": deepcopy(raw),
    }


async def verify_bybit_testnet_account(exchange, expected_uid: str) -> dict:
    """Verify the actual key UID, unified account and spot-trading permission.

    Sandbox routing must already be enabled before any network request. Never
    return the raw query-api payload: it includes the complete API key.
    """
    _require_bybit_testnet_spot(exchange)
    if not isinstance(expected_uid, str) or not expected_uid.isdecimal():
        raise ValueError("A numeric expected UID is required")
    raw = await exchange.private_get_v5_user_query_api()
    result = raw.get("result", {})
    if raw.get("retCode") != 0 or str(result.get("userID")) != expected_uid:
        raise AuthenticationError("The API key UID does not match the expected account")
    if result.get("uta") != 1:
        raise NotSupported("A unified account is required")
    if result.get("readOnly") != 0 or "SpotTrade" not in result.get("permissions", {}).get(
        "Spot", []
    ):
        raise PermissionDenied("The verified API key needs spot-trading permission")
    account = await exchange.private_get_v5_account_info()
    status = account.get("result", {}).get("unifiedMarginStatus")
    if account.get("retCode") != 0 or status not in (3, 4, 5, 6):
        raise NotSupported("Unified account status could not be verified")
    settings = await exchange.private_get_v5_account_user_setting_config()
    behavior = settings.get("result", {})
    if settings.get("retCode") != 0 or behavior.get("lpaSpot") is not False:
        raise NotSupported("Spot limit-price auto-adjustment must be disabled")
    if behavior.get("smsef") is not False:
        raise NotSupported("Third-currency spot fee deduction is not supported")
    margin_mode = account.get("result", {}).get("marginMode")
    if margin_mode not in ("ISOLATED_MARGIN", "REGULAR_MARGIN", "PORTFOLIO_MARGIN"):
        margin_mode = None
    return {
        "uid": expected_uid,
        "environment": "sandbox",
        "market_type": "spot",
        "unified_margin_status": status,
        "margin_mode": margin_mode,
        "spot_price_auto_adjustment": False,
        "spot_mnt_fee_deduction": False,
    }
