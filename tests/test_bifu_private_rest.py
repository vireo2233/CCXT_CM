import asyncio
import hashlib
import hmac
from types import SimpleNamespace

import pytest
from aiohttp import web
from ccxt import (
    ArgumentsRequired,
    AuthenticationError,
    BadRequest,
    BadResponse,
    InvalidOrder,
    NotSupported,
    OrderNotFound,
    PermissionDenied,
    RequestTimeout,
)

from ccxt_cm import create_exchange, special_methods
from examples.inspect_bifu_orders import inspect_orders


async def test_order_inspection_keeps_unknown_and_known_statuses():
    base_order = {
        "id": "[REDACTED]",
        "symbol": "BTC/USDT",
        "type": "limit",
        "side": "buy",
        "amount": 1.0,
        "filled": 0.0,
    }

    class FakeExchange:
        async def fetch_open_orders(self, symbol):
            return [
                dict(base_order, status=None),
                dict(base_order, status="open"),
            ]

        async def fetch_closed_orders(self, symbol, limit):
            return []

        async def fetch_my_trades(self, symbol, limit):
            return []

        async def fetch_order(self, order_id, symbol):
            return dict(base_order, status="open")

    result = await inspect_orders(exchange=FakeExchange(), retry_delay=0)

    assert result["open_statuses"] == [None, "open"]


