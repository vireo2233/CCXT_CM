"""Identity and public read-evidence guards using official implicit endpoints."""

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


async def fetch_binance_spot_public_window(exchange, symbol, *, start_ms, end_ms):
    """Bounded aggregate pagination under normal API semantics, not a publication watermark.

    Binance documents inclusive time/ID bounds and chronological responses.
    Database publication can lag; server time only establishes that the cutoff
    has arrived. A full page requires continuation, including same-ms trades.
    """
    def require(condition, code):
        if not condition:
            raise ValueError("binance_public_window:" + code)

    def route():
        require(exchange.id == "binance" and exchange.isSandboxModeEnabled is False
                and exchange.options.get("defaultType") == "spot"
                and not exchange.options.get("enableDemoTrading")
                and not any(getattr(exchange, key, None)
                            for key in ("apiKey", "secret", "password"))
                and exchange.urls.get("api", {}).get("public")
                == "https://data-api.binance.vision/api/v3",
                "public_route")

    route()
    require(type(start_ms) is int and type(end_ms) is int and 0 <= start_ms < end_ms
            and end_ms - start_ms <= 60000, "invalid_window")
    market = exchange.market(symbol)
    require(isinstance(market, dict) and market.get("spot") is True
            and isinstance(market.get("id"), str) and bool(market["id"])
            and market.get("symbol") == symbol, "spot_market")
    timeout = exchange.timeout
    require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
            "invalid_timeout")
    read_start = exchange.milliseconds()
    require(type(read_start) is int and end_ms - 1 <= read_start, "cutoff_not_reached")
    pages, rows, seen, anchor, terminal = [], [], set(), None, None
    async with asyncio.timeout(timeout / 1000):
        before = await exchange.public_get_time()
        require(isinstance(before, dict) and type(before.get("serverTime")) is int
                and before["serverTime"] >= end_ms, "server_cutoff_not_reached")
        for _ in range(60):  # Finite read budget, never an unbounded recovery/retry loop.
            route()
            params = {"symbol": market["id"], "limit": 1000}
            if anchor is None:
                params.update(startTime=start_ms, endTime=end_ms - 1)
            else:
                params["fromId"] = anchor["a"]
            raw = await exchange.publicGetAggTrades(dict(params))
            require(isinstance(raw, list) and len(raw) <= 1000, "response_shape")
            pages.append({"query": params, "response": deepcopy(raw)})
            if anchor is not None:
                require(bool(raw) and raw[0] == anchor, "continuation_anchor")
            new = raw if anchor is None else raw[1:]
            for row in new:
                require(isinstance(row, dict)
                        and all(type(row.get(key)) is int and row[key] >= 0
                                for key in ("a", "f", "l"))
                        and row["f"] <= row["l"] and type(row.get("T")) is int and row["T"] > 0
                        and type(row.get("m")) is bool and row["a"] not in seen, "trade_identity")
                require(not rows or (row["a"] > rows[-1]["a"] and row["T"] >= rows[-1]["T"]),
                        "trade_order")
                require(row["T"] >= start_ms and (anchor is not None or row["T"] < end_ms),
                        "trade_window")
                for key in ("p", "q"):
                    value = row.get(key)
                    require(isinstance(value, str) and bool(value) and value.strip() == value,
                            "trade_number")
                    try:
                        number = Decimal(value)
                    except InvalidOperation:
                        raise ValueError("binance_public_window:trade_number") from None
                    require(number.is_finite() and number > 0, "trade_number")
                seen.add(row["a"])
                rows.append(deepcopy(row))
            if new and new[-1]["T"] >= end_ms:
                terminal = "successor_at_or_after_cutoff"
            elif len(raw) < 1000:
                terminal = ("bounded_query_exhausted" if anchor is None
                            else "id_continuation_exhausted")
            if terminal:
                break
            require(bool(new), "pagination_no_progress")
            anchor = deepcopy(new[-1])
        require(terminal is not None, "pagination_budget_exhausted")
        route()
        after = await exchange.public_get_time()
    read_end = exchange.milliseconds()
    require(type(read_end) is int and read_start <= read_end <= read_start + timeout
            and isinstance(after, dict) and type(after.get("serverTime")) is int
            and read_start <= before["serverTime"] <= after["serverTime"] <= read_end,
            "read_clock_order")
    require(all(row["T"] <= after["serverTime"] for row in rows), "future_trade")
    return {
        "exchange": "binance", "environment": "mainnet", "market_type": "spot", "symbol": symbol,
        "window_start_ms": start_ms, "window_end_ms": end_ms,
        "read_start_ms": read_start, "read_end_ms": read_end,
        "server_time_before_ms": before["serverTime"], "server_time_after_ms": after["serverTime"],
        "complete": terminal is not None, "termination": terminal,
        "coverage_basis": "bounded_aggregate_pagination_normal_api_semantics_inference",
        "source_time_is_completion_watermark": False, "zero_latency_guaranteed": False,
        "native_responses": pages, "trades": [row for row in rows if row["T"] < end_ms],
    }


