"""Exact, read-only client-identity lookup using the existing CCXT Bybit client."""

import asyncio
import json
import math
import re

from ccxt import ExchangeError, NotSupported

from .account_guards import _require_bybit_testnet_spot

_MAX_LOOKUP_PAGES = 10


class ClientOrderLookupUnconfirmed(ExchangeError):
    """No unique, identity-matched order was proved; retain the original intent."""

    def __init__(self, message, diagnostic=None):
        super().__init__(message)
        self.diagnostic = diagnostic or {"reason": message}


def order_error_diagnostic(exc):
    """Whitelist protocol codes and fixed reasons, never transport text/credentials.

    Duplicate IDs, timeouts, rate limits and unknown errors do not prove rejection.
    Classification is meaningful only for an original create request, not reads.
    """
    result = {"error_type": type(exc).__name__, "reason": "response_unconfirmed"}
    try:
        text = str(exc)[:8192]
        raw, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
    except (ValueError, TypeError):
        return result
    if not isinstance(raw, dict) or type(raw.get("retCode")) is not int:
        return result
    code = raw["retCode"]
    reasons = {
        10001: "invalid_request_parameters", 10002: "request_time_window_expired",
        10003: "invalid_api_key", 10004: "invalid_signature", 10005: "permission_denied",
        10006: "rate_limit", 110072: "duplicate_client_order_id",
        170131: "insufficient_balance", 170132: "price_too_high",
        170133: "price_below_minimum", 170134: "price_precision_invalid",
        170136: "quantity_below_minimum", 170137: "quantity_precision_invalid",
        170140: "order_value_below_minimum", 170141: "duplicate_client_order_id",
        170146: "creation_timeout", 170193: "buy_price_above_limit",
        170194: "sell_price_below_limit", 170210: "new_order_rejected",
    }
    result.update(error_code=code, reason=reasons.get(code, "exchange_error_text_redacted"))
    result["definite_rejection"] = (
        isinstance(exc, ExchangeError) and code in reasons
        and code not in (10006, 110072, 170141, 170146)
    )
    return result


