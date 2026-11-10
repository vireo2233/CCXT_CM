"""Spot native-batch evidence, limits and item outcomes; never write to probe support."""

import asyncio
import json
import re
from collections import Counter
from inspect import getattr_static

from ccxt import ExchangeError, NotSupported

from .account_guards import _require_bybit_testnet_spot
from .capabilities import capability
from .order_lookup import order_error_diagnostic

_METHODS = {"create": "createOrders", "cancel": "cancelOrders"}
_ENDPOINTS = {"create": "/v5/order/create-batch", "cancel": "/v5/order/cancel-batch"}


def spot_batch_capabilities(exchange, symbol, identity=None):
    """Unknown/emulated SDK methods are not evidence of account-scoped native support.

    Only the verified Bybit Testnet spot account is integrated here. Other native
    methods remain unknown; an explicit False/emulated declaration selects single
    orders in advance. No permissions, account modes or writes are changed.
    """
    result = {
        operation: {
            "native": False if capability(exchange, method) in (False, "emulated") else None,
            "single": capability(
                exchange,
                "createOrder" if operation == "create" else "cancelOrder",
            ),
            "max_orders": 1,
            "endpoint": None,
        }
        for operation, method in _METHODS.items()
    }
    if (
        exchange.id == "binance"
        and (identity or {}).get("environment") == "sandbox"
        and (identity or {}).get("market_type") == "spot"
    ):
        # CCXT's aggregate batch declarations can refer to derivatives.
        for caps in result.values():
            caps["native"] = False
    if exchange.id != "bybit":
        return result
    try:
        _require_bybit_testnet_spot(exchange)
    except NotSupported:
        return result
    market = exchange.market(symbol)
    identity = identity or {}
    if (
        market.get("spot") is not True
        or identity.get("environment") != "sandbox"
        or identity.get("market_type") != "spot"
        or identity.get("unified_margin_status") not in (3, 4, 5, 6)
        or not isinstance(identity.get("uid"), str)
        or not identity["uid"].isdecimal()
    ):
        return result
    for operation, method in _METHODS.items():
        if capability(exchange, method) is True:
            result[operation] = {
                "native": True,
                "single": result[operation]["single"],
                "max_orders": 10,
                "endpoint": _ENDPOINTS[operation],
            }
    return result


async def submit_spot_batch(exchange, symbol, operation, items, identity):
    """One native HTTP request, retaining every original identity and uncertainty.

    Bybit's two response lists are aligned to each other, NOT to request order.
    Match those pairs by client ID (create) or order ID (cancel). Missing, duplicate
    or malformed pairs remain unknown. Cancel acceptance never implies terminal.
    CCXT's cancel_orders parser currently drops retExtInfo, so use its existing
    signed implicit transport here rather than infer success from parsed ID rows.
    """
    if operation not in _METHODS:
        raise ValueError("Unknown spot batch operation")
    caps = spot_batch_capabilities(exchange, symbol, identity)[operation]
    if caps["native"] is not True:
        raise NotSupported("Account-scoped native spot batch capability is unconfirmed")
    if not 1 <= len(items) <= caps["max_orders"]:
        raise ValueError("Native spot batch exceeds declared item limit")
    market = exchange.market(symbol)
    requests, keys = [], []
    for item in items:
        if operation == "create":
            key = (item.get("params") or {}).get("clientOrderId")
            if item.get("symbol") != symbol:
                raise ValueError("Spot batch symbol mismatch")
            row = exchange.create_order_request(
                symbol,
                item["type"],
                item["side"],
                item["amount"],
                item["price"],
                dict(item.get("params") or {}),
                True,
            )
            if row.pop("category", None) != "spot":
                raise ValueError("Spot batch cannot route to another market type")
        else:
            key = item
            row = {"symbol": market["id"], "orderId": key}
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise ValueError("Original batch identity is required")
        keys.append(key)
        requests.append(row)
    if len(set(keys)) != len(keys):
        raise ValueError("Batch identities must be unique")
    path = caps["endpoint"].lstrip("/")
    # Use the generated endpoint's API selector (a string or list in CCXT).
    route = getattr_static(
        type(exchange), "private_post_" + path.replace("/", "_").replace("-", "_")
    )
    config = dict(exchange.api["private"]["post"][path])
    # Spot batch quota counts children, not just HTTP requests. Reuse the
    # existing CCXT token bucket and conservative endpoint weight per child.
    config["cost"] *= len(items)
    async with asyncio.timeout(exchange.timeout / 1000):
        raw = await exchange.request(
            path,
            route.api,
            "POST",
            {
                "category": "spot",
                "request": requests,
                # fetch2 consumes this control before signing. A lost write
                # reply must be resolved by the original ID, never replayed.
                "maxRetriesOnFailure": 0,
            },
            config=config,
        )
    result_info = raw.get("result") if isinstance(raw, dict) else None
    error_info = raw.get("retExtInfo") if isinstance(raw, dict) else None
    rows = result_info.get("list") if isinstance(result_info, dict) else None
    codes = error_info.get("list") if isinstance(error_info, dict) else None
    if (
        not isinstance(raw, dict)
        or type(raw.get("retCode")) is not int
        or raw["retCode"] != 0
        or not isinstance(rows, list)
        or len(rows) > len(items)
    ):
        raise ExchangeError("Native batch response lacks matched item evidence")
    if not isinstance(codes, list) or len(rows) != len(codes):
        # Retain unique returned identities, without assigning unmatched error
        # codes or treating an absent code as success/zero.
        codes = [{} for _ in rows]
    key_field = "orderLinkId" if operation == "create" else "orderId"
    counts = Counter(r.get(key_field) for r in rows if isinstance(r, dict))
    ids = Counter(r.get("orderId") for r in rows if isinstance(r, dict) and r.get("orderId"))
    matched = {
        r[key_field]: (r, code)
        for r, code in zip(rows, codes, strict=True)
        if isinstance(r, dict) and r.get(key_field) in keys and counts[r[key_field]] == 1
    }
    result = []
    for key in keys:
        row, code_row = matched.get(key, ({}, {}))
        oid, cid = row.get("orderId"), row.get("orderLinkId")
        oid_valid = isinstance(oid, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", oid))
        code = code_row.get("code") if isinstance(code_row, dict) else None
        if isinstance(code, str) and code.isascii() and code.isdecimal():
            code = int(code)
        diagnostic = {"reason": "batch_item_unconfirmed"}
        outcome = "unknown"
        if type(code) is int and row.get("symbol") == market["id"]:
            diagnostic = order_error_diagnostic(ExchangeError(json.dumps({"retCode": code})))
            if code == 0 and oid_valid and ids[oid] == 1:
                outcome = "acknowledged" if operation == "create" else "cancel_pending"
                diagnostic = {"error_code": 0, "reason": outcome}
            elif operation == "create" and not oid and diagnostic.get("definite_rejection"):
                outcome = "not_created"
        result.append(
            {
                "id": key
                if operation == "cancel"
                else (
                    oid
                    if oid_valid and ids[oid] == 1 and row.get("symbol") == market["id"]
                    else None
                ),
                "clientOrderId": key if operation == "create" else cid,
                "symbol": symbol,
                "cm_outcome": outcome,
                "cm_diagnostic": diagnostic,
            }
        )
    return result