async def fetch_bybit_testnet_spot_smp_settings(exchange) -> dict:
    """Read current SMP facts; neither an atomic snapshot nor a capability grant.

    ``source_ms`` is the earlier source time of the two sequential reads. Keep
    both original source times so callers cannot mistake it for a single read.
    """
    _require_bybit_testnet_spot(exchange)

    def require(condition, code):
        if not condition:
            raise ValueError("bybit_smp:" + code)

    timeout_ms = exchange.timeout
    require(type(timeout_ms) in (int, float) and math.isfinite(timeout_ms) and timeout_ms > 0,
            "invalid_timeout")
    read_start = exchange.milliseconds()
    require(type(read_start) is int and read_start >= 0, "read_clock_order")
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            settings = await exchange.private_get_v5_account_user_setting_config()
            _require_bybit_testnet_spot(exchange)
            group = await exchange.private_get_v5_account_smp_group()
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        raise ValueError("bybit_smp:read_timeout") from None
    except Exception:
        raise ValueError("bybit_smp:native_read_failed") from None
    read_end = exchange.milliseconds()
    require(type(read_end) is int and read_start <= read_end
            and read_end - read_start <= timeout_ms, "read_clock_order")
    for raw in (settings, group):
        require(isinstance(raw, dict) and type(raw.get("retCode")) is int and raw["retCode"] == 0
                and isinstance(raw.get("result"), dict) and type(raw.get("time")) is int,
                "response_shape")
    setting_source, group_source = settings["time"], group["time"]
    require(0 < setting_source and read_start <= setting_source <= group_source <= read_end,
            "source_clock_order")
    smp_type, smp_group = settings["result"].get("smpType"), group["result"].get("smpGroup")
    require(type(smp_type) is int and smp_type in (0, 1, 2, 3), "smp_type_unknown")
    require(type(smp_group) is int and smp_group >= 0, "smp_group_unknown")
    return {"smp_type": smp_type, "smp_group": smp_group,
            "source_ms": min(setting_source, group_source), "settings_source_ms": setting_source,
            "group_source_ms": group_source, "read_start_ms": read_start, "read_end_ms": read_end}


async def fetch_bybit_testnet_spot_account_mode(exchange) -> dict:
    """Read current account mode on the already-bound official Testnet client."""
    _require_bybit_testnet_spot(exchange)
    account = await exchange.private_get_v5_account_info()
    _require_bybit_testnet_spot(exchange)
    result = account.get("result") if isinstance(account, dict) else None
    if (not isinstance(result, dict) or type(account.get("retCode")) is not int
            or account["retCode"] != 0 or type(result.get("unifiedMarginStatus")) is not int
            or result["unifiedMarginStatus"] not in (3, 4, 5, 6)):
        raise NotSupported("Unified account status could not be verified")
    margin_mode = result.get("marginMode")
    if margin_mode not in ("ISOLATED_MARGIN", "REGULAR_MARGIN", "PORTFOLIO_MARGIN"):
        margin_mode = None
    return {"unified_margin_status": result["unifiedMarginStatus"], "margin_mode": margin_mode}


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
    account_mode = await fetch_bybit_testnet_spot_account_mode(exchange)
    settings = await exchange.private_get_v5_account_user_setting_config()
    behavior = settings.get("result", {})
    if settings.get("retCode") != 0 or behavior.get("lpaSpot") is not False:
        raise NotSupported("Spot limit-price auto-adjustment must be disabled")
    if behavior.get("smsef") is not False:
        raise NotSupported("Third-currency spot fee deduction is not supported")
    return {
        "uid": expected_uid,
        "environment": "sandbox",
        "market_type": "spot",
        **account_mode,
        "spot_price_auto_adjustment": False,
        "spot_mnt_fee_deduction": False,
    }
