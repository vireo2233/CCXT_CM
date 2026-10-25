"""Native CCXT signing/request construction, mocked only at the HTTP transport."""

import asyncio
import copy
from unittest.mock import AsyncMock, Mock
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


def assert_exact_request(call, path, cursor=None):
    url, method, headers, body = call.args
    parsed = urlsplit(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "api-testnet.bybit.com", path)
    assert method == "GET" and body is None
    expected = {
        "category": ["spot"],
        "symbol": ["BTCUSDT"],
        "orderLinkId": [CLIENT_ID],
        "orderFilter": ["Order"],
        "limit": ["2"],
    }
    if cursor is not None:
        expected["cursor"] = [cursor]
    assert parse_qs(parsed.query) == expected
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
        payload([ROW], nextPageCursor=None),
        payload([ROW], nextPageCursor=1),
        dict(payload([ROW]), retCode=False),
        dict(payload([ROW]), retCode="0"),
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


@pytest.mark.parametrize("history", [False, True])
async def test_single_match_with_cursor_is_not_returned_until_explicit_last_page(exchange, history):
    replies = [payload([copy.deepcopy(ROW)], nextPageCursor="original-page"), payload([])]
    if history:
        replies.insert(0, payload([]))
    exchange.fetch.side_effect = replies
    exchange.parse_order = Mock(wraps=exchange.parse_order)
    result = await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    assert result["id"] == ROW["orderId"]
    exchange.parse_order.assert_called_once()
    calls = exchange.fetch.await_args_list
    if history:
        assert_exact_request(calls.pop(0), "/v5/order/realtime")
    endpoint = "/v5/order/history" if history else "/v5/order/realtime"
    assert len(calls) == 2
    assert_exact_request(calls[0], endpoint)
    assert_exact_request(calls[1], endpoint, "original-page")


@pytest.mark.parametrize(
    "second",
    [
        payload([ROW]),  # Even an identical duplicate is not a unique result.
        payload([dict(ROW, orderId="second-id")]),
        payload([dict(ROW, orderLinkId="foreign-client")]),
        payload([dict(ROW, symbol="ETHUSDT")]),
        payload([], nextPageCursor="first-page"),
        payload([], nextPageCursor="unexpected-more"),
        payload([ROW], nextPageCursor=None),
        payload([], nextPageCursor=None),
        {"retCode": 0, "result": {"category": "spot", "list": []}},
        {"retCode": 0, "result": {"category": "spot", "list": [ROW]}},
        {"retCode": 0, "result": {"category": "spot", "nextPageCursor": ""}},
        payload([ROW, ROW, ROW]),
    ],
)
async def test_later_pages_cannot_hide_duplicates_foreign_rows_or_truncation(exchange, second):
    exchange.fetch.side_effect = [
        payload([ROW], nextPageCursor="first-page"),
        copy.deepcopy(second),
    ]
    exchange.parse_order = Mock(wraps=exchange.parse_order)
    with pytest.raises(ClientOrderLookupUnconfirmed) as result:
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    assert "fixture-secret" not in str(result.value)
    assert exchange.fetch.await_count == 2
    exchange.parse_order.assert_not_called()


async def test_missing_initial_cursor_remains_unknown(exchange):
    response = payload([ROW])
    del response["result"]["nextPageCursor"]
    exchange.fetch.return_value = response
    with pytest.raises(ClientOrderLookupUnconfirmed):
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    exchange.fetch.assert_awaited_once()


async def test_page_limit_cannot_turn_partial_match_into_success(exchange, monkeypatch):
    monkeypatch.setattr("ccxt_cm.order_lookup._MAX_LOOKUP_PAGES", 1)
    exchange.fetch.return_value = payload([ROW], nextPageCursor="not-exhausted")
    with pytest.raises(ClientOrderLookupUnconfirmed, match="page limit"):
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    exchange.fetch.assert_awaited_once()


async def test_native_transport_exception_never_exposes_venue_text(exchange):
    exchange.fetch.side_effect = [
        payload([ROW], nextPageCursor="first-page"),
        ccxt.ExchangeError("fixture-secret-native-error"),
    ]
    with pytest.raises(ClientOrderLookupUnconfirmed, match="request is unconfirmed") as result:
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    assert "fixture-secret" not in str(result.value)
    assert result.value.__suppress_context__


async def test_total_deadline_includes_empty_realtime_and_history(exchange, monkeypatch):
    exchange.timeout = 25
    timeout = Mock(wraps=asyncio.timeout)
    monkeypatch.setattr("ccxt_cm.order_lookup.asyncio.timeout", timeout)

    async def slow_fetch(*args):
        if urlsplit(args[0]).path == "/v5/order/history":
            await asyncio.sleep(1)
        return payload([])

    exchange.fetch.side_effect = slow_fetch
    with pytest.raises(ClientOrderLookupUnconfirmed, match="deadline"):
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    assert exchange.fetch.await_count == 2
    timeout.assert_called_once_with(0.025)


async def test_shorter_caller_timeout_still_cancels_lookup(exchange):
    async def slow_fetch(*args):
        await asyncio.sleep(1)
        return payload([])

    exchange.fetch.side_effect = slow_fetch
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.001):
            await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    exchange.fetch.assert_awaited_once()


@pytest.mark.parametrize("timeout", [None, 0, -1, True, float("inf"), float("nan")])
async def test_invalid_total_timeout_never_sends(exchange, timeout):
    exchange.timeout = timeout
    with pytest.raises(ValueError, match="timeout"):
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    exchange.fetch.assert_not_awaited()


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


@pytest.mark.parametrize("parsed", [None, [], 1])
async def test_malformed_parser_result_remains_unknown(exchange, parsed):
    exchange.parse_order = Mock(return_value=parsed)
    with pytest.raises(ClientOrderLookupUnconfirmed, match="Parsed"):
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)


async def test_parser_exception_never_exposes_native_fields(exchange):
    exchange.parse_order = Mock(side_effect=ValueError("fixture-secret-native-parser"))
    with pytest.raises(ClientOrderLookupUnconfirmed, match="Parsed") as result:
        await fetch_bybit_testnet_spot_order_by_client_id(exchange, SYMBOL, CLIENT_ID)
    assert "fixture-secret" not in str(result.value)
    assert result.value.__suppress_context__