@pytest.fixture
async def bifu_private_server(unused_tcp_port):
    state = SimpleNamespace(
        calls=[],
        cancel_all_orders_response={"canceled": 2},
        cancel_orders_response={"accepted": True, "count": 2},
        cancel_order_response={"accepted": True},
        create_orders_response={
            "acks": [
                {
                    "order_id": "batch-order-1",
                    "client_order_id": "fixture-batch-1",
                    "status": "PENDING",
                    "reject_code": "",
                },
                {
                    "order_id": "batch-order-2",
                    "client_order_id": "fixture-batch-2",
                    "status": "PENDING",
                    "reject_code": "",
                },
            ]
        },
        create_order_response={"order_id": "created-order-456"},
        edit_order_response={"order_id": "order-123"},
        edit_orders_response={
            "acks": [
                {"order_id": "order-1", "accepted": True, "reject_code": ""},
                {
                    "order_id": "order-2",
                    "accepted": False,
                    "reject_code": "ORDER_NOT_SETTLED",
                },
            ]
        },
        delays={},
        request_bodies=[],
        forced_errors={},
        fund_flows_response={
            "flows": [
                {
                    "kind": "CREDIT",
                    "ticket": "flow-in-1",
                    "product": "SPOT",
                    "margin_scope": 0,
                    "asset": 2,
                    "amount": "100.25",
                    "from_product": "FUNDING",
                    "from_scope": 0,
                    "to_product": "SPOT",
                    "to_scope": 0,
                    "period": "",
                    "ts_ms": "1700000000100",
                },
                {
                    "kind": "TRANSFER",
                    "ticket": "flow-out-1",
                    "product": "SPOT",
                    "margin_scope": 0,
                    "asset": 2,
                    "amount": "-5.5",
                    "from_product": "SPOT",
                    "from_scope": 0,
                    "to_product": "FUNDING",
                    "to_scope": 0,
                    "period": "",
                    "ts_ms": "1700000000200",
                },
            ],
            "next_cursor": "fund-page-2",
        },
        history_orders_response=None,
        my_trades_response=None,
        open_orders_response=None,
        order_error=None,
        order={
            "order_id": "order-123",
            "client_order_id": "client-123",
            "account_id": "[REDACTED]",
            "instrument_id": 90000001,
            "side": "BUY",
            "type": "LIMIT",
            "time_in_force": "POST_ONLY",
            "price": "100.10",
            "orig_qty": "0.2",
            "quote_qty": "0",
            "display_qty": "0",
            "status": "PARTIALLY_FILLED",
            "filled_qty": "0.05",
            "cum_quote": "5.005",
            "cancel_reason": "",
            "created_ts": "1700000000000",
            "updated_ts": "1700000000500",
            "upper_trigger_price": "0",
            "lower_trigger_price": "0",
            "upper_order_price": "0",
            "lower_order_price": "0",
            "origin": "USER",
        },
    )

    async def endpoint(request):
        body = await request.text()
        state.request_bodies.append((request.method, request.path, body))
        timestamp = request.headers.get("X-TS", "")
        payload = "\n".join((timestamp, request.method, request.path, body))
        expected = hmac.new(b"fixture-secret", payload.encode(), hashlib.sha256).hexdigest()
        authenticated = request.headers.get("X-API-KEY") == "fixture-key" and hmac.compare_digest(
            request.headers.get("X-SIGN", ""), expected
        )
        state.calls.append((request.method, request.path, dict(request.query), authenticated))
        delay = state.delays.get(request.path)
        if delay:
            await asyncio.sleep(delay)
        if not authenticated:
            return web.json_response({"code": 4002}, status=401)
        forced_error = state.forced_errors.get(request.path)
        if forced_error:
            return web.json_response(forced_error, status=403)
        if request.path == "/spot/v1/account":
            return web.json_response(
                {
                    "balances": [
                        {"asset_id": 1, "available": "0.5", "frozen": "0.1"},
                        {"asset_id": 2, "available": "100", "frozen": "5"},
                        {"asset_id": 999, "available": "3", "frozen": "0"},
                    ]
                }
            )
        if request.path == "/spot/v1/order" and request.method == "POST":
            return web.json_response(state.create_order_response)
        if request.path == "/spot/v1/order/amend" and request.method == "POST":
            return web.json_response(state.edit_order_response)
        if request.path == "/spot/v1/orders/amend" and request.method == "POST":
            return web.json_response(state.edit_orders_response)
        if request.path == "/spot/v1/orders" and request.method == "POST":
            return web.json_response(state.create_orders_response)
        if request.path == "/spot/v1/order/cancel" and request.method == "POST":
            return web.json_response(state.cancel_order_response)
        if request.path == "/spot/v1/orders/cancel" and request.method == "POST":
            return web.json_response(state.cancel_orders_response)
        if request.path == "/spot/v1/openOrders/cancel" and request.method == "POST":
            return web.json_response(state.cancel_all_orders_response)
        if request.path == "/spot/v1/order/query":
            if state.order_error:
                return web.json_response(state.order_error, status=404)
            return web.json_response(state.order)
        if request.path == "/spot/v1/openOrders":
            response = state.open_orders_response
            return web.json_response(
                response if response is not None else {"orders": [state.order]}
            )
        if request.path == "/spot/v1/historyOrders":
            response = state.history_orders_response
            closed_order = dict(
                state.order,
                status="FILLED",
                filled_qty="0.2",
                cum_quote="20.02",
            )
            return web.json_response(
                response
                if response is not None
                else {"orders": [closed_order], "next_cursor": "next-page"}
            )
        if request.path == "/spot/v1/myTrades":
            response = state.my_trades_response
            return web.json_response(
                response
                if response is not None
                else {
                    "trades": [
                        {
                            "order_id": "order-123",
                            "instrument_id": 90000001,
                            "side": "BUY",
                            "price": "100.10",
                            "qty": "0.05",
                            "fee": "0.00005",
                            "fee_asset_id": 1,
                            "is_maker": True,
                            "product": "SPOT",
                            "ts_ms": "1700000000250",
                            "trade_id": "trade-123",
                        }
                    ],
                    "next_cursor": "next-page",
                }
            )
        if request.path == "/spot/v1/fundFlows":
            return web.json_response(state.fund_flows_response)
        return web.json_response({"code": 1000}, status=404)

    async def meta(request):
        state.calls.append((request.method, request.path, dict(request.query), None))
        return web.json_response(
            {
                "assets": [
                    {"asset_id": 1, "code": "BTC"},
                    {"asset_id": 2, "code": "USDT"},
                ],
                "spots": [
                    {
                        "instrument_id": 90000001,
                        "base_asset_id": 1,
                        "quote_asset_id": 2,
                        "status": "TRADING",
                        "rules": {
                            "price_tick": "0.01",
                            "qty_step": "0.00001",
                            "min_qty": "0.00001",
                            "max_qty": "9000",
                            "min_notional": "5",
                            "max_notional": "1000000",
                        },
                    }
                ],
            }
        )

    app = web.Application()
    app.router.add_get("/market/v1/meta", meta)
    app.router.add_route("*", "/spot/{path:.*}", endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    state.url = f"http://127.0.0.1:{unused_tcp_port}"
    try:
        yield state
    finally:
        await runner.cleanup()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_balance_authenticates_and_returns_ccxt_balance(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        balance = await exchange.fetch_balance()
    finally:
        await exchange.close()

    assert balance["BTC"] == {"free": 0.5, "used": 0.1, "total": 0.6}
    assert balance["USDT"] == {"free": 100.0, "used": 5.0, "total": 105.0}
    assert balance["999"] == {"free": 3.0, "used": 0.0, "total": 3.0}
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        ("GET", "/spot/v1/account", {}, True),
    ]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_balance_rejects_unsupported_params_before_request(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(NotSupported, match="fetch_balance does not accept params"):
            await exchange.fetch_balance({"accountType": "spot"})
    finally:
        await exchange.close()

    assert bifu_private_server.calls == []


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_limit_order_sends_bifu_request_and_returns_ccxt_ack(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.create_order(
            "BTC/USDT",
            "limit",
            "buy",
            0.123456,
            100.129,
            {"clientOrderId": "fixture-create-1"},
        )
    finally:
        await exchange.close()

    assert order["id"] == "created-order-456"
    assert order["clientOrderId"] == "fixture-create-1"
    assert order["symbol"] == "BTC/USDT"
    assert order["type"] == "limit"
    assert order["side"] == "buy"
    assert order["timeInForce"] == "GTC"
    assert order["price"] == 100.13
    assert order["amount"] == 0.12345
    assert order["status"] is None
    assert order["filled"] is None
    assert order["remaining"] is None
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        ("POST", "/spot/v1/order", {}, True),
    ]
    assert bifu_private_server.request_bodies[-1] == (
        "POST",
        "/spot/v1/order",
        '{"client_order_id":"fixture-create-1","instrument_id":90000001,'
        '"price":"100.13","qty":"0.12345","side":"BUY",'
        '"time_in_force":"GTC","type":"LIMIT"}',
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_orders_sends_one_bifu_batch_and_preserves_ack_order(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        orders = await exchange.create_orders(
            [
                {
                    "symbol": "BTC/USDT",
                    "type": "limit",
                    "side": "buy",
                    "amount": 0.123456,
                    "price": 100.129,
                    "params": {
                        "clientOrderId": "fixture-batch-1",
                        "postOnly": True,
                    },
                },
                {
                    "symbol": "BTC/USDT",
                    "type": "limit",
                    "side": "sell",
                    "amount": 0.2,
                    "price": 101,
                    "params": {"clientOrderId": "fixture-batch-2"},
                },
            ]
        )
    finally:
        await exchange.close()

    assert [order["id"] for order in orders] == ["batch-order-1", "batch-order-2"]
    assert [order["clientOrderId"] for order in orders] == [
        "fixture-batch-1",
        "fixture-batch-2",
    ]
    assert [order["side"] for order in orders] == ["buy", "sell"]
    assert [order["status"] for order in orders] == ["open", "open"]
    assert exchange.has["createOrders"] is True
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        ("POST", "/spot/v1/orders", {}, True),
    ]
    assert bifu_private_server.request_bodies[-1] == (
        "POST",
        "/spot/v1/orders",
        '{"entries":[{"client_order_id":"fixture-batch-1","price":"100.13",'
        '"qty":"0.12345","side":"BUY","time_in_force":"POST_ONLY",'
        '"type":"LIMIT"},{"client_order_id":"fixture-batch-2","price":"101",'
        '"qty":"0.2","side":"SELL","time_in_force":"GTC","type":"LIMIT"}],'
        '"instrument_id":90000001}',
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_orders_preserves_per_item_rejection(bifu_private_server):
    bifu_private_server.create_orders_response = {
        "acks": [
            {
                "order_id": "batch-order-1",
                "client_order_id": "fixture-batch-ok",
                "status": "PENDING",
                "reject_code": "",
            },
            {
                "order_id": "",
                "client_order_id": "fixture-batch-rejected",
                "status": "REJECTED",
                "reject_code": "MIN_NOTIONAL",
            },
        ]
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        orders = await exchange.create_orders(
            [
                {
                    "symbol": "BTC/USDT",
                    "type": "limit",
                    "side": "buy",
                    "amount": 0.1,
                    "price": 100,
                    "params": {"clientOrderId": "fixture-batch-ok"},
                },
                {
                    "symbol": "BTC/USDT",
                    "type": "limit",
                    "side": "buy",
                    "amount": 0.1,
                    "price": 100,
                    "params": {"clientOrderId": "fixture-batch-rejected"},
                },
            ]
        )
    finally:
        await exchange.close()

    assert orders[0]["status"] == "open"
    assert orders[1]["id"] is None
    assert orders[1]["status"] == "rejected"
    assert orders[1]["info"]["reject_code"] == "MIN_NOTIONAL"


@pytest.mark.parametrize("orders", [[], [{}] * 101])
async def test_create_orders_rejects_invalid_batch_size_before_network(orders):
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(ArgumentsRequired, match="between 1 and 100"):
            await exchange.create_orders(orders)
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_orders_rejects_ack_count_mismatch(bifu_private_server):
    bifu_private_server.create_orders_response = {"acks": []}
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(BadResponse, match="ACK count"):
            await exchange.create_orders(
                [
                    {
                        "symbol": "BTC/USDT",
                        "type": "limit",
                        "side": "buy",
                        "amount": 0.1,
                        "price": 100,
                    }
                ]
            )
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(
    ("ack", "message"),
    [
        (
            {
                "order_id": "batch-order-1",
                "client_order_id": "wrong-client-id",
                "status": "PENDING",
                "reject_code": "",
            },
            "client_order_id",
        ),
        (
            {
                "order_id": "batch-order-1",
                "client_order_id": "fixture-invalid-ack",
                "reject_code": "",
            },
            "missing status",
        ),
        (
            {
                "order_id": "",
                "client_order_id": "fixture-invalid-ack",
                "status": "REJECTED",
                "reject_code": "",
            },
            "missing reject_code",
        ),
        (
            {
                "order_id": "",
                "client_order_id": "fixture-invalid-ack",
                "status": "PENDING",
                "reject_code": "",
            },
            "missing order_id",
        ),
    ],
)
async def test_create_orders_rejects_invalid_ack_entry(bifu_private_server, ack, message):
    bifu_private_server.create_orders_response = {"acks": [ack]}
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(BadResponse, match=message):
            await exchange.create_orders(
                [
                    {
                        "symbol": "BTC/USDT",
                        "type": "limit",
                        "side": "buy",
                        "amount": 0.1,
                        "price": 100,
                        "params": {"clientOrderId": "fixture-invalid-ack"},
                    }
                ]
            )
    finally:
        await exchange.close()


async def test_create_orders_rejects_mixed_symbols_before_network():
    exchange = create_exchange("bifu", mode="async")
    orders = [
        {"symbol": "BTC/USDT", "type": "limit", "side": "buy", "amount": 1, "price": 5},
        {"symbol": "ETH/USDT", "type": "limit", "side": "buy", "amount": 1, "price": 5},
    ]
    try:
        with pytest.raises(NotSupported, match="one symbol"):
            await exchange.create_orders(orders)
    finally:
        await exchange.close()


async def test_create_orders_rejects_non_object_item_params_before_network():
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(InvalidOrder, match="params must be an object"):
            await exchange.create_orders(
                [
                    {
                        "symbol": "BTC/USDT",
                        "type": "limit",
                        "side": "buy",
                        "amount": 1,
                        "price": 5,
                        "params": ["postOnly"],
                    }
                ]
            )
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_orders_preserves_unknown_ack_status(bifu_private_server):
    bifu_private_server.create_orders_response = {
        "acks": [
            {
                "order_id": "",
                "client_order_id": "fixture-unknown-ack",
                "status": "AWAITING_RISK_REVIEW",
                "reject_code": "",
            }
        ]
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        orders = await exchange.create_orders(
            [
                {
                    "symbol": "BTC/USDT",
                    "type": "limit",
                    "side": "buy",
                    "amount": 0.1,
                    "price": 100,
                    "params": {"clientOrderId": "fixture-unknown-ack"},
                }
            ]
        )
    finally:
        await exchange.close()

    assert orders[0]["id"] is None
    assert orders[0]["status"] is None
    assert orders[0]["info"]["status"] == "AWAITING_RISK_REVIEW"


async def test_create_orders_rejects_unsupported_common_params_before_network():
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(NotSupported, match="does not accept params"):
            await exchange.create_orders([], {"foo": "bar"})
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_orders_maps_bifu_business_error(bifu_private_server):
    bifu_private_server.forced_errors["/spot/v1/orders"] = {
        "code": 4001,
        "message": "permission denied",
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(PermissionDenied, match="4001"):
            await exchange.create_orders(
                [
                    {
                        "symbol": "BTC/USDT",
                        "type": "limit",
                        "side": "buy",
                        "amount": 0.1,
                        "price": 100,
                    }
                ]
            )
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_orders_timeout_sends_exactly_one_post(bifu_private_server):
    bifu_private_server.delays["/spot/v1/orders"] = 0.1
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        await exchange.load_markets()
        exchange.timeout = 10
        with pytest.raises(RequestTimeout):
            await exchange.create_orders(
                [
                    {
                        "symbol": "BTC/USDT",
                        "type": "limit",
                        "side": "buy",
                        "amount": 0.1,
                        "price": 100,
                    }
                ]
            )
    finally:
        await exchange.close()

    posts = [call for call in bifu_private_server.calls if call[0:2] == ("POST", "/spot/v1/orders")]
    assert len(posts) == 1


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_limit_order_maps_post_only_to_bifu_time_in_force(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.create_order(
            "BTC/USDT",
            "limit",
            "sell",
            0.1,
            100,
            {"clientOrderId": "fixture-post-only", "postOnly": True},
        )
    finally:
        await exchange.close()

    assert order["timeInForce"] == "PO"
    assert order["postOnly"] is True
    assert '"side":"SELL"' in bifu_private_server.request_bodies[-1][2]
    assert '"time_in_force":"POST_ONLY"' in bifu_private_server.request_bodies[-1][2]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_limit_order_accepts_ccxt_time_in_force(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.create_order(
            "BTC/USDT",
            "limit",
            "buy",
            0.1,
            100,
            {"clientOrderId": "fixture-fok", "timeInForce": "FOK"},
        )
    finally:
        await exchange.close()

    assert order["timeInForce"] == "FOK"
    assert '"time_in_force":"FOK"' in bifu_private_server.request_bodies[-1][2]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_limit_order_rejects_cost_below_market_minimum_before_post(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(InvalidOrder, match="minimum cost"):
            await exchange.create_order(
                "BTC/USDT",
                "limit",
                "buy",
                0.01,
                100,
                {"clientOrderId": "fixture-too-small"},
            )
    finally:
        await exchange.close()

    assert bifu_private_server.calls == [("GET", "/market/v1/meta", {}, None)]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_limit_order_accepts_cost_equal_to_market_minimum(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.create_order(
            "BTC/USDT",
            "limit",
            "buy",
            "0.05",
            "100",
            {"clientOrderId": "fixture-exact-minimum"},
        )
    finally:
        await exchange.close()

    assert order["id"] == "created-order-456"
    assert '"price":"100"' in bifu_private_server.request_bodies[-1][2]
    assert '"qty":"0.05"' in bifu_private_server.request_bodies[-1][2]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_market_sell_sends_base_quantity_and_ioc(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.create_order(
            "BTC/USDT",
            "market",
            "sell",
            0.123456,
            params={"clientOrderId": "fixture-market-sell"},
        )
    finally:
        await exchange.close()

    assert order["id"] == "created-order-456"
    assert order["clientOrderId"] == "fixture-market-sell"
    assert order["symbol"] == "BTC/USDT"
    assert order["type"] == "market"
    assert order["side"] == "sell"
    assert order["timeInForce"] == "IOC"
    assert order["price"] is None
    assert order["amount"] == 0.12345
    assert order["status"] is None
    assert bifu_private_server.request_bodies[-1] == (
        "POST",
        "/spot/v1/order",
        '{"client_order_id":"fixture-market-sell","instrument_id":90000001,'
        '"qty":"0.12345","side":"SELL","time_in_force":"IOC",'
        '"type":"MARKET"}',
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_market_buy_converts_amount_and_price_to_quote_budget(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.create_order(
            "BTC/USDT",
            "market",
            "buy",
            0.123456,
            100.129,
            {"clientOrderId": "fixture-market-buy"},
        )
    finally:
        await exchange.close()

    assert order["id"] == "created-order-456"
    assert order["clientOrderId"] == "fixture-market-buy"
    assert order["symbol"] == "BTC/USDT"
    assert order["type"] == "market"
    assert order["side"] == "buy"
    assert order["timeInForce"] == "IOC"
    assert order["price"] is None
    assert order["amount"] is None
    assert order["cost"] is None
    assert order["status"] is None
    assert bifu_private_server.request_bodies[-1] == (
        "POST",
        "/spot/v1/order",
        '{"client_order_id":"fixture-market-buy","instrument_id":90000001,'
        '"quote_qty":"12.36","side":"BUY","time_in_force":"IOC",'
        '"type":"MARKET"}',
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_market_buy_accepts_explicit_quote_cost(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.create_order(
            "BTC/USDT",
            "market",
            "buy",
            0.1,
            params={"clientOrderId": "fixture-market-cost", "cost": "5.019"},
        )
    finally:
        await exchange.close()

    assert order["type"] == "market"
    assert order["side"] == "buy"
    assert order["amount"] is None
    assert '"quote_qty":"5.01"' in bifu_private_server.request_bodies[-1][2]
    assert '"qty"' not in bifu_private_server.request_bodies[-1][2]
    assert '"price"' not in bifu_private_server.request_bodies[-1][2]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_market_buy_order_with_cost_uses_standard_ccxt_helper(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        assert exchange.has["createMarketOrder"] is False
        assert exchange.has["createMarketBuyOrder"] is False
        assert exchange.has["createMarketBuyOrderWithCost"] is True
        assert exchange.has["createMarketSellOrder"] is True
        order = await exchange.create_market_buy_order_with_cost(
            "BTC/USDT", "5.019", {"clientOrderId": "fixture-cost-helper"}
        )
    finally:
        await exchange.close()

    assert order["type"] == "market"
    assert order["side"] == "buy"
    assert '"quote_qty":"5.01"' in bifu_private_server.request_bodies[-1][2]


async def test_bifu_declares_mock_as_a_private_mutating_special_method():
    exchange = create_exchange("bifu", mode="async")
    try:
        assert special_methods(exchange) == [
            {
                "method": "create_mock_order",
                "description": "Create one Bifu sandbox-only simulated market order",
                "documentation": "docs/learning/12-bifu-mock.md",
                "private": True,
                "mutating": True,
                "returns": "CCXT Order with raw Bifu acknowledgement in info",
            }
        ]
    finally:
        await exchange.close()


async def test_create_mock_order_rejects_non_sandbox_instance_before_network():
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    try:
        with pytest.raises(PermissionDenied, match="require sandbox mode"):
            await exchange.create_mock_order("BTC/USDT", "buy", 5)
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(
    ("side", "value", "expected_quantity"),
    [
        ("buy", "5.019", '"quote_qty":"5.01"'),
        ("sell", "0.123456", '"qty":"0.12345"'),
    ],
)
async def test_create_mock_order_uses_sandbox_market_without_standard_create_order(
    bifu_private_server, side, value, expected_quantity
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.create_mock_order(
            "BTC/USDT",
            side,
            value,
            {"clientOrderId": f"fixture-mock-{side}"},
        )
    finally:
        await exchange.close()

    assert order["id"] == "created-order-456"
    assert order["symbol"] == "BTC/USDT"
    assert order["type"] == "market"
    assert order["side"] == side
    assert order["timeInForce"] == "IOC"
    request_body = bifu_private_server.request_bodies[-1][2]
    assert '"type":"SANDBOX_MARKET"' in request_body
    assert expected_quantity in request_body
    assert ('"qty"' in request_body) is (side == "sell")
    assert ('"quote_qty"' in request_body) is (side == "buy")


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_mock_order_supports_an_optional_protection_price(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        await exchange.create_mock_order(
            "BTC/USDT",
            "buy",
            "5.019",
            {"clientOrderId": "fixture-mock-price", "price": "100.129"},
        )
    finally:
        await exchange.close()

    assert '"price":"100.13"' in bifu_private_server.request_bodies[-1][2]


@pytest.mark.parametrize(
    ("side", "value", "params", "message"),
    [
        ("hold", 5, {}, "side must be buy or sell"),
        ("buy", 0, {}, "value must be a positive finite value"),
        ("buy", 5, {"price": 0}, "price must be a positive finite value"),
        ("buy", 5, {"unsupported": True}, "does not accept params"),
    ],
)
async def test_create_mock_order_rejects_invalid_input_before_network(side, value, params, message):
    exchange = create_exchange("bifu", mode="async")
    exchange.set_sandbox_mode(True)
    try:
        with pytest.raises((InvalidOrder, NotSupported), match=message):
            await exchange.create_mock_order("BTC/USDT", side, value, params)
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_mock_order_rejects_value_below_market_minimum_before_post(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(InvalidOrder, match="minimum value"):
            await exchange.create_mock_order("BTC/USDT", "buy", "4.99")
    finally:
        await exchange.close()

    assert bifu_private_server.calls == [("GET", "/market/v1/meta", {}, None)]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_mock_order_rejects_ack_without_order_id(bifu_private_server):
    bifu_private_server.create_order_response = {}
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(BadResponse, match="missing order_id"):
            await exchange.create_mock_order("BTC/USDT", "buy", "5")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_mock_order_never_treats_ack_status_as_final(bifu_private_server):
    bifu_private_server.create_order_response = {
        "order_id": "created-order-456",
        "status": "FILLED",
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.create_mock_order("BTC/USDT", "buy", "5")
    finally:
        await exchange.close()

    assert order["status"] is None
    assert order["info"]["status"] == "FILLED"


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_mock_order_timeout_sends_exactly_one_post(bifu_private_server):
    bifu_private_server.delays["/spot/v1/order"] = 0.1
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        await exchange.load_markets()
        exchange.timeout = 10
        with pytest.raises(RequestTimeout):
            await exchange.create_mock_order(
                "BTC/USDT",
                "buy",
                "5",
                {"clientOrderId": "fixture-mock-timeout"},
            )
    finally:
        await exchange.close()

    order_posts = [
        call for call in bifu_private_server.calls if call[0:2] == ("POST", "/spot/v1/order")
    ]
    assert len(order_posts) == 1
    assert '"type":"SANDBOX_MARKET"' in bifu_private_server.request_bodies[-1][2]


@pytest.mark.parametrize("order_type", ["mock", "sandbox_market", "SANDBOX_MARKET"])
async def test_standard_create_order_rejects_bifu_mock_types(order_type):
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(NotSupported, match="limit and market orders only"):
            await exchange.create_order("BTC/USDT", order_type, "buy", 5)
    finally:
        await exchange.close()


@pytest.mark.parametrize(
    ("side", "amount", "price", "params", "message"),
    [
        ("buy", 0.1, None, {}, "require a price or cost"),
        ("buy", 0.1, None, {"cost": 0}, "cost must be"),
        ("sell", 0.1, None, {"cost": 5}, "only supported for market buy"),
    ],
)
async def test_create_market_order_rejects_invalid_budget_before_network(
    side, amount, price, params, message
):
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(InvalidOrder, match=message):
            await exchange.create_order("BTC/USDT", "market", side, amount, price, params)
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"postOnly": True}, "do not support postOnly"),
        ({"timeInForce": "GTC"}, "must be IOC"),
    ],
)
async def test_create_market_order_rejects_limit_only_options_before_post(
    bifu_private_server, params, message
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(InvalidOrder, match=message):
            await exchange.create_order("BTC/USDT", "market", "buy", 0.1, 100, params)
    finally:
        await exchange.close()

    assert bifu_private_server.calls == [("GET", "/market/v1/meta", {}, None)]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_market_buy_rejects_cost_below_minimum_before_post(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(InvalidOrder, match="minimum cost"):
            await exchange.create_order("BTC/USDT", "market", "buy", 0.1, params={"cost": "4.99"})
    finally:
        await exchange.close()

    assert bifu_private_server.calls == [("GET", "/market/v1/meta", {}, None)]


@pytest.mark.parametrize(
    ("order_type", "side", "amount", "price"),
    [
        (None, "buy", 0.1, 100),
        ("limit", None, 0.1, 100),
        ("limit", "buy", "not-a-number", 100),
        ("limit", "buy", 0.1, "not-a-number"),
    ],
)
async def test_create_order_maps_invalid_input_to_ccxt_invalid_order(
    order_type, side, amount, price
):
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(InvalidOrder):
            await exchange.create_order("BTC/USDT", order_type, side, amount, price)
    finally:
        await exchange.close()


async def test_create_order_rejects_unsupported_params_before_network():
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(NotSupported, match="unexpected"):
            await exchange.create_order("BTC/USDT", "limit", "buy", 0.1, 100, {"unexpected": True})
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_order_maps_bifu_permission_denied(bifu_private_server):
    bifu_private_server.forced_errors["/spot/v1/order"] = {
        "code": 4001,
        "message": "permission denied",
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(PermissionDenied, match="4001"):
            await exchange.create_order("BTC/USDT", "limit", "buy", 0.1, 100)
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_order_rejects_ack_without_order_id(bifu_private_server):
    bifu_private_server.create_order_response = {}
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(BadResponse, match="missing order_id"):
            await exchange.create_order("BTC/USDT", "limit", "buy", 0.1, 100)
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_create_order_timeout_sends_exactly_one_post(bifu_private_server):
    bifu_private_server.delays["/spot/v1/order"] = 0.1
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        await exchange.load_markets()
        exchange.timeout = 10
        with pytest.raises(RequestTimeout):
            await exchange.create_order("BTC/USDT", "limit", "buy", 0.1, 100)
    finally:
        await exchange.close()

    order_posts = [
        call for call in bifu_private_server.calls if call[0:2] == ("POST", "/spot/v1/order")
    ]
    assert len(order_posts) == 1


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_cancel_order_sends_bifu_request_and_returns_ccxt_ack(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.cancel_order("order-123", "BTC/USDT")
    finally:
        await exchange.close()

    assert order["id"] == "order-123"
    assert order["symbol"] == "BTC/USDT"
    assert order["status"] is None
    assert order["info"] == {"accepted": True}
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        ("POST", "/spot/v1/order/cancel", {}, True),
    ]
    assert bifu_private_server.request_bodies[-1] == (
        "POST",
        "/spot/v1/order/cancel",
        '{"instrument_id":90000001,"order_id":"order-123"}',
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_cancel_orders_sends_one_bifu_batch_and_returns_ccxt_ack_list(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        orders = await exchange.cancel_orders(["batch-order-1", "batch-order-2"], "BTC/USDT")
    finally:
        await exchange.close()

    assert [order["id"] for order in orders] == ["batch-order-1", "batch-order-2"]
    assert [order["symbol"] for order in orders] == ["BTC/USDT", "BTC/USDT"]
    assert [order["status"] for order in orders] == [None, None]
    assert all(order["info"] == {"accepted": True, "count": 2} for order in orders)
    assert exchange.has["cancelOrders"] is True
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        ("POST", "/spot/v1/orders/cancel", {}, True),
    ]
    assert bifu_private_server.request_bodies[-1] == (
        "POST",
        "/spot/v1/orders/cancel",
        '{"instrument_id":90000001,"order_ids":["batch-order-1","batch-order-2"]}',
    )


@pytest.mark.parametrize(
    ("ids", "symbol", "message"),
    [
        ([], "BTC/USDT", "between 1 and 100"),
        (["order"] * 101, "BTC/USDT", "between 1 and 100"),
        (["order"], None, "requires a symbol"),
    ],
)
async def test_cancel_orders_rejects_invalid_scope_before_network(ids, symbol, message):
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(ArgumentsRequired, match=message):
            await exchange.cancel_orders(ids, symbol)
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(
    "response",
    [
        {"accepted": False, "count": 2},
        {"accepted": True, "count": 1},
        {"accepted": True, "count": -1},
        {},
    ],
)
async def test_cancel_orders_rejects_invalid_batch_ack(bifu_private_server, response):
    bifu_private_server.cancel_orders_response = response
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(BadResponse, match="cancel orders response"):
            await exchange.cancel_orders(["batch-order-1", "batch-order-2"], "BTC/USDT")
    finally:
        await exchange.close()


async def test_cancel_orders_rejects_unsupported_params_before_network():
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(NotSupported, match="does not accept params"):
            await exchange.cancel_orders(["batch-order-1"], "BTC/USDT", {"foo": "bar"})
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_cancel_orders_maps_bifu_business_error(bifu_private_server):
    bifu_private_server.forced_errors["/spot/v1/orders/cancel"] = {
        "code": 4001,
        "message": "permission denied",
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(PermissionDenied, match="4001"):
            await exchange.cancel_orders(["batch-order-1"], "BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_cancel_orders_timeout_sends_exactly_one_post(bifu_private_server):
    bifu_private_server.delays["/spot/v1/orders/cancel"] = 0.1
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        await exchange.load_markets()
        exchange.timeout = 10
        with pytest.raises(RequestTimeout):
            await exchange.cancel_orders(["batch-order-1"], "BTC/USDT")
    finally:
        await exchange.close()

    posts = [
        call
        for call in bifu_private_server.calls
        if call[0:2] == ("POST", "/spot/v1/orders/cancel")
    ]
    assert len(posts) == 1


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_order_uses_native_bifu_amend_and_returns_unknown_status_ack(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.edit_order(
            "order-123",
            "BTC/USDT",
            "limit",
            "buy",
            0.2,
            101.129,
        )
    finally:
        await exchange.close()

    assert order["id"] == "order-123"
    assert order["symbol"] == "BTC/USDT"
    assert order["type"] == "limit"
    assert order["side"] == "buy"
    assert order["amount"] == 0.2
    assert order["price"] == 101.13
    assert order["status"] is None
    assert order["info"] == {"order_id": "order-123"}
    assert exchange.has["editOrder"] is True
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        ("POST", "/spot/v1/order/amend", {}, True),
    ]
    assert bifu_private_server.request_bodies[-1] == (
        "POST",
        "/spot/v1/order/amend",
        '{"instrument_id":90000001,"order_id":"order-123","price":"101.13","qty":"0.2"}',
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_order_maps_bifu_trigger_amend_fields(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.edit_order(
            "order-123",
            "BTC/USDT",
            "trigger",
            "sell",
            params={
                "upper_trigger_price": 82000.129,
                "lower_trigger_price": 0,
                "upper_order_price": 81900.129,
                "lower_order_price": 0,
            },
        )
    finally:
        await exchange.close()

    assert order["id"] == "order-123"
    assert order["type"] == "trigger"
    assert order["side"] == "sell"
    assert order["status"] is None
    assert bifu_private_server.request_bodies[-1] == (
        "POST",
        "/spot/v1/order/amend",
        '{"instrument_id":90000001,"lower_order_price":"0",'
        '"lower_trigger_price":"0","order_id":"order-123",'
        '"upper_order_price":"81900.13","upper_trigger_price":"82000.13"}',
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_orders_uses_one_native_batch_and_preserves_per_item_result(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        orders = await exchange.edit_orders(
            [
                {
                    "id": "order-1",
                    "symbol": "BTC/USDT",
                    "type": "limit",
                    "side": "buy",
                    "amount": 0.2,
                    "price": 101.129,
                },
                {
                    "id": "order-2",
                    "symbol": "BTC/USDT",
                    "type": "limit",
                    "side": "sell",
                    "price": 102.129,
                },
            ]
        )
    finally:
        await exchange.close()

    assert [order["id"] for order in orders] == ["order-1", "order-2"]
    assert [order["status"] for order in orders] == [None, None]
    assert [order["info"]["accepted"] for order in orders] == [True, False]
    assert orders[1]["info"]["reject_code"] == "ORDER_NOT_SETTLED"
    assert orders[1]["amount"] is None
    assert orders[1]["price"] is None
    assert exchange.has["editOrders"] is True
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        ("POST", "/spot/v1/orders/amend", {}, True),
    ]
    assert bifu_private_server.request_bodies[-1] == (
        "POST",
        "/spot/v1/orders/amend",
        '{"entries":[{"order_id":"order-1","price":"101.13","qty":"0.2"},'
        '{"order_id":"order-2","price":"102.13"}],"instrument_id":90000001}',
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_order_rejects_amount_below_market_minimum_before_post(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(InvalidOrder, match="minimum amount"):
            await exchange.edit_order(
                "order-123",
                "BTC/USDT",
                "limit",
                "buy",
                amount=0.000001,
            )
    finally:
        await exchange.close()

    amend_posts = [
        call for call in bifu_private_server.calls if call[0:2] == ("POST", "/spot/v1/order/amend")
    ]
    assert amend_posts == []


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_order_rejects_amount_above_market_maximum_before_post(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(InvalidOrder, match="maximum amount"):
            await exchange.edit_order(
                "order-123",
                "BTC/USDT",
                "limit",
                "buy",
                amount=9001,
            )
    finally:
        await exchange.close()

    assert not any(
        call[0:2] == ("POST", "/spot/v1/order/amend") for call in bifu_private_server.calls
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_order_checks_known_price_and_cost_limits_before_post(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        await exchange.load_markets()
        exchange.markets["BTC/USDT"]["limits"]["price"] = {"min": 1, "max": 1000}
        with pytest.raises(InvalidOrder, match="maximum price"):
            await exchange.edit_order("order-123", "BTC/USDT", "limit", "buy", price=1001)
        with pytest.raises(InvalidOrder, match="minimum cost"):
            await exchange.edit_order(
                "order-123",
                "BTC/USDT",
                "limit",
                "buy",
                amount=0.00001,
                price=100,
            )
    finally:
        await exchange.close()

    assert not any(
        call[0:2] == ("POST", "/spot/v1/order/amend") for call in bifu_private_server.calls
    )


@pytest.mark.parametrize(
    ("order_id", "order_type", "side", "amount", "price", "params", "error", "message"),
    [
        ("", "limit", "buy", 0.2, None, {}, ArgumentsRequired, "order id"),
        ("order-123", "market", "buy", 0.2, None, {}, NotSupported, "limit and trigger"),
        ("order-123", "limit", "hold", 0.2, None, {}, InvalidOrder, "buy or sell"),
        ("order-123", "limit", "buy", None, None, {}, ArgumentsRequired, "requires an amount"),
        ("order-123", "limit", "buy", 0.2, None, {"foo": "bar"}, NotSupported, "foo"),
    ],
)
async def test_edit_order_rejects_invalid_input_before_network(
    order_id,
    order_type,
    side,
    amount,
    price,
    params,
    error,
    message,
):
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(error, match=message):
            await exchange.edit_order(
                order_id,
                "BTC/USDT",
                order_type,
                side,
                amount,
                price,
                params,
            )
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_order_rejects_negative_trigger_price_before_post(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(InvalidOrder, match="non-negative"):
            await exchange.edit_order(
                "order-123",
                "BTC/USDT",
                "trigger",
                "sell",
                params={"upper_trigger_price": -1},
            )
    finally:
        await exchange.close()

    assert not any(
        call[0:2] == ("POST", "/spot/v1/order/amend") for call in bifu_private_server.calls
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_order_rejects_nonfinite_trigger_price_before_post(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(InvalidOrder, match="non-negative"):
            await exchange.edit_order(
                "order-123",
                "BTC/USDT",
                "trigger",
                "sell",
                params={"upper_trigger_price": float("nan")},
            )
    finally:
        await exchange.close()

    assert not any(
        call[0:2] == ("POST", "/spot/v1/order/amend") for call in bifu_private_server.calls
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_order_rejects_mismatched_ack(bifu_private_server):
    bifu_private_server.edit_order_response = {"order_id": "wrong-order"}
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(BadResponse, match="does not match"):
            await exchange.edit_order("order-123", "BTC/USDT", "limit", "buy", price=101)
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_order_maps_bifu_business_error(bifu_private_server):
    bifu_private_server.forced_errors["/spot/v1/order/amend"] = {
        "code": 4001,
        "message": "permission denied",
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(PermissionDenied, match="4001"):
            await exchange.edit_order("order-123", "BTC/USDT", "limit", "buy", price=101)
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_order_timeout_sends_exactly_one_post(bifu_private_server):
    bifu_private_server.delays["/spot/v1/order/amend"] = 0.1
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        await exchange.load_markets()
        exchange.timeout = 10
        with pytest.raises(RequestTimeout):
            await exchange.edit_order("order-123", "BTC/USDT", "limit", "buy", price=101)
    finally:
        await exchange.close()

    posts = [
        call for call in bifu_private_server.calls if call[0:2] == ("POST", "/spot/v1/order/amend")
    ]
    assert len(posts) == 1


@pytest.mark.parametrize("orders", [[], [{}] * 101])
async def test_edit_orders_rejects_invalid_batch_size_before_network(orders):
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(ArgumentsRequired, match="between 1 and 100"):
            await exchange.edit_orders(orders)
    finally:
        await exchange.close()


async def test_edit_orders_rejects_mixed_symbols_before_network():
    exchange = create_exchange("bifu", mode="async")
    orders = [
        {
            "id": "order-1",
            "symbol": "BTC/USDT",
            "type": "limit",
            "side": "buy",
            "price": 100,
        },
        {
            "id": "order-2",
            "symbol": "ETH/USDT",
            "type": "limit",
            "side": "buy",
            "price": 100,
        },
    ]
    try:
        with pytest.raises(NotSupported, match="one symbol"):
            await exchange.edit_orders(orders)
    finally:
        await exchange.close()


async def test_edit_orders_rejects_non_object_params_before_network():
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(InvalidOrder, match="params must be an object"):
            await exchange.edit_orders(
                [
                    {
                        "id": "order-1",
                        "symbol": "BTC/USDT",
                        "type": "limit",
                        "side": "buy",
                        "price": 100,
                        "params": ["upper_trigger_price"],
                    }
                ]
            )
    finally:
        await exchange.close()


async def test_edit_orders_rejects_unsupported_common_params_before_network():
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(NotSupported, match="foo"):
            await exchange.edit_orders([], {"foo": "bar"})
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(
    ("acks", "message"),
    [
        ([], "ACK count"),
        (
            [{"order_id": "wrong-order", "accepted": True, "reject_code": ""}],
            "order_id",
        ),
        (
            [{"order_id": "order-1", "accepted": False, "reject_code": ""}],
            "missing reject_code",
        ),
    ],
)
async def test_edit_orders_rejects_invalid_ack(bifu_private_server, acks, message):
    bifu_private_server.edit_orders_response = {"acks": acks}
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(BadResponse, match=message):
            await exchange.edit_orders(
                [
                    {
                        "id": "order-1",
                        "symbol": "BTC/USDT",
                        "type": "limit",
                        "side": "buy",
                        "price": 101,
                    }
                ]
            )
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_orders_preserves_unknown_per_item_result(bifu_private_server):
    bifu_private_server.edit_orders_response = {"acks": [{"order_id": "order-1"}]}
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        orders = await exchange.edit_orders(
            [
                {
                    "id": "order-1",
                    "symbol": "BTC/USDT",
                    "type": "limit",
                    "side": "buy",
                    "price": 101,
                }
            ]
        )
    finally:
        await exchange.close()

    assert orders[0]["status"] is None
    assert orders[0]["info"] == {"order_id": "order-1"}
    assert orders[0]["amount"] is None
    assert orders[0]["price"] is None


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_orders_maps_bifu_business_error(bifu_private_server):
    bifu_private_server.forced_errors["/spot/v1/orders/amend"] = {
        "code": 4001,
        "message": "permission denied",
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(PermissionDenied, match="4001"):
            await exchange.edit_orders(
                [
                    {
                        "id": "order-1",
                        "symbol": "BTC/USDT",
                        "type": "limit",
                        "side": "buy",
                        "price": 101,
                    }
                ]
            )
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_edit_orders_timeout_sends_exactly_one_post(bifu_private_server):
    bifu_private_server.delays["/spot/v1/orders/amend"] = 0.1
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        await exchange.load_markets()
        exchange.timeout = 10
        with pytest.raises(RequestTimeout):
            await exchange.edit_orders(
                [
                    {
                        "id": "order-1",
                        "symbol": "BTC/USDT",
                        "type": "limit",
                        "side": "buy",
                        "price": 101,
                    }
                ]
            )
    finally:
        await exchange.close()

    posts = [
        call for call in bifu_private_server.calls if call[0:2] == ("POST", "/spot/v1/orders/amend")
    ]
    assert len(posts) == 1


async def test_cancel_order_requires_symbol_before_network():
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(ArgumentsRequired, match="requires a symbol"):
            await exchange.cancel_order("order-123")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_cancel_all_orders_sends_market_scope_and_returns_ccxt_ack_list(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        orders = await exchange.cancel_all_orders("BTC/USDT")
    finally:
        await exchange.close()

    assert exchange.has["cancelAllOrders"] is True
    assert len(orders) == 1
    assert orders[0]["symbol"] == "BTC/USDT"
    assert orders[0]["status"] is None
    assert orders[0]["info"] == {"canceled": 2}
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        ("POST", "/spot/v1/openOrders/cancel", {}, True),
    ]
    assert bifu_private_server.request_bodies[-1] == (
        "POST",
        "/spot/v1/openOrders/cancel",
        '{"instrument_id":90000001}',
    )


async def test_cancel_all_orders_requires_symbol_before_network():
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(ArgumentsRequired, match="requires a symbol"):
            await exchange.cancel_all_orders()
    finally:
        await exchange.close()


async def test_cancel_all_orders_rejects_unsupported_params_before_network():
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(NotSupported, match="unexpected"):
            await exchange.cancel_all_orders("BTC/USDT", {"unexpected": True})
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize("response", [{}, {"canceled": -1}, {"canceled": "invalid"}])
async def test_cancel_all_orders_rejects_invalid_ack(bifu_private_server, response):
    bifu_private_server.cancel_all_orders_response = response
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(BadResponse, match="invalid canceled count"):
            await exchange.cancel_all_orders("BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_cancel_all_orders_maps_bifu_permission_failure(bifu_private_server):
    bifu_private_server.forced_errors["/spot/v1/openOrders/cancel"] = {
        "code": 4001,
        "message": "permission denied",
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(PermissionDenied, match="4001"):
            await exchange.cancel_all_orders("BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_cancel_all_orders_timeout_sends_exactly_one_post(bifu_private_server):
    bifu_private_server.delays["/spot/v1/openOrders/cancel"] = 0.1
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        await exchange.load_markets()
        exchange.timeout = 10
        with pytest.raises(RequestTimeout):
            await exchange.cancel_all_orders("BTC/USDT")
    finally:
        await exchange.close()

    posts = [
        call
        for call in bifu_private_server.calls
        if call[0:2] == ("POST", "/spot/v1/openOrders/cancel")
    ]
    assert len(posts) == 1


async def test_cancel_order_rejects_unsupported_params_before_network():
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(NotSupported, match="unexpected"):
            await exchange.cancel_order("order-123", "BTC/USDT", {"unexpected": True})
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_cancel_order_maps_bifu_order_not_found(bifu_private_server):
    bifu_private_server.forced_errors["/spot/v1/order/cancel"] = {
        "code": 3000,
        "message": "order not found",
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(OrderNotFound, match="order not found"):
            await exchange.cancel_order("missing-order", "BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_cancel_order_rejects_unaccepted_ack(bifu_private_server):
    bifu_private_server.cancel_order_response = {"accepted": False}
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(BadResponse, match="not accepted"):
            await exchange.cancel_order("order-123", "BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_balance_maps_authentication_failure(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "wrong-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(AuthenticationError, match="4002"):
            await exchange.fetch_balance()
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_order_returns_a_standard_ccxt_order(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.fetch_order("order-123", "BTC/USDT")
    finally:
        await exchange.close()

    assert order["id"] == "order-123"
    assert order["clientOrderId"] == "client-123"
    assert order["symbol"] == "BTC/USDT"
    assert order["type"] == "limit"
    assert order["side"] == "buy"
    assert order["timeInForce"] == "PO"
    assert order["postOnly"] is True
    assert order["status"] == "open"
    assert order["price"] == 100.1
    assert order["amount"] == 0.2
    assert order["filled"] == 0.05
    assert order["remaining"] == 0.15
    assert order["cost"] == 5.005
    assert order["average"] == 100.1
    assert order["timestamp"] == 1700000000000
    assert order["lastUpdateTimestamp"] == 1700000000500
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        ("GET", "/spot/v1/order/query", {"order_id": "order-123"}, True),
    ]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_open_orders_returns_standard_orders_for_one_symbol(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        orders = await exchange.fetch_open_orders("BTC/USDT")
    finally:
        await exchange.close()

    assert len(orders) == 1
    assert orders[0]["id"] == "order-123"
    assert orders[0]["status"] == "open"
    assert orders[0]["symbol"] == "BTC/USDT"
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        ("GET", "/spot/v1/openOrders", {"instrument_id": "90000001"}, True),
    ]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_open_orders_accepts_the_live_empty_object_shape(bifu_private_server):
    bifu_private_server.open_orders_response = {}
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        assert await exchange.fetch_open_orders("BTC/USDT") == []
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_order_maps_bifu_order_not_found(bifu_private_server):
    bifu_private_server.order_error = {"code": 3000, "message": "order not found"}
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(OrderNotFound, match="order not found"):
            await exchange.fetch_order("missing-order", "BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_order_rejects_a_mismatched_response(bifu_private_server):
    bifu_private_server.order["order_id"] = "different-order"
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(BadResponse, match="order id does not match"):
            await exchange.fetch_order("order-123", "BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_order_keeps_unknown_status_unknown(bifu_private_server):
    bifu_private_server.order["status"] = "PAUSED_BY_OPERATOR"
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.fetch_order("order-123", "BTC/USDT")
    finally:
        await exchange.close()

    assert order["status"] is None


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_order_handles_quote_amount_market_buy_and_fee(bifu_private_server):
    bifu_private_server.order.update(
        {
            "type": "MARKET",
            "time_in_force": "IOC",
            "orig_qty": "0",
            "quote_qty": "25",
            "status": "FILLED",
            "filled_qty": "0.249",
            "cum_quote": "24.9249",
            "cum_fee": "0.000249",
            "fee_asset_id": 1,
        }
    )
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        order = await exchange.fetch_order("order-123", "BTC/USDT")
    finally:
        await exchange.close()

    assert order["type"] == "market"
    assert order["status"] == "closed"
    assert order["amount"] is None
    assert order["filled"] == 0.249
    assert order["remaining"] is None
    assert order["cost"] == 24.9249
    assert order["fee"] == {"cost": 0.000249, "currency": "BTC"}


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(
    ("method_name", "endpoint"),
    [
        ("fetch_open_orders", "/spot/v1/openOrders"),
        ("fetch_closed_orders", "/spot/v1/historyOrders"),
        ("fetch_my_trades", "/spot/v1/myTrades"),
    ],
)
async def test_private_order_lists_map_bifu_permission_denied(
    bifu_private_server, method_name, endpoint
):
    bifu_private_server.forced_errors[endpoint] = {
        "code": 4001,
        "message": "permission denied",
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(PermissionDenied, match="4001"):
            await getattr(exchange, method_name)("BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(
    "method_name",
    ["fetch_order", "fetch_open_orders", "fetch_closed_orders", "fetch_my_trades"],
)
async def test_private_order_methods_reject_unsupported_params_before_request(
    bifu_private_server, method_name
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(NotSupported, match="does not accept params"):
            if method_name == "fetch_order":
                await exchange.fetch_order("order-123", "BTC/USDT", {"unsupported": True})
            else:
                await getattr(exchange, method_name)("BTC/USDT", params={"unsupported": True})
    finally:
        await exchange.close()

    assert bifu_private_server.calls == []


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_closed_orders_uses_server_filters_and_returns_standard_orders(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        orders = await exchange.fetch_closed_orders(
            "BTC/USDT",
            since=1699999999000,
            limit=10,
            params={"cursor": "page-1"},
        )
    finally:
        await exchange.close()

    assert len(orders) == 1
    assert orders[0]["id"] == "order-123"
    assert orders[0]["status"] == "closed"
    assert orders[0]["filled"] == 0.2
    assert orders[0]["remaining"] == 0.0
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        (
            "GET",
            "/spot/v1/historyOrders",
            {
                "cursor": "page-1",
                "instrument_id": "90000001",
                "limit": "10",
                "start_ts_ms": "1699999999000",
            },
            True,
        ),
    ]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_my_trades_returns_standard_private_trades(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        trades = await exchange.fetch_my_trades(
            "BTC/USDT",
            since=1699999999000,
            limit=10,
            params={"cursor": "page-1"},
        )
    finally:
        await exchange.close()

    assert len(trades) == 1
    assert trades[0]["id"] == "trade-123"
    assert trades[0]["order"] == "order-123"
    assert trades[0]["symbol"] == "BTC/USDT"
    assert trades[0]["side"] == "buy"
    assert trades[0]["takerOrMaker"] == "maker"
    assert trades[0]["price"] == 100.1
    assert trades[0]["amount"] == 0.05
    assert trades[0]["cost"] == 5.005
    assert trades[0]["fee"] == {"cost": 0.00005, "currency": "BTC"}
    assert trades[0]["timestamp"] == 1700000000250
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        (
            "GET",
            "/spot/v1/myTrades",
            {
                "cursor": "page-1",
                "instrument_id": "90000001",
                "limit": "10",
                "start_ts_ms": "1699999999000",
            },
            True,
        ),
    ]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ledger_returns_ccxt_entries_and_preserves_cursor(
    bifu_private_server,
):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        entries = await exchange.fetch_ledger(
            since=1699999999000,
            limit=10,
            params={"cursor": "fund-page-1", "until": 1700000010000},
        )
        next_cursor = exchange.safe_string(exchange.last_json_response, "next_cursor")
    finally:
        await exchange.close()

    assert exchange.has["fetchLedger"] is True
    assert len(entries) == 2
    assert entries[0] == {
        "id": "flow-in-1",
        "timestamp": 1700000000100,
        "datetime": "2023-11-14T22:13:20.100Z",
        "direction": "in",
        "account": "SPOT:0",
        "referenceId": None,
        "referenceAccount": None,
        "type": "deposit",
        "currency": "USDT",
        "amount": 100.25,
        "before": None,
        "after": None,
        "status": None,
        "fee": None,
        "info": bifu_private_server.fund_flows_response["flows"][0],
    }
    assert entries[1]["direction"] == "out"
    assert entries[1]["type"] == "transfer"
    assert entries[1]["amount"] == 5.5
    assert entries[1]["account"] == "SPOT:0"
    assert entries[1]["referenceAccount"] == "FUNDING:0"
    assert next_cursor == "fund-page-2"
    assert bifu_private_server.calls == [
        ("GET", "/market/v1/meta", {}, None),
        (
            "GET",
            "/spot/v1/fundFlows",
            {
                "cursor": "fund-page-1",
                "end_ts_ms": "1700000010000",
                "limit": "10",
                "start_ts_ms": "1699999999000",
            },
            True,
        ),
    ]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ledger_filters_currency_after_parsing_page(bifu_private_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        entries = await exchange.fetch_ledger("BTC", limit=5)
    finally:
        await exchange.close()

    assert entries == []
    assert bifu_private_server.calls[-1][2] == {"limit": "5"}


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize("response", [{}, {"flows": "invalid"}, {"flows": [None]}])
async def test_fetch_ledger_handles_empty_and_rejects_malformed_responses(
    bifu_private_server, response
):
    bifu_private_server.fund_flows_response = response
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        if response == {}:
            assert await exchange.fetch_ledger() == []
        else:
            with pytest.raises(BadResponse, match="fund flows"):
                await exchange.fetch_ledger()
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ledger_rejects_invalid_entry(bifu_private_server):
    bifu_private_server.fund_flows_response = {
        "flows": [{"ticket": "missing-required-fields"}],
        "next_cursor": "",
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(BadResponse, match="invalid fund flow entry"):
            await exchange.fetch_ledger()
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ledger_preserves_unknown_kind_asset_and_zero_direction(
    bifu_private_server,
):
    bifu_private_server.fund_flows_response = {
        "flows": [
            {
                "kind": "BONUS",
                "ticket": "flow-unknown",
                "product": "SPOT",
                "margin_scope": 0,
                "asset": 999,
                "amount": "0",
                "ts_ms": "1700000000300",
            }
        ],
        "next_cursor": "",
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        entries = await exchange.fetch_ledger()
    finally:
        await exchange.close()

    assert entries[0]["type"] == "bonus"
    assert entries[0]["currency"] == "999"
    assert entries[0]["direction"] is None
    assert entries[0]["amount"] == 0.0


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize("amount", ["not-a-number", "nan", "inf"])
async def test_fetch_ledger_rejects_invalid_amount(bifu_private_server, amount):
    flow = dict(bifu_private_server.fund_flows_response["flows"][0], amount=amount)
    bifu_private_server.fund_flows_response = {"flows": [flow], "next_cursor": ""}
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(BadResponse, match="invalid fund flow entry"):
            await exchange.fetch_ledger()
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ledger_maps_permission_denied(bifu_private_server):
    bifu_private_server.forced_errors["/spot/v1/fundFlows"] = {
        "code": 4001,
        "message": "permission denied",
    }
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_private_server.url
    exchange.urls["api"]["private"] = bifu_private_server.url
    try:
        with pytest.raises(PermissionDenied, match="4001"):
            await exchange.fetch_ledger()
    finally:
        await exchange.close()


@pytest.mark.parametrize("limit", [0, 1001, 1.5])
async def test_fetch_ledger_rejects_invalid_limit_before_network(limit):
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(BadRequest, match="integer between 1 and 1000"):
            await exchange.fetch_ledger(limit=limit)
    finally:
        await exchange.close()


async def test_fetch_ledger_rejects_unsupported_or_conflicting_params_before_network():
    exchange = create_exchange("bifu", mode="async")
    try:
        with pytest.raises(NotSupported, match="does not accept params"):
            await exchange.fetch_ledger(params={"unsupported": True})
        with pytest.raises(BadRequest, match="both until and end_ts_ms"):
            await exchange.fetch_ledger(params={"until": 2, "end_ts_ms": 2})
    finally:
        await exchange.close()
