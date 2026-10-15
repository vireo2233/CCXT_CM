import pytest
from ccxt import RequestTimeout

from examples.accept_bifu_market_order import accept_market_order


class FakeExchange:
    def __init__(self):
        self.id = "bifu"
        self.isSandboxModeEnabled = True
        sandbox_urls = {"public": "https://test", "private": "https://test"}
        self.urls = {"api": dict(sandbox_urls), "test": sandbox_urls}
        self.calls = []

    async def load_markets(self):
        self.calls.append(("load_markets",))
        return {"BTC/USDT": {"symbol": "BTC/USDT"}}

    async def create_market_buy_order_with_cost(self, symbol, cost, params):
        self.calls.append(("create_market_buy_order_with_cost", symbol, cost, params))
        return {
            "id": "secret-order-id",
            "clientOrderId": "secret-client-id",
            "symbol": symbol,
            "type": "market",
            "side": "buy",
            "amount": None,
            "price": None,
            "cost": None,
            "status": None,
            "filled": None,
        }

    async def create_order(self, symbol, type, side, amount, price, params):
        self.calls.append(("create_order", symbol, type, side, amount, price, params))
        return {
            "id": "secret-order-id",
            "clientOrderId": "secret-client-id",
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": amount,
            "price": None,
            "cost": None,
            "status": None,
            "filled": None,
        }

    async def fetch_order(self, order_id, symbol):
        self.calls.append(("fetch_order", order_id, symbol))
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "closed",
            "filled": 0.0001,
            "cost": 5.5,
        }

    async def fetch_my_trades(self, symbol, params):
        self.calls.append(("fetch_my_trades", symbol, params))
        return [
            {
                "id": "secret-trade-id",
                "order": "secret-order-id",
                "symbol": symbol,
                "price": 55000,
                "amount": 0.0001,
                "cost": 5.5,
            }
        ]

    async def fetch_open_orders(self, symbol):
        self.calls.append(("fetch_open_orders", symbol))
        return []

    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel_order", order_id, symbol))
        return {"id": order_id}


class FakePreflightTimesOutTwice(FakeExchange):
    def __init__(self):
        super().__init__()
        self.load_markets_count = 0

    async def load_markets(self):
        self.calls.append(("load_markets",))
        self.load_markets_count += 1
        if self.load_markets_count < 3:
            raise RequestTimeout("fixture public metadata timeout")
        return {"BTC/USDT": {"symbol": "BTC/USDT"}}


class FakePreflightAlwaysTimesOut(FakeExchange):
    def __init__(self):
        super().__init__()
        self.load_markets_count = 0

    async def load_markets(self):
        self.calls.append(("load_markets",))
        self.load_markets_count += 1
        raise RequestTimeout("fixture public metadata timeout")


class FakeQueryTimeoutWithUnexpectedOpen(FakeExchange):
    def __init__(self, remains_open=False):
        super().__init__()
        self.remains_open = remains_open
        self.canceled = False

    async def fetch_order(self, order_id, symbol):
        self.calls.append(("fetch_order", order_id, symbol))
        raise RequestTimeout("fixture order query timeout")

    async def fetch_open_orders(self, symbol):
        self.calls.append(("fetch_open_orders", symbol))
        if not self.canceled or self.remains_open:
            return [{"id": "secret-order-id", "symbol": symbol}]
        return []

    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel_order", order_id, symbol))
        self.canceled = True
        return {"id": order_id}


class FakeNoTrades(FakeExchange):
    async def fetch_my_trades(self, symbol, params):
        self.calls.append(("fetch_my_trades", symbol, params))
        return []


async def test_market_buy_acceptance_uses_ccxt_cost_helper_and_redacts_ids():
    exchange = FakeExchange()

    result = await accept_market_order(
        "BTC/USDT",
        "buy",
        5.5,
        confirmation="BIFU_TEST_MARKET_WRITE",
        exchange=exchange,
        poll_delay=0,
    )

    create_params = exchange.calls[1][3]
    assert len(create_params["clientOrderId"]) == 16
    int(create_params["clientOrderId"], 16)
    assert exchange.calls == [
        ("load_markets",),
        ("create_market_buy_order_with_cost", "BTC/USDT", 5.5, create_params),
        ("fetch_order", "secret-order-id", "BTC/USDT"),
        ("fetch_my_trades", "BTC/USDT", {"order_id": "secret-order-id"}),
        ("fetch_open_orders", "BTC/USDT"),
    ]
    assert result == {
        "environment": "test",
        "symbol": "BTC/USDT",
        "side": "buy",
        "order_type": "market",
        "requested_value": 5.5,
        "requested_value_unit": "quote",
        "create_ack_standard_fields_present": True,
        "create_status": None,
        "query_after_create_found": True,
        "query_after_create_status": "closed",
        "trade_count": 1,
        "trade_standard_fields_present": True,
        "order_absent_from_open_orders": True,
        "unexpected_open_cleanup_attempted": False,
        "identifiers_redacted": True,
    }
    assert "secret-order-id" not in repr(result)
    assert "secret-client-id" not in repr(result)
    assert "secret-trade-id" not in repr(result)


