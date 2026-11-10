"""Offline native HTTP transport and wallet-source tests, no account credentials."""

import copy
import json
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit

import ccxt
import pytest

from ccxt_cm import create_exchange
from ccxt_cm.account_guards import preserve_bybit_wallet_time
from ccxt_cm.spot_batch import spot_batch_capabilities, submit_spot_batch

SYMBOL = "BTC/USDT"
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
IDENTITY = {
    "uid": "123",
    "environment": "sandbox",
    "market_type": "spot",
    "unified_margin_status": 5,
}
ORDERS = [
    {
        "symbol": SYMBOL,
        "type": "limit",
        "side": side,
        "amount": "0.002",
        "price": "10",
        "params": {"clientOrderId": "cm-" + side},
    }
    for side in ("buy", "sell")
]


def response(rows, codes):
    return {
        "retCode": 0,
        "result": {"list": rows},
        "retExtInfo": {"list": [{"code": code, "msg": "fixture-secret"} for code in codes]},
    }


def row(cid, oid):
    return {"symbol": "BTCUSDT", "orderId": oid, "orderLinkId": cid}


@pytest.fixture
async def exchange():
    value = create_exchange(
        "bybit",
        {
            "apiKey": "fixture-key",
            "secret": "fixture-secret",
            "enableRateLimit": False,
            "options": {"defaultType": "spot"},
        },
        mode="pro",
    )
    value.set_sandbox_mode(True)
    value.set_markets([copy.deepcopy(MARKET)])
    value.fetch = AsyncMock(return_value=response([], []))
    try:
        yield value
    finally:
        await value.close()


async def test_account_environment_market_scoped_capabilities_do_not_write(exchange):
    caps = spot_batch_capabilities(exchange, SYMBOL, IDENTITY)
    assert caps == {
        "create": {
            "native": True,
            "single": True,
            "max_orders": 10,
            "endpoint": "/v5/order/create-batch",
        },
        "cancel": {
            "native": True,
            "single": True,
            "max_orders": 10,
            "endpoint": "/v5/order/cancel-batch",
        },
    }
    for identity in (
        None,
        {},
        {**IDENTITY, "unified_margin_status": 1},
        {**IDENTITY, "environment": "demo"},
    ):
        assert spot_batch_capabilities(exchange, SYMBOL, identity)["create"]["native"] is None
    exchange.set_sandbox_mode(False)
    assert spot_batch_capabilities(exchange, SYMBOL, IDENTITY)["create"]["native"] is None
    exchange.fetch.assert_not_awaited()


@pytest.mark.parametrize(
    "declared,expected", [(True, True), (False, False), (None, None), ("emulated", False)]
)
async def test_create_and_cancel_native_capabilities_are_independent(exchange, declared, expected):
    exchange.has["createOrders"] = declared
    caps = spot_batch_capabilities(exchange, SYMBOL, IDENTITY)
    assert caps["create"]["native"] is expected
    assert caps["cancel"]["native"] is True
    if expected is not True:
        with pytest.raises(ccxt.NotSupported):
            await submit_spot_batch(exchange, SYMBOL, "create", ORDERS, IDENTITY)
        exchange.fetch.assert_not_awaited()


async def test_native_create_one_real_endpoint_mixed_sides_unordered_item_mapping(exchange):
    exchange.fetch.return_value = response(
        [row("cm-sell", "sell-id"), row("cm-buy", "buy-id")],
        [0, 0],
    )
    result = await submit_spot_batch(exchange, SYMBOL, "create", ORDERS, IDENTITY)
    assert [r["id"] for r in result] == ["buy-id", "sell-id"]
    assert all(r["cm_outcome"] == "acknowledged" for r in result)
    exchange.fetch.assert_awaited_once()
    url, method, _, body = exchange.fetch.call_args.args
    target = urlsplit(url)
    assert target.hostname == "api-testnet.bybit.com"
    assert target.path == "/v5/order/create-batch" and method == "POST"
    payload = json.loads(body)
    assert payload["category"] == "spot"
    assert [r["side"] for r in payload["request"]] == ["Buy", "Sell"]
    assert [r["orderLinkId"] for r in payload["request"]] == ["cm-buy", "cm-sell"]
    assert "fixture-secret" not in str(result)


