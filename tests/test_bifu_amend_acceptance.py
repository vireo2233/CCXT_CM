import pytest

from examples.accept_bifu_amend_orders import accept_amend_orders


class FakeExchange:
    def __init__(self):
        self.id = "bifu"
        self.isSandboxModeEnabled = True
        sandbox_urls = {"public": "https://test", "private": "https://test"}
        self.urls = {"api": dict(sandbox_urls), "test": sandbox_urls}
        self.calls = []
        self.orders = []

    async def load_markets(self):
        self.calls.append(("load_markets",))
        return {"BTC/USDT": {"symbol": "BTC/USDT"}}

    async def fetch_ticker(self, symbol):
        self.calls.append(("fetch_ticker", symbol))
        return {"symbol": symbol, "last": 80000.0}

    async def fetch_open_orders(self, symbol):
        self.calls.append(("fetch_open_orders", symbol))
        return [dict(order) for order in self.orders]

    async def create_orders(self, requests):
        self.calls.append(("create_orders", requests))
        created = []
        for index, request in enumerate(requests, start=1):
            order = {
                "id": f"secret-order-{index}",
                "clientOrderId": request["params"]["clientOrderId"],
                "symbol": request["symbol"],
                "type": request["type"],
                "side": request["side"],
                "amount": request["amount"],
                "price": request["price"],
                "status": "open",
            }
            self.orders.append(order)
            created.append(dict(order))
        return created

    async def edit_order(self, order_id, symbol, order_type, side, amount, price):
        self.calls.append(("edit_order", order_id, symbol, order_type, side, amount, price))
        for order in self.orders:
            if order["id"] == order_id:
                order["amount"] = amount
                order["price"] = price
                return {
                    "id": order_id,
                    "symbol": symbol,
                    "status": None,
                    "info": {"order_id": order_id},
                }
        raise RuntimeError("fixture order missing")

    async def edit_orders(self, requests):
        self.calls.append(("edit_orders", requests))
        result = []
        for request in requests:
            for order in self.orders:
                if order["id"] == request["id"]:
                    order["amount"] = request["amount"]
                    order["price"] = request["price"]
                    result.append(
                        {
                            "id": order["id"],
                            "symbol": order["symbol"],
                            "status": None,
                            "info": {
                                "order_id": order["id"],
                                "accepted": True,
                                "reject_code": "",
                            },
                        }
                    )
        return result

    async def cancel_orders(self, ids, symbol):
        self.calls.append(("cancel_orders", ids, symbol))
        self.orders = [order for order in self.orders if order["id"] not in ids]
        return [{"id": order_id, "symbol": symbol, "status": None} for order_id in ids]

    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel_order", order_id, symbol))
        self.orders = [order for order in self.orders if order["id"] != order_id]


class FakeSingleAmendFails(FakeExchange):
    async def edit_order(self, order_id, symbol, order_type, side, amount, price):
        self.calls.append(("edit_order", order_id, symbol, order_type, side, amount, price))
        raise RuntimeError("fixture single amend failed")


class FakeBatchAmendTimesOutAfterApply(FakeExchange):
    async def edit_orders(self, requests):
        await super().edit_orders(requests)
        raise RuntimeError("fixture timeout after server applied batch amend")


class FakeSecondOrderDisappearsAfterSingleAmend(FakeExchange):
    async def edit_order(self, order_id, symbol, order_type, side, amount, price):
        result = await super().edit_order(order_id, symbol, order_type, side, amount, price)
        self.orders = [order for order in self.orders if order["id"] == order_id]
        return result


async def test_amend_acceptance_verifies_single_and_batch_changes_without_residue():
    exchange = FakeExchange()

    result = await accept_amend_orders(
        "BTC/USDT",
        "buy",
        0.00008,
        70000,
        69000,
        68000,
        67000,
        confirmation="BIFU_TEST_AMEND_WRITE",
        exchange=exchange,
        poll_delay=0,
    )

    assert sum(call[0] == "create_orders" for call in exchange.calls) == 1
    assert sum(call[0] == "edit_order" for call in exchange.calls) == 1
    assert sum(call[0] == "edit_orders" for call in exchange.calls) == 1
    assert sum(call[0] == "cancel_orders" for call in exchange.calls) == 1
    assert exchange.orders == []
    assert result == {
        "environment": "test",
        "symbol": "BTC/USDT",
        "side": "buy",
        "order_type": "limit",
        "post_only": True,
        "created_order_count": 2,
        "single_amend_ack_status": None,
        "single_amend_verified": True,
        "batch_amend_ack_count": 2,
        "batch_amend_all_accepted": True,
        "batch_amend_verified": True,
        "both_absent_after_cancel": True,
        "remaining_open_order_count": 0,
        "identifiers_redacted": True,
    }
    assert "secret-order" not in repr(result)


async def test_amend_acceptance_requires_exact_confirmation_before_network():
    exchange = FakeExchange()

    with pytest.raises(RuntimeError, match="BIFU_TEST_AMEND_WRITE"):
        await accept_amend_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            69000,
            68000,
            67000,
            confirmation="yes",
            exchange=exchange,
        )

    assert exchange.calls == []


async def test_amend_acceptance_rejects_prices_that_could_cross_the_market():
    exchange = FakeExchange()

    with pytest.raises(RuntimeError, match="below the current last price"):
        await accept_amend_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            81000,
            69000,
            68000,
            67000,
            confirmation="BIFU_TEST_AMEND_WRITE",
            exchange=exchange,
        )

    assert sum(call[0] == "create_orders" for call in exchange.calls) == 0


async def test_amend_acceptance_cleans_owned_orders_after_single_amend_failure():
    exchange = FakeSingleAmendFails()

    with pytest.raises(RuntimeError, match="single amend failed"):
        await accept_amend_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            69000,
            68000,
            67000,
            confirmation="BIFU_TEST_AMEND_WRITE",
            exchange=exchange,
            poll_delay=0,
        )

    assert exchange.orders == []
    assert sum(call[0] == "edit_order" for call in exchange.calls) == 1
    assert sum(call[0] == "cancel_order" for call in exchange.calls) == 2


async def test_amend_acceptance_does_not_retry_unknown_batch_amend_result():
    exchange = FakeBatchAmendTimesOutAfterApply()

    with pytest.raises(RuntimeError, match="timeout after server applied"):
        await accept_amend_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            69000,
            68000,
            67000,
            confirmation="BIFU_TEST_AMEND_WRITE",
            exchange=exchange,
            poll_delay=0,
        )

    assert exchange.orders == []
    assert sum(call[0] == "edit_orders" for call in exchange.calls) == 1
    assert sum(call[0] == "cancel_order" for call in exchange.calls) == 2


async def test_amend_acceptance_stops_if_second_order_disappears_after_single_amend():
    exchange = FakeSecondOrderDisappearsAfterSingleAmend()

    with pytest.raises(RuntimeError, match="single amend was not visible"):
        await accept_amend_orders(
            "BTC/USDT",
            "buy",
            0.00008,
            70000,
            69000,
            68000,
            67000,
            confirmation="BIFU_TEST_AMEND_WRITE",
            exchange=exchange,
            poll_attempts=1,
            poll_delay=0,
        )

    assert sum(call[0] == "edit_orders" for call in exchange.calls) == 0
    assert exchange.orders == []
