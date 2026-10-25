"""Exact, read-only client-identity lookup using the existing CCXT Bybit client."""

import asyncio
import math
import re

from ccxt import ExchangeError, NotSupported

from .account_guards import _require_bybit_testnet_spot

_MAX_LOOKUP_PAGES = 10


class ClientOrderLookupUnconfirmed(ExchangeError):
    """No unique, identity-matched order was proved; retain the original intent."""


async def _exact_rows(method, params):
    rows, cursors = [], set()
    params = dict(params)
    for _ in range(_MAX_LOOKUP_PAGES):
        try:
            raw = await method(dict(params))
        except Exception:
            raise ClientOrderLookupUnconfirmed(
                "Exact order lookup request is unconfirmed"
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
                or not row["orderId"]
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
                rows = await _exact_rows(method, params)
                if not rows:
                    continue
                row = rows[0]
                try:
                    order = exchange.parse_order(row, market)
                except Exception:
                    raise ClientOrderLookupUnconfirmed(
                        "Parsed order lookup response is unconfirmed"
                    ) from None
                if (
                    not isinstance(order, dict)
                    or order.get("id") != row["orderId"]
                    or order.get("clientOrderId") != client_order_id
                    or order.get("symbol") != market["symbol"]
                ):
                    raise ClientOrderLookupUnconfirmed(
                        "Parsed order lookup identity is unconfirmed"
                    )
                return order
    except TimeoutError:
        raise ClientOrderLookupUnconfirmed(
            "Exact order lookup deadline remains unconfirmed"
        ) from None
    raise ClientOrderLookupUnconfirmed("Original order outcome remains unknown after exact queries")
