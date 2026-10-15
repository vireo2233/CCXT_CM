import pytest

from examples.accept_bifu_batch_orders import accept_batch_orders


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

    async def create_orders(self, requests):
        self.calls.append(("create_orders", requests))
        created = []
        for request in requests:
            params = request["params"]
            order = {
                "id": f"secret-order-{len(self.orders) + 1}",
                "clientOrderId": params["clientOrderId"],
                "symbol": request["symbol"],
                "type": request["type"],
                "side": request["side"],
                "amount": request["amount"],
                "price": request["price"],
                "status": "open",
                "filled": None,
            }
            self.orders.append(order)
            created.append(order)
        return created

    async def cancel_orders(self, ids, symbol):
        self.calls.append(("cancel_orders", ids, symbol))
        self.orders = [order for order in self.orders if order["id"] not in ids]
        return [
            {"id": order_id, "symbol": symbol, "status": None, "info": {"count": len(ids)}}
            for order_id in ids
        ]

    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel_order", order_id, symbol))
        self.orders = [order for order in self.orders if order["id"] != order_id]


class FakePartialCreateRejected(FakeExchange):
    async def create_orders(self, requests):
        created = await super().create_orders(requests[:1])
        return created + [
            {
                "id": None,
                "clientOrderId": requests[1]["params"]["clientOrderId"],
                "symbol": requests[1]["symbol"],
                "status": "rejected",
                "info": {"reject_code": "FIXTURE_REJECT"},
            }
        ]


class FakeBatchCancelFails(FakeExchange):
    async def cancel_orders(self, ids, symbol):
        self.calls.append(("cancel_orders", ids, symbol))
        raise RuntimeError("fixture batch cancel failed")


class FakeCleanupFails(FakeBatchCancelFails):
    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel_order", order_id, symbol))
        raise RuntimeError("fixture cleanup failed")


class FakeCreateTimesOutAfterServerAccepted(FakeExchange):
    async def create_orders(self, requests):
        await super().create_orders(requests)
        self.orders.append(
            {
                "id": "foreign-order",
                "clientOrderId": "foreign-client-id",
                "symbol": requests[0]["symbol"],
                "status": "open",
            }
        )
        raise RuntimeError("fixture client timeout after server accepted batch")


async def test_batch_acceptance_uses_one_create_and_one_cancel_batch_without_residue():
    exchange = FakeExchange()

    result = await accept_batch_orders(
        "BTC/USDT",
        "buy",
        0.00008,
        70000,
        confirmation="BIFU_TEST_BATCH_WRITE",
        exchange=exchange,
        poll_delay=0,
    )

    assert sum(call[0] == "create_orders" for call in exchange.calls) == 1
    assert sum(call[0] == "cancel_orders" for call in exchange.calls) == 1
    assert result == {
        "environment": "test",
        "symbol": "BTC/USDT",
        "side": "buy",
        "order_type": "limit",
        "post_only": True,
        "batch_size": 2,
        "create_ack_count": 2,
        "both_open_before_cancel": True,
        "cancel_ack_count": 2,
        "both_absent_after_cancel": True,
        "remaining_open_order_count": 0,
        "identifiers_redacted": True,
    }
    assert "secret-order" not in repr(result)


async def test_batch_acceptance_requires_exact_confirmation():
    exchange = FakeExchange()

    with pytest.raises(RuntimeError, match="BIFU_TEST_BATCH_WRITE"):
        await accept_batch_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="yes",
            exchange=exchange,
        )

    assert exchange.calls == []


async def test_batch_acceptance_cleans_accepted_order_after_partial_rejection():
    exchange = FakePartialCreateRejected()

    with pytest.raises(RuntimeError, match="batch create did not accept all orders"):
        await accept_batch_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="BIFU_TEST_BATCH_WRITE",
            exchange=exchange,
            poll_delay=0,
        )

    assert exchange.orders == []
    assert sum(call[0] == "cancel_order" for call in exchange.calls) == 1


async def test_batch_acceptance_falls_back_to_single_cancel_when_batch_cancel_fails():
    exchange = FakeBatchCancelFails()

    with pytest.raises(RuntimeError, match="batch cancel failed"):
        await accept_batch_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="BIFU_TEST_BATCH_WRITE",
            exchange=exchange,
            poll_delay=0,
        )

    assert exchange.orders == []
    assert sum(call[0] == "cancel_order" for call in exchange.calls) == 2


async def test_batch_acceptance_surfaces_unverified_cleanup():
    exchange = FakeCleanupFails()

    with pytest.raises(RuntimeError, match="may remain open"):
        await accept_batch_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="BIFU_TEST_BATCH_WRITE",
            exchange=exchange,
            poll_delay=0,
        )

    assert len(exchange.orders) == 2


async def test_batch_acceptance_cleans_only_owned_orders_after_unknown_create_result():
    exchange = FakeCreateTimesOutAfterServerAccepted()

    with pytest.raises(RuntimeError, match="client timeout"):
        await accept_batch_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            confirmation="BIFU_TEST_BATCH_WRITE",
            exchange=exchange,
            poll_delay=0,
        )

    assert sum(call[0] == "create_orders" for call in exchange.calls) == 1
    assert [order["id"] for order in exchange.orders] == ["foreign-order"]
    assert sum(call[0] == "cancel_order" for call in exchange.calls) == 2