@pytest.mark.parametrize("operation", ["create", "cancel"])
async def test_batch_rate_limit_weights_children_without_changing_endpoint_metadata(
    exchange,
    operation,
):
    path = "/v5/order/" + operation + "-batch"
    metadata = copy.deepcopy(exchange.api["private"]["post"][path.lstrip("/")])
    exchange.enableRateLimit = True
    exchange.throttle = AsyncMock()
    exchange.fetch.return_value = response(
        [row("cm-buy", "buy-id"), row("cm-sell", "sell-id")],
        [0, 0],
    )
    items = ORDERS if operation == "create" else ["buy-id", "sell-id"]
    await submit_spot_batch(exchange, SYMBOL, operation, items, IDENTITY)
    exchange.throttle.assert_awaited_once_with(metadata["cost"] * len(items))
    assert exchange.api["private"]["post"][path.lstrip("/")] == metadata
    exchange.fetch.assert_awaited_once()
    assert urlsplit(exchange.fetch.call_args.args[0]).path == path


@pytest.mark.parametrize(
    "code,outcome",
    [
        (170193, "not_created"),
        (170131, "not_created"),
        (170146, "unknown"),
        (170141, "unknown"),
        (10006, "unknown"),
        (987654, "unknown"),
    ],
)
async def test_partial_rejection_is_not_transport_uncertainty(exchange, code, outcome):
    exchange.fetch.return_value = response(
        [row("cm-sell", ""), row("cm-buy", "buy-id")],
        [code, 0],
    )
    result = await submit_spot_batch(exchange, SYMBOL, "create", ORDERS, IDENTITY)
    assert result[0]["cm_outcome"] == "acknowledged"
    assert result[1]["cm_outcome"] == outcome
    assert result[1]["cm_diagnostic"]["error_code"] == code
    assert "fixture-secret" not in str(result)


@pytest.mark.parametrize(
    "rows,codes",
    [
        ([row("cm-buy", "b")], [0]),
        ([row("cm-buy", "b"), row("cm-buy", "other")], [0, 0]),
        ([row("cm-buy", "same"), row("cm-sell", "same")], [0, 0]),
        ([row("other-program", "foreign")], [0]),
        ([row("", "")], [170193]),
    ],
)
async def test_missing_duplicate_or_unattributed_results_do_not_prove_failure(
    exchange,
    rows,
    codes,
):
    exchange.fetch.return_value = response(rows, codes)
    result = await submit_spot_batch(exchange, SYMBOL, "create", ORDERS, IDENTITY)
    assert len(result) == 2
    assert result[1]["cm_outcome"] == "unknown"
    assert result[1]["clientOrderId"] == "cm-sell"
    assert all(r["cm_outcome"] != "not_created" for r in result)


async def test_cancel_item_codes_retained_ack_is_not_terminal(exchange):
    exchange.fetch.return_value = response(
        [row("cm-sell", "sell-id"), row("cm-buy", "buy-id")],
        [170213, "0"],
    )
    result = await submit_spot_batch(
        exchange,
        SYMBOL,
        "cancel",
        ["buy-id", "sell-id"],
        IDENTITY,
    )
    assert result[0]["cm_outcome"] == "cancel_pending"
    assert result[1]["cm_outcome"] == "unknown"
    assert result[1]["cm_diagnostic"]["error_code"] == 170213
    assert urlsplit(exchange.fetch.call_args.args[0]).path == "/v5/order/cancel-batch"
    exchange.fetch.assert_awaited_once()


