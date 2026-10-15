import pytest
from ccxt import RequestTimeout

import examples.accept_bifu_mock_order as mock_acceptance
from examples.accept_bifu_mock_order import accept_mock_order


class FakeMockExchange:
    def __init__(self, *, trades=None, open_orders=None, create_error=None, trade_error=None):
        self.id = "bifu"
        self.isSandboxModeEnabled = True
        sandbox_urls = {"public": "https://test", "private": "https://test"}
        self.urls = {"api": dict(sandbox_urls), "test": sandbox_urls}
        self.calls = []
        self.trades = trades
        self.open_orders = list(open_orders or [])
        self.create_error = create_error
        self.trade_error = trade_error
        self.closed = False

    def set_sandbox_mode(self, enabled):
        self.isSandboxModeEnabled = enabled
        self.calls.append(("set_sandbox_mode", enabled))

    async def create_mock_order(self, symbol, side, value, params):
        self.calls.append(("create_mock_order", symbol, side, value, params))
        if self.create_error is not None:
            raise self.create_error
        return {
            "id": "mock-order-1",
            "clientOrderId": params["clientOrderId"],
            "symbol": symbol,
            "type": "market",
            "side": side,
            "amount": None,
            "price": None,
            "cost": None,
            "status": None,
            "filled": None,
        }

    async def fetch_my_trades(self, symbol, params):
        self.calls.append(("fetch_my_trades", symbol, params))
        if self.trade_error is not None:
            raise self.trade_error
        if self.trades is not None:
            return self.trades
        return [
            {
                "id": "mock-trade-1",
                "order": "mock-order-1",
                "symbol": symbol,
                "price": 100.0,
                "amount": 0.05,
                "cost": 5.0,
            }
        ]

    async def fetch_open_orders(self, symbol):
        self.calls.append(("fetch_open_orders", symbol))
        return list(self.open_orders)

    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel_order", order_id, symbol))
        self.open_orders = [order for order in self.open_orders if order.get("id") != order_id]

    async def close(self):
        self.calls.append(("close",))
        self.closed = True


@pytest.mark.asyncio
async def test_mock_acceptance_requires_explicit_confirmation():
    exchange = FakeMockExchange()

    with pytest.raises(RuntimeError, match="BIFU_TEST_MOCK_WRITE"):
        await accept_mock_order(
            "BTC/USDT",
            "buy",
            5,
            confirmation="wrong",
            exchange=exchange,
        )

    assert exchange.calls == []


@pytest.mark.asyncio
async def test_mock_acceptance_proves_trade_and_no_open_order():
    exchange = FakeMockExchange()

    result = await accept_mock_order(
        "BTC/USDT",
        "buy",
        5,
        confirmation="BIFU_TEST_MOCK_WRITE",
        exchange=exchange,
        poll_attempts=1,
        poll_delay=0,
    )

    assert result == {
        "environment": "test",
        "symbol": "BTC/USDT",
        "side": "buy",
        "special_method": "create_mock_order",
        "bifu_order_type": "SANDBOX_MARKET",
        "requested_value": 5,
        "requested_value_unit": "quote",
        "create_ack_standard_fields_present": True,
        "create_status": None,
        "trade_count": 1,
        "trade_standard_fields_present": True,
        "order_absent_from_open_orders": True,
        "identifiers_redacted": True,
    }
    assert exchange.calls[0][:4] == ("create_mock_order", "BTC/USDT", "buy", 5)
    client_order_id = exchange.calls[0][4]["clientOrderId"]
    assert len(client_order_id) == 16
    int(client_order_id, 16)
    assert exchange.calls[1:] == [
        ("fetch_my_trades", "BTC/USDT", {"order_id": "mock-order-1"}),
        ("fetch_open_orders", "BTC/USDT"),
    ]


@pytest.mark.asyncio
async def test_mock_acceptance_does_not_retry_an_unknown_create_result():
    exchange = FakeMockExchange(create_error=RequestTimeout("unknown result"))

    with pytest.raises(RequestTimeout, match="unknown result"):
        await accept_mock_order(
            "BTC/USDT",
            "buy",
            5,
            confirmation="BIFU_TEST_MOCK_WRITE",
            exchange=exchange,
            poll_attempts=1,
            poll_delay=0,
        )

    create_calls = [call for call in exchange.calls if call[0] == "create_mock_order"]
    assert len(create_calls) == 1
    assert [call[0] for call in exchange.calls] == [
        "create_mock_order",
        "fetch_open_orders",
        "fetch_open_orders",
    ]


@pytest.mark.asyncio
async def test_mock_acceptance_cleans_up_an_unexpected_open_order():
    exchange = FakeMockExchange(
        open_orders=[
            {
                "id": "mock-order-1",
                "clientOrderId": "replaced-by-call-parameter",
            }
        ]
    )

    with pytest.raises(RuntimeError, match="unexpectedly entered the open order book"):
        await accept_mock_order(
            "BTC/USDT",
            "buy",
            5,
            confirmation="BIFU_TEST_MOCK_WRITE",
            exchange=exchange,
            poll_attempts=1,
            poll_delay=0,
        )

    assert ("cancel_order", "mock-order-1", "BTC/USDT") in exchange.calls
    assert exchange.open_orders == []


@pytest.mark.asyncio
async def test_mock_acceptance_cleans_up_when_trade_query_fails():
    exchange = FakeMockExchange(
        open_orders=[{"id": "mock-order-1", "clientOrderId": None}],
        trade_error=RuntimeError("trade query failed"),
    )

    with pytest.raises(RuntimeError, match="trade query failed"):
        await accept_mock_order(
            "BTC/USDT",
            "buy",
            5,
            confirmation="BIFU_TEST_MOCK_WRITE",
            exchange=exchange,
            poll_attempts=1,
            poll_delay=0,
        )

    assert ("cancel_order", "mock-order-1", "BTC/USDT") in exchange.calls
    assert exchange.open_orders == []


@pytest.mark.asyncio
async def test_mock_acceptance_closes_an_exchange_it_created(monkeypatch):
    exchange = FakeMockExchange()
    monkeypatch.setattr(
        mock_acceptance,
        "prepare_sandbox_exchange",
        lambda _exchange=None, **kwargs: (exchange, True),
    )

    await accept_mock_order(
        "BTC/USDT",
        "buy",
        5,
        confirmation="BIFU_TEST_MOCK_WRITE",
        poll_attempts=1,
        poll_delay=0,
    )

    assert exchange.closed is True
