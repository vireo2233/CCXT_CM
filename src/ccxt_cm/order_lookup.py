"""Exact, read-only client-identity lookup using the existing CCXT Bybit client."""

import re

from ccxt import ExchangeError, NotSupported

from .account_guards import _require_bybit_testnet_spot


class ClientOrderLookupUnconfirmed(ExchangeError):
    """No unique, identity-matched order was proved; retain the original intent."""


async def fetch_bybit_testnet_spot_order_by_client_id(exchange, symbol, client_order_id):
    """Use official implicit endpoints, preserving cardinality before parsing.

    Official CCXT fetch_order forwards orderLinkId but selects only the first
    realtime row. Here both response cardinality and raw symbol are checked.
    Empty realtime/history responses never prove that a write did not happen.
    """
    _require_bybit_testnet_spot(exchange)
    if not isinstance(client_order_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,36}", client_order_id
    ):
        raise ValueError("A valid original Bybit client order ID is required")
    market = exchange.market(symbol)
    if market.get("spot") is not True or not isinstance(market.get("id"), str):
        raise NotSupported("Client-identity lookup requires an already loaded spot market")
    params = {
        "category": "spot",
        "symbol": market["id"],
        "orderLinkId": client_order_id,
        "orderFilter": "Order",
        "limit": 2,
    }
    for method in (
        exchange.private_get_v5_order_realtime,
        exchange.private_get_v5_order_history,
    ):
        raw = await method(dict(params))
        result = raw.get("result") if isinstance(raw, dict) else None
        if (
            not isinstance(raw, dict)
            or raw.get("retCode") != 0
            or not isinstance(result, dict)
            or result.get("category", "spot") != "spot"
            or not isinstance(result.get("list"), list)
            or result.get("nextPageCursor") not in (None, "")
        ):
            raise ClientOrderLookupUnconfirmed("Exact order lookup response is unconfirmed")
        rows = result["list"]
        if not rows:
            continue
        if len(rows) != 1:
            raise ClientOrderLookupUnconfirmed("Exact order lookup is not unique")
        row = rows[0]
        if (
            not isinstance(row, dict)
            or row.get("orderLinkId") != client_order_id
            or row.get("symbol") != market["id"]
            or not isinstance(row.get("orderId"), str)
            or not row["orderId"]
        ):
            raise ClientOrderLookupUnconfirmed("Exact order lookup identity is unconfirmed")
        order = exchange.parse_order(row, market)
        if (
            order.get("id") != row["orderId"]
            or order.get("clientOrderId") != client_order_id
            or order.get("symbol") != market["symbol"]
        ):
            raise ClientOrderLookupUnconfirmed("Parsed order lookup identity is unconfirmed")
        return order
    raise ClientOrderLookupUnconfirmed("Original order outcome remains unknown after exact queries")