async def test_native_support_does_not_invent_a_single_order_fallback(exchange):
    exchange.has["createOrder"] = False
    caps = spot_batch_capabilities(exchange, SYMBOL, IDENTITY)
    assert caps["create"]["native"] is True and caps["create"]["single"] is False
    assert caps["cancel"]["single"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"retCode": 0, "result": {"list": []}},
        {"retCode": 0, "result": {"list": [row("cm-buy", "buy-id")]}, "retExtInfo": {"list": []}},
    ],
)
async def test_unpaired_item_evidence_fails_unknown_not_not_created(exchange, payload):
    exchange.fetch.return_value = payload
    result = await submit_spot_batch(exchange, SYMBOL, "create", ORDERS, IDENTITY)
    assert len(result) == 2 and all(r["cm_outcome"] == "unknown" for r in result)
    if payload["result"]["list"]:
        assert result[0]["id"] == "buy-id" and result[0]["clientOrderId"] == "cm-buy"
    exchange.fetch.assert_awaited_once()


async def test_rejection_with_order_id_remains_queryable_not_proved_uncreated(exchange):
    exchange.fetch.return_value = response([row("cm-buy", "original-id")], [170193])
    result = await submit_spot_batch(exchange, SYMBOL, "create", ORDERS, IDENTITY)
    assert result[0]["cm_outcome"] == "unknown" and result[0]["id"] == "original-id"


async def test_wrong_symbol_does_not_poison_original_client_lookup_with_foreign_order_id(exchange):
    exchange.fetch.return_value = response(
        [{**row("cm-buy", "foreign-id"), "symbol": "ETHUSDT"}],
        [0],
    )
    result = await submit_spot_batch(exchange, SYMBOL, "create", ORDERS, IDENTITY)
    assert result[0]["cm_outcome"] == "unknown" and result[0]["id"] is None
    assert result[0]["clientOrderId"] == "cm-buy"


async def test_missing_cancel_response_keeps_each_original_order_identity(exchange):
    exchange.fetch.return_value = response([], [])
    result = await submit_spot_batch(exchange, SYMBOL, "cancel", ["one", "two"], IDENTITY)
    assert [r["id"] for r in result] == ["one", "two"]
    assert all(r["cm_outcome"] == "unknown" for r in result)


@pytest.mark.parametrize(
    "failure", [ccxt.RequestTimeout, ccxt.RateLimitExceeded, ccxt.PermissionDenied]
)
async def test_batch_transport_errors_never_switch_or_resend(exchange, failure):
    exchange.options["maxRetriesOnFailure"] = 3
    exchange.fetch.side_effect = failure("fixture-secret")
    with pytest.raises(failure):
        await submit_spot_batch(exchange, SYMBOL, "create", ORDERS, IDENTITY)
    exchange.fetch.assert_awaited_once()
    assert exchange.options["maxRetriesOnFailure"] == 3
    assert "maxRetriesOnFailure" not in exchange.fetch.call_args.args[3]


async def test_validate_limit_identities_and_all_requests_before_native_write(exchange):
    for items in (
        [],
        ORDERS * 6,
        [ORDERS[0], ORDERS[0]],
        [ORDERS[0], {**ORDERS[1], "symbol": "ETH/USDT"}],
    ):
        with pytest.raises(ValueError):
            await submit_spot_batch(exchange, SYMBOL, "create", items, IDENTITY)
    exchange.fetch.assert_not_awaited()


async def test_wallet_hook_preserves_real_source_without_changing_factory_or_global_class(exchange):
    original_class = type(exchange)
    preserve_bybit_wallet_time(exchange)
    wrapped = exchange.handle_balance
    preserve_bybit_wallet_time(exchange)
    assert exchange.handle_balance is wrapped and type(exchange) is original_class
    client = Mock()
    message = {
        "topic": "wallet",
        "creationTime": 1700000000123,
        "data": [{"accountType": "UNIFIED", "coin": []}],
    }
    exchange.handle_balance(client, message)
    actual, topic = client.resolve.call_args.args
    assert actual["timestamp"] == message["creationTime"] and topic == "balances:unified"
    assert "ts" not in message
    exchange.handle_balance(client, {"topic": "wallet", "data": message["data"]})
    assert client.resolve.call_args.args[0]["timestamp"] is None
