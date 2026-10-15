import pytest
from ccxt import OrderNotFound, RequestTimeout

from examples.accept_bifu_order_lifecycle import accept_order_lifecycle


class FakeExchange:
    def __init__(self):
        self.id = "bifu"
        self.isSandboxModeEnabled = True
        sandbox_urls = {"public": "https://test", "private": "https://test"}
        self.urls = {"api": dict(sandbox_urls), "test": sandbox_urls}
        self.calls = []
        self.fetch_count = 0

    async def load_markets(self):
        self.calls.append(("load_markets",))
        return {"BTC/USDT": {"symbol": "BTC/USDT"}}

    async def create_order(self, symbol, type, side, amount, price, params):
        self.calls.append(("create_order", symbol, type, side, amount, price, params))
        return {
            "id": "secret-order-id",
            "clientOrderId": "secret-client-id",
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": amount,
            "price": price,
            "status": None,
            "filled": None,
        }

    async def fetch_order(self, order_id, symbol):
        self.calls.append(("fetch_order", order_id, symbol))
        self.fetch_count += 1
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "open" if self.fetch_count == 1 else "canceled",
        }

    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel_order", order_id, symbol))
        return {"id": order_id, "symbol": symbol, "status": None}

    async def fetch_open_orders(self, symbol):
        self.calls.append(("fetch_open_orders", symbol))
        return []


class FakeOrderDisappearsAfterCancel(FakeExchange):
    async def fetch_order(self, order_id, symbol):
        self.calls.append(("fetch_order", order_id, symbol))
        self.fetch_count += 1
        if self.fetch_count == 1:
            return {"id": order_id, "symbol": symbol, "status": "open"}
        raise OrderNotFound("fixture order no longer queryable")


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


async def test_order_lifecycle_creates_queries_cancels_and_redacts_ids():
    exchange = FakeExchange()

    result = await accept_order_lifecycle(
        "BTC/USDT",
        "buy",
        0.1,
        100,
        confirmation="BIFU_TEST_WRITE",
        exchange=exchange,
        poll_delay=0,
    )

    create_params = exchange.calls[1][6]
    assert create_params["postOnly"] is True
    assert len(create_params["clientOrderId"]) == 16
    int(create_params["clientOrderId"], 16)
    assert exchange.calls == [
        ("load_markets",),
        (
            "create_order",
            "BTC/USDT",
            "limit",
            "buy",
            0.1,
            100,
            create_params,
        ),
        ("fetch_order", "secret-order-id", "BTC/USDT"),
        ("cancel_order", "secret-order-id", "BTC/USDT"),
        ("fetch_order", "secret-order-id", "BTC/USDT"),
        ("fetch_open_orders", "BTC/USDT"),
    ]
    assert result == {
        "environment": "test",
        "symbol": "BTC/USDT",
        "side": "buy",
        "order_type": "limit",
        "post_only": True,
        "create_ack_standard_fields_present": True,
        "create_status": None,
        "query_after_create_status": "open",
        "cancel_command_accepted": True,
        "query_after_cancel_found": True,
        "query_after_cancel_status": "canceled",
        "order_absent_from_open_orders": True,
        "identifiers_redacted": True,
    }
    assert "secret-order-id" not in repr(result)
    assert "secret-client-id" not in repr(result)


async def test_order_lifecycle_requires_exact_test_write_confirmation():
    exchange = FakeExchange()

    with pytest.raises(RuntimeError, match="BIFU_TEST_WRITE"):
        await accept_order_lifecycle(
            "BTC/USDT",
            "buy",
            0.1,
            100,
            confirmation="yes",
            exchange=exchange,
            poll_delay=0,
        )

    assert exchange.calls == []


async def test_order_lifecycle_accepts_not_found_after_cancel_when_not_open():
    exchange = FakeOrderDisappearsAfterCancel()

    result = await accept_order_lifecycle(
        "BTC/USDT",
        "buy",
        0.1,
        100,
        confirmation="BIFU_TEST_WRITE",
        exchange=exchange,
        poll_attempts=1,
        poll_delay=0,
    )

    assert result["query_after_cancel_found"] is False
    assert result["query_after_cancel_status"] is None
    assert result["order_absent_from_open_orders"] is True
    assert exchange.calls[-1] == ("fetch_open_orders", "BTC/USDT")


async def test_order_lifecycle_retries_read_only_preflight_but_creates_once():
    exchange = FakePreflightTimesOutTwice()

    result = await accept_order_lifecycle(
        "BTC/USDT",
        "buy",
        0.1,
        100,
        confirmation="BIFU_TEST_WRITE",
        exchange=exchange,
        poll_delay=0,
    )

    assert exchange.load_markets_count == 3
    assert sum(call[0] == "create_order" for call in exchange.calls) == 1
    assert result["order_absent_from_open_orders"] is True


async def test_order_lifecycle_does_not_expose_a_preflight_bypass():
    exchange = FakeExchange()

    with pytest.raises(TypeError, match="preflight_attempts"):
        await accept_order_lifecycle(
            "BTC/USDT",
            "buy",
            0.1,
            100,
            confirmation="BIFU_TEST_WRITE",
            exchange=exchange,
            preflight_attempts=0,
        )

    assert exchange.calls == []


async def test_order_lifecycle_stops_before_create_after_three_preflight_failures():
    exchange = FakePreflightAlwaysTimesOut()

    with pytest.raises(RequestTimeout, match="metadata timeout"):
        await accept_order_lifecycle(
            "BTC/USDT",
            "buy",
            0.1,
            100,
            confirmation="BIFU_TEST_WRITE",
            exchange=exchange,
        )

    assert exchange.load_markets_count == 3
    assert sum(call[0] == "create_order" for call in exchange.calls) == 0
