import pytest

import examples.accept_bifu_cancel_all_orders as acceptance_module
from examples.accept_bifu_cancel_all_orders import accept_cancel_all_orders


class FakeExchange:
    def __init__(self):
        self.id = "bifu"
        self.isSandboxModeEnabled = True
        sandbox_urls = {"public": "https://test", "private": "https://test"}
        self.urls = {"api": dict(sandbox_urls), "test": sandbox_urls}
        self.calls = []
        self.closed = False
        self.orders = []

    def set_sandbox_mode(self, enabled):
        self.isSandboxModeEnabled = enabled
        self.calls.append(("set_sandbox_mode", enabled))

    async def close(self):
        self.closed = True
        self.calls.append(("close",))

    async def load_markets(self):
        self.calls.append(("load_markets",))
        return {"BTC/USDT": {"symbol": "BTC/USDT"}}

    async def fetch_open_orders(self, symbol):
        self.calls.append(("fetch_open_orders", symbol))
        return list(self.orders)

    async def create_order(self, symbol, type, side, amount, price, params):
        order = {
            "id": f"secret-order-{len(self.orders) + 1}",
            "clientOrderId": params["clientOrderId"],
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": amount,
            "price": price,
            "status": None,
            "filled": None,
        }
        self.calls.append(("create_order", symbol, type, side, amount, price, params))
        self.orders.append(order)
        return order

    async def cancel_all_orders(self, symbol):
        self.calls.append(("cancel_all_orders", symbol))
        count = len(self.orders)
        self.orders = []
        return [{"symbol": symbol, "status": None, "info": {"canceled": count}}]

    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel_order", order_id, symbol))
        self.orders = [order for order in self.orders if order["id"] != order_id]


class FakeSecondCreateFails(FakeExchange):
    async def create_order(self, symbol, type, side, amount, price, params):
        if self.orders:
            raise RuntimeError("fixture second create failed")
        return await super().create_order(symbol, type, side, amount, price, params)


class FakeCleanupFails(FakeSecondCreateFails):
    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel_order", order_id, symbol))
        raise RuntimeError("fixture cleanup failed")


class FakeConcurrentOrderAppears(FakeExchange):
    def __init__(self):
        super().__init__()
        self.fetch_count = 0

    async def fetch_open_orders(self, symbol):
        self.fetch_count += 1
        if self.fetch_count == 2:
            self.orders.append({"id": "not-owned", "symbol": symbol})
        return await super().fetch_open_orders(symbol)


class FakeWrongAckCount(FakeExchange):
    async def cancel_all_orders(self, symbol):
        self.calls.append(("cancel_all_orders", symbol))
        self.orders = []
        return [{"symbol": symbol, "status": None, "info": {"canceled": 1}}]


class FakeResidualOrderAfterCancel(FakeExchange):
    async def cancel_all_orders(self, symbol):
        self.calls.append(("cancel_all_orders", symbol))
        self.orders = [{"id": "not-owned", "symbol": symbol}]
        return [{"symbol": symbol, "status": None, "info": {"canceled": 2}}]


async def test_cancel_all_acceptance_creates_two_owned_orders_and_verifies_no_residue():
    exchange = FakeExchange()

    result = await accept_cancel_all_orders(
        "BTC/USDT",
        "buy",
        0.00008,
        70000,
        confirmation="BIFU_TEST_CANCEL_ALL_WRITE",
        exchange=exchange,
        poll_delay=0,
    )

    assert sum(call[0] == "create_order" for call in exchange.calls) == 2
    assert ("cancel_all_orders", "BTC/USDT") in exchange.calls
    assert result == {
        "environment": "test",
        "symbol": "BTC/USDT",
        "side": "buy",
        "order_type": "limit",
        "post_only": True,
        "initial_open_order_count": 0,
        "created_order_count": 2,
        "both_open_before_cancel": True,
        "cancel_ack_count": 2,
        "both_absent_after_cancel": True,
        "remaining_open_order_count": 0,
        "identifiers_redacted": True,
    }
    assert "secret-order" not in repr(result)
    assert "secret-client" not in repr(result)


async def test_cancel_all_acceptance_stops_when_market_already_has_open_orders():
    exchange = FakeExchange()
    exchange.orders = [{"id": "not-owned", "symbol": "BTC/USDT"}]

    with pytest.raises(RuntimeError, match="already has open orders"):
        await accept_cancel_all_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="BIFU_TEST_CANCEL_ALL_WRITE",
            exchange=exchange,
            poll_delay=0,
        )

    assert sum(call[0] == "create_order" for call in exchange.calls) == 0
    assert sum(call[0] == "cancel_all_orders" for call in exchange.calls) == 0


async def test_cancel_all_acceptance_requires_exact_confirmation():
    exchange = FakeExchange()

    with pytest.raises(RuntimeError, match="BIFU_TEST_CANCEL_ALL_WRITE"):
        await accept_cancel_all_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="yes",
            exchange=exchange,
        )

    assert exchange.calls == []


async def test_cancel_all_acceptance_cleans_first_order_when_second_create_fails():
    exchange = FakeSecondCreateFails()

    with pytest.raises(RuntimeError, match="second create failed"):
        await accept_cancel_all_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="BIFU_TEST_CANCEL_ALL_WRITE",
            exchange=exchange,
            poll_delay=0,
        )

    assert exchange.orders == []
    assert sum(call[0] == "cancel_order" for call in exchange.calls) == 1


async def test_cancel_all_acceptance_surfaces_residual_order_after_cleanup_failure():
    exchange = FakeCleanupFails()

    with pytest.raises(RuntimeError, match="may remain open"):
        await accept_cancel_all_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="BIFU_TEST_CANCEL_ALL_WRITE",
            exchange=exchange,
            poll_delay=0,
        )

    assert len(exchange.orders) == 1


async def test_cancel_all_acceptance_stops_if_concurrent_order_appears_before_cancel():
    exchange = FakeConcurrentOrderAppears()

    with pytest.raises(RuntimeError, match="unexpected open orders appeared"):
        await accept_cancel_all_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="BIFU_TEST_CANCEL_ALL_WRITE",
            exchange=exchange,
            poll_delay=0,
        )

    assert sum(call[0] == "cancel_all_orders" for call in exchange.calls) == 0
    assert exchange.orders == [{"id": "not-owned", "symbol": "BTC/USDT"}]


async def test_cancel_all_acceptance_rejects_ack_count_other_than_two():
    exchange = FakeWrongAckCount()

    with pytest.raises(RuntimeError, match="expected 2"):
        await accept_cancel_all_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="BIFU_TEST_CANCEL_ALL_WRITE",
            exchange=exchange,
            poll_delay=0,
        )


async def test_cancel_all_acceptance_rejects_any_remaining_open_order():
    exchange = FakeResidualOrderAfterCancel()

    with pytest.raises(RuntimeError, match="still has open orders"):
        await accept_cancel_all_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="BIFU_TEST_CANCEL_ALL_WRITE",
            exchange=exchange,
            poll_delay=0,
        )


async def test_owned_exchange_closes_even_when_failed_cleanup_still_has_order(monkeypatch):
    exchange = FakeCleanupFails()
    monkeypatch.setattr(
        acceptance_module,
        "prepare_sandbox_exchange",
        lambda _exchange=None, **kwargs: (exchange, True),
    )

    with pytest.raises(RuntimeError, match="may remain open"):
        await accept_cancel_all_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="BIFU_TEST_CANCEL_ALL_WRITE",
            poll_delay=0,
        )

    assert exchange.closed is True
