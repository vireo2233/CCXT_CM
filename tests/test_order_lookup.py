"""Native CCXT signing/request construction, mocked only at the HTTP transport."""

import copy
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import ccxt
import pytest

from ccxt_cm import (
    ClientOrderLookupUnconfirmed,
    create_exchange,
    fetch_bybit_testnet_spot_order_by_client_id,
)

SYMBOL = "BTC/USDT"
CLIENT_ID = "cmseed-0123456789abcdef-bid"
MARKET = {
    "id": "BTCUSDT",
    "symbol": SYMBOL,
    "base": "BTC",
    "quote": "USDT",
    "spot": True,
    "type": "spot",
    "contract": False,
    "active": True,
    "precision": {"price": 0.01, "amount": 0.001},
    "limits": {},
}
ROW = {
    "orderId": "original-order-id",
    "orderLinkId": CLIENT_ID,
    "symbol": "BTCUSDT",
    "orderType": "Limit",
    "side": "Buy",
    "qty": "2",
    "price": "10",
    "orderStatus": "Cancelled",
    "cumExecQty": "0.3",
    "cumExecValue": "3",
    "leavesQty": "1.7",
    "avgPrice": "10",
    "timeInForce": "GTC",
    "createdTime": "1700000000000",
    "updatedTime": "1700000000010",
}


def payload(rows, **updates):
    return {
        "retCode": 0,
        "retMsg": "fixture-secret-never-expose",
        "result": {"category": "spot", "list": rows, "nextPageCursor": "", **updates},
    }


@pytest.fixture
async def exchange():
    value = create_exchange(
        "bybit",
        {
            "apiKey": "fixture-key",
            "secret": "fixture-secret",
            "enableRateLimit": False,
            "options": {
                "defaultType": "spot",
                "enableUnifiedMargin": False,
                "enableUnifiedAccount": True,
            },
        },
        mode="async",
    )
    value.set_sandbox_mode(True)
    value.set_markets([copy.deepcopy(MARKET)])
    value.fetch = AsyncMock(return_value=payload([copy.deepcopy(ROW)]))
    try:
        yield value
    finally:
        await value.close()


def assert_exact_request(call, path):
    url, method, headers, body = call.args
    parsed = urlsplit(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "api-testnet.bybit.com", path)
    assert method == "GET" and body is None
    assert parse_qs(parsed.query) == {
        "category": ["spot"],
        "symbol": ["BTCUSDT"],
        "orderLinkId": [CLIENT_ID],
        "orderFilter": ["Order"],
        "limit": ["2"],
    }
    assert headers["X-BAPI-API-KEY"] == "fixture-key"
    assert headers["X-BAPI-SIGN"]


async def test_realtime_exact_lookup_uses_real_ccxt_request_and_parser(exchange):
    result = await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    assert result["id"] == ROW["orderId"] and result["clientOrderId"] == CLIENT_ID
    assert result["symbol"] == SYMBOL and result["status"] == "canceled"
    assert result["filled"] == 0.3
    exchange.fetch.assert_awaited_once()
    assert_exact_request(exchange.fetch.await_args, "/v5/order/realtime")


async def test_empty_realtime_queries_history_with_identical_original_identity(exchange):
    exchange.fetch.side_effect = [payload([]), payload([copy.deepcopy(ROW)])]
    result = await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    assert result["id"] == ROW["orderId"]
    assert len(exchange.fetch.await_args_list) == 2
    for call, path in zip(
        exchange.fetch.await_args_list, ["/v5/order/realtime", "/v5/order/history"], strict=True
    ):
        assert_exact_request(call, path)


async def test_empty_both_does_not_prove_order_was_never_created(exchange):
    exchange.fetch.return_value = payload([])
    with pytest.raises(ClientOrderLookupUnconfirmed, match="remains unknown") as result:
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    assert "fixture-secret" not in str(result.value)
    assert exchange.fetch.await_count == 2


@pytest.mark.parametrize(
    "response",
    [
        payload([ROW, ROW]),
        payload([dict(ROW, orderLinkId="foreign-client")]),
        payload([dict(ROW, symbol="ETHUSDT")]),
        payload([dict(ROW, orderId=None)]),
        payload([dict(ROW, orderId="")]),
        payload([None]),
        payload([ROW], category="linear"),
        payload([ROW], nextPageCursor="more-rows"),
        payload(None),
        [],
        {"retCode": 10001, "retMsg": "fixture-secret-never-expose", "result": {}},
    ],
)
async def test_ambiguous_foreign_or_malformed_result_fails_without_fallback(exchange, response):
    exchange.fetch.return_value = copy.deepcopy(response)
    with pytest.raises(ClientOrderLookupUnconfirmed) as result:
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    assert "fixture-secret" not in str(result.value)
    exchange.fetch.assert_awaited_once()


@pytest.mark.parametrize("change", ["sandbox", "route", "market", "default_type", "exchange"])
async def test_wrong_environment_or_nonspot_never_sends(exchange, change):
    if change == "sandbox":
        exchange.isSandboxModeEnabled = False
    elif change == "route":
        exchange.urls["api"]["private"] = "https://api.bybit.com"
    elif change == "market":
        exchange.market(SYMBOL)["spot"] = False
    elif change == "default_type":
        exchange.options["defaultType"] = "swap"
    else:
        exchange.id = "other"
    with pytest.raises(ccxt.NotSupported):
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    exchange.fetch.assert_not_awaited()


@pytest.mark.parametrize("identity", [None, "", "with space", "x" * 37])
async def test_invalid_original_identity_never_sends(exchange, identity):
    with pytest.raises(ValueError):
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, identity)
    exchange.fetch.assert_not_awaited()


async def test_official_fetch_order_forwards_identity_but_silently_selects_first_row(exchange):
    exchange.fetch.return_value = payload([copy.deepcopy(ROW), dict(ROW, orderId="second-id")])
    result = await exchange.fetch_order(
        None, SYMBOL, {"orderLinkId": CLIENT_ID, "acknowledged": True}
    )
    assert result["id"] == ROW["orderId"]  # Native method discards cardinality evidence.
    query = parse_qs(urlsplit(exchange.fetch.await_args.args[0]).query)
    assert query["orderLinkId"] == [CLIENT_ID]
    assert urlsplit(exchange.fetch.await_args.args[0]).netloc == "api-testnet.bybit.com"


async def test_parsed_identity_is_checked_again(exchange):
    exchange.parse_order = lambda row, market: {
        "id": row["orderId"],
        "symbol": "ETH/USDT",
        "clientOrderId": CLIENT_ID,
    }
    with pytest.raises(ClientOrderLookupUnconfirmed, match="Parsed"):
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