async def test_market_sell_acceptance_uses_base_amount():
    exchange = FakeExchange()

    result = await accept_market_order(
        "BTC/USDT",
        "sell",
        0.0001,
        confirmation="BIFU_TEST_MARKET_WRITE",
        exchange=exchange,
        poll_delay=0,
    )

    assert exchange.calls[1] == (
        "create_order",
        "BTC/USDT",
        "market",
        "sell",
        0.0001,
        None,
        exchange.calls[1][6],
    )
    assert len(exchange.calls[1][6]["clientOrderId"]) == 16
    assert result["requested_value_unit"] == "base"


async def test_market_order_acceptance_requires_exact_confirmation():
    exchange = FakeExchange()

    with pytest.raises(RuntimeError, match="BIFU_TEST_MARKET_WRITE"):
        await accept_market_order(
            "BTC/USDT",
            "buy",
            5.5,
            confirmation="yes",
            exchange=exchange,
        )

    assert exchange.calls == []


@pytest.mark.parametrize("value", [0, -1, "nan", "inf"])
async def test_market_order_acceptance_rejects_invalid_value_before_network(value):
    exchange = FakeExchange()

    with pytest.raises(ValueError, match="positive finite"):
        await accept_market_order(
            "BTC/USDT",
            "buy",
            value,
            confirmation="BIFU_TEST_MARKET_WRITE",
            exchange=exchange,
        )

    assert exchange.calls == []


async def test_market_order_acceptance_retries_only_read_only_preflight():
    exchange = FakePreflightTimesOutTwice()

    result = await accept_market_order(
        "BTC/USDT",
        "buy",
        5.5,
        confirmation="BIFU_TEST_MARKET_WRITE",
        exchange=exchange,
        poll_delay=0,
    )

    assert exchange.load_markets_count == 3
    assert sum(call[0] == "create_market_buy_order_with_cost" for call in exchange.calls) == 1
    assert result["query_after_create_status"] == "closed"


async def test_market_order_acceptance_stops_before_write_after_preflight_failures():
    exchange = FakePreflightAlwaysTimesOut()

    with pytest.raises(RequestTimeout, match="metadata timeout"):
        await accept_market_order(
            "BTC/USDT",
            "buy",
            5.5,
            confirmation="BIFU_TEST_MARKET_WRITE",
            exchange=exchange,
        )

    assert exchange.load_markets_count == 3
    assert sum(call[0] == "create_market_buy_order_with_cost" for call in exchange.calls) == 0


async def test_market_order_acceptance_cleans_up_if_query_fails_after_create():
    exchange = FakeQueryTimeoutWithUnexpectedOpen()

    with pytest.raises(RequestTimeout, match="order query timeout"):
        await accept_market_order(
            "BTC/USDT",
            "buy",
            5.5,
            confirmation="BIFU_TEST_MARKET_WRITE",
            exchange=exchange,
            poll_attempts=1,
            poll_delay=0,
        )

    assert ("cancel_order", "secret-order-id", "BTC/USDT") in exchange.calls
    assert exchange.calls[-1] == ("fetch_open_orders", "BTC/USDT")


async def test_market_order_acceptance_surfaces_failed_cleanup():
    exchange = FakeQueryTimeoutWithUnexpectedOpen(remains_open=True)

    with pytest.raises(RuntimeError, match="may remain open"):
        await accept_market_order(
            "BTC/USDT",
            "buy",
            5.5,
            confirmation="BIFU_TEST_MARKET_WRITE",
            exchange=exchange,
            poll_attempts=1,
            poll_delay=0,
        )

    assert ("cancel_order", "secret-order-id", "BTC/USDT") in exchange.calls


async def test_market_order_acceptance_fails_without_matching_trade_evidence():
    exchange = FakeNoTrades()

    with pytest.raises(RuntimeError, match="no matching trade evidence"):
        await accept_market_order(
            "BTC/USDT",
            "buy",
            5.5,
            confirmation="BIFU_TEST_MARKET_WRITE",
            exchange=exchange,
            poll_attempts=1,
            poll_delay=0,
        )

    assert exchange.calls[-1] == ("fetch_open_orders", "BTC/USDT")