async def _exact_rows(method, params):
    rows, cursors = [], set()
    params = dict(params)
    for _ in range(_MAX_LOOKUP_PAGES):
        try:
            raw = await method(dict(params))
        except Exception as exc:
            raise ClientOrderLookupUnconfirmed(
                "Exact order lookup request is unconfirmed", order_error_diagnostic(exc)
            ) from None
        result = raw.get("result") if isinstance(raw, dict) else None
        if (
            not isinstance(raw, dict)
            or type(raw.get("retCode")) is not int
            or raw["retCode"] != 0
            or not isinstance(result, dict)
            or result.get("category", "spot") != "spot"
            or not isinstance(result.get("list"), list)
            or not isinstance(result.get("nextPageCursor"), str)
            or len(result["list"]) > params["limit"]
        ):
            raise ClientOrderLookupUnconfirmed("Exact order lookup response is unconfirmed")
        for row in result["list"]:
            if (
                not isinstance(row, dict)
                or row.get("orderLinkId") != params["orderLinkId"]
                or row.get("symbol") != params["symbol"]
                or not isinstance(row.get("orderId"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", row["orderId"])
            ):
                raise ClientOrderLookupUnconfirmed("Exact order lookup identity is unconfirmed")
            rows.append(row)
        if len(rows) > 1:
            raise ClientOrderLookupUnconfirmed("Exact order lookup is not unique")
        cursor = result["nextPageCursor"]
        if cursor == "":
            return rows
        if not result["list"] or cursor in cursors:
            raise ClientOrderLookupUnconfirmed("Exact order lookup pagination is unconfirmed")
        cursors.add(cursor)
        params["cursor"] = cursor
    raise ClientOrderLookupUnconfirmed("Exact order lookup page limit remains unconfirmed")


async def fetch_bybit_testnet_spot_order_by_client_id(exchange, symbol, client_order_id):
    """Use official implicit endpoints, preserving cardinality before parsing.

    Official CCXT fetch_order forwards orderLinkId but selects only the first
    realtime row. Here complete pagination, cardinality and raw symbol are checked.
    Empty realtime/history responses never prove that a write did not happen.
    The existing CCXT request timeout bounds the entire lookup, not each page;
    a caller's shorter timeout/cancellation still takes precedence.
    """
    _require_bybit_testnet_spot(exchange)
    if not isinstance(client_order_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,36}", client_order_id
    ):
        raise ValueError("A valid original Bybit client order ID is required")
    market = exchange.market(symbol)
    if market.get("spot") is not True or not isinstance(market.get("id"), str):
        raise NotSupported("Client-identity lookup requires an already loaded spot market")
    timeout_ms = exchange.timeout
    if type(timeout_ms) not in (int, float) or not math.isfinite(timeout_ms) or timeout_ms <= 0:
        raise ValueError("A finite positive CCXT request timeout is required")
    params = {
        "category": "spot",
        "symbol": market["id"],
        "orderLinkId": client_order_id,
        "orderFilter": "Order",
        "limit": 2,
    }
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            for method in (
                exchange.private_get_v5_order_realtime,
                exchange.private_get_v5_order_history,
            ):
                try:
                    rows = await _exact_rows(method, params)
                except ClientOrderLookupUnconfirmed as exc:
                    exc.diagnostic["step"] = (
                        "order_realtime" if method == exchange.private_get_v5_order_realtime
                        else "order_history"
                    )
                    raise
                if not rows:
                    continue
                row = rows[0]
                try:
                    order = exchange.parse_order(row, market)
                except Exception as exc:
                    raise ClientOrderLookupUnconfirmed(
                        "Parsed order lookup response is unconfirmed", {
                            "step": "order_parse", "reason": "order_parse_failed",
                            "error_type": type(exc).__name__, "order_id": row["orderId"],
                            "client_order_id": client_order_id,
                        },
                    ) from None
                if (
                    not isinstance(order, dict)
                    or order.get("id") != row["orderId"]
                    or order.get("clientOrderId") != client_order_id
                    or order.get("symbol") != market["symbol"]
                ):
                    raise ClientOrderLookupUnconfirmed(
                        "Parsed order lookup identity is unconfirmed", {
                            "step": "order_parse", "reason": "parsed_order_identity_mismatch",
                            "order_id": row["orderId"], "client_order_id": client_order_id,
                        },
                    )
                return order
    except TimeoutError:
        raise ClientOrderLookupUnconfirmed(
            "Exact order lookup deadline remains unconfirmed"
        ) from None
    raise ClientOrderLookupUnconfirmed("Original order outcome remains unknown after exact queries")


async def fetch_open_orders_complete(exchange, symbol):
    """Complete, bounded-current open-order directory; no historical adoption.

    Only the already guarded spot adapter is implemented here. Other venues
    must explicitly implement completeness rather than inheriting truncated data.
    """
    _require_bybit_testnet_spot(exchange)
    market = exchange.market(symbol)
    params = {
        "category": "spot",
        "symbol": market["id"],
        "openOnly": 0,
        "orderFilter": "Order",
        "limit": 50,
    }
    orders, cursors = {}, set()
    while True:
        async with asyncio.timeout(exchange.timeout / 1000):
            raw = await exchange.private_get_v5_order_realtime(dict(params))
        result = raw.get("result") if isinstance(raw, dict) else None
        if (
            not isinstance(result, dict)
            or raw.get("retCode") != 0
            or not isinstance(result.get("list"), list)
            or len(result["list"]) > 50
            or not isinstance(result.get("nextPageCursor"), str)
        ):
            raise ClientOrderLookupUnconfirmed("Open-order directory is incomplete")
        for row in result["list"]:
            if (
                not isinstance(row, dict)
                or row.get("symbol") != market["id"]
                or not row.get("orderId")
                or row["orderId"] in orders
            ):
                raise ClientOrderLookupUnconfirmed("Open-order directory identity conflict")
            try:
                order = exchange.parse_order(row, market)
            except Exception:
                raise ClientOrderLookupUnconfirmed("Open-order mapping is unconfirmed") from None
            if (
                not isinstance(order, dict)
                or order.get("id") != row["orderId"]
                or order.get("symbol") != symbol
            ):
                raise ClientOrderLookupUnconfirmed("Open-order mapping is unconfirmed")
            if len(orders) >= 8192:
                raise ClientOrderLookupUnconfirmed(
                    "Open-order directory exceeds current-state capacity"
                )
            orders[row["orderId"]] = order
        cursor = result["nextPageCursor"]
        if not cursor:
            return list(orders.values())
        if not result["list"] or cursor in cursors or len(orders) >= 8192:
            raise ClientOrderLookupUnconfirmed(
                "Open-order directory exceeds current-state capacity"
            )
        cursors.add(cursor)
        params["cursor"] = cursor
