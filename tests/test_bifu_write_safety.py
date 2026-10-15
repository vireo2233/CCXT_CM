import pytest

from examples._bifu_write import cleanup_owned_orders, prepare_sandbox_exchange


class StubExchange:
    def __init__(self, exchange_id="bifu", sandbox=True, *, api_url="https://sandbox.example"):
        self.id = exchange_id
        self.isSandboxModeEnabled = sandbox
        sandbox_urls = {
            "public": "https://sandbox.example",
            "private": "https://sandbox.example",
            "ws": "wss://sandbox.example",
        }
        self.urls = {"api": dict(sandbox_urls), "test": sandbox_urls}
        self.urls["api"]["private"] = api_url


def test_prepare_sandbox_exchange_accepts_only_bifu_sandbox_instances():
    exchange = StubExchange()

    prepared, owns_exchange = prepare_sandbox_exchange(exchange)

    assert prepared is exchange
    assert owns_exchange is False


@pytest.mark.parametrize(
    ("exchange_id", "sandbox"),
    [("bifu", False), ("binance", True)],
)
def test_prepare_sandbox_exchange_rejects_unsafe_injected_instances(exchange_id, sandbox):
    with pytest.raises(RuntimeError, match="requires configured sandbox endpoints"):
        prepare_sandbox_exchange(StubExchange(exchange_id, sandbox))


def test_prepare_sandbox_exchange_rejects_production_url_with_sandbox_flag():
    exchange = StubExchange(api_url="https://production.example")

    with pytest.raises(RuntimeError, match="requires configured sandbox endpoints"):
        prepare_sandbox_exchange(exchange)


class DelayedOpenOrdersExchange:
    def __init__(self):
        self.fetch_count = 0
        self.cancelled = set()
        self.calls = []

    async def fetch_open_orders(self, symbol):
        self.calls.append(("fetch_open_orders", symbol))
        self.fetch_count += 1
        orders = []
        if self.fetch_count >= 2 and "delayed-order-1" not in self.cancelled:
            orders.append(
                {
                    "id": "delayed-order-1",
                    "clientOrderId": "owned-client-1",
                    "symbol": symbol,
                }
            )
        if self.fetch_count >= 3 and "delayed-order-2" not in self.cancelled:
            orders.append(
                {
                    "id": "delayed-order-2",
                    "clientOrderId": "owned-client-2",
                    "symbol": symbol,
                }
            )
        return orders

    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel_order", order_id, symbol))
        self.cancelled.add(order_id)


@pytest.mark.asyncio
async def test_cleanup_discovers_all_unknown_batch_writes_that_appear_late():
    exchange = DelayedOpenOrdersExchange()
    owned_ids = set()

    await cleanup_owned_orders(
        exchange,
        "BTC/USDT",
        owned_ids,
        {"owned-client-1", "owned-client-2"},
        label="delayed write",
        poll_attempts=3,
        poll_delay=0,
    )

    assert owned_ids == {"delayed-order-1", "delayed-order-2"}
    assert exchange.cancelled == {"delayed-order-1", "delayed-order-2"}
