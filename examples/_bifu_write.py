"""Shared safety boundaries for Bifu test-environment write acceptance."""

import asyncio
import uuid

from ccxt import NetworkError

from examples._bifu_readonly import credentials_from_environment

_PREFLIGHT_ATTEMPTS = 3
_PREFLIGHT_RETRY_DELAY = 1


def _uses_configured_sandbox_endpoints(exchange):
    urls = getattr(exchange, "urls", None)
    if not isinstance(urls, dict):
        return False
    api_urls = urls.get("api")
    test_urls = urls.get("test")
    if not isinstance(api_urls, dict) or not isinstance(test_urls, dict):
        return False
    endpoint_names = {"public", "private"}
    if "ws" in api_urls or "ws" in test_urls:
        endpoint_names.add("ws")
    return all(
        test_urls.get(name) and api_urls.get(name) == test_urls.get(name) for name in endpoint_names
    )


def prepare_sandbox_exchange(exchange=None, *, mode="async"):
    """Return a Bifu sandbox exchange and whether this helper created it."""
    owns_exchange = exchange is None
    if owns_exchange:
        from ccxt_cm import create_exchange

        exchange = create_exchange("bifu", credentials_from_environment(), mode=mode)
        exchange.set_sandbox_mode(True)
    if (
        getattr(exchange, "id", None) != "bifu"
        or getattr(exchange, "isSandboxModeEnabled", False) is not True
        or not _uses_configured_sandbox_endpoints(exchange)
    ):
        raise RuntimeError("Bifu write acceptance requires configured sandbox endpoints")
    return exchange, owns_exchange


def new_client_order_id():
    """Create the stable client identifier used to reconcile an uncertain write."""
    return uuid.uuid4().hex[:16]


async def load_markets_with_retry(exchange):
    """Retry only the idempotent public metadata preflight."""
    for attempt in range(_PREFLIGHT_ATTEMPTS):
        try:
            return await exchange.load_markets()
        except NetworkError:
            if attempt == _PREFLIGHT_ATTEMPTS - 1:
                raise
            await asyncio.sleep(_PREFLIGHT_RETRY_DELAY)


async def wait_for_order_trades(exchange, order_id, symbol, poll_attempts, poll_delay):
    """Poll private trades until this run's order has matching evidence."""
    for attempt in range(poll_attempts):
        trades = await exchange.fetch_my_trades(symbol, params={"order_id": order_id})
        matching = [trade for trade in trades if trade.get("order") == order_id]
        if matching:
            return matching
        if attempt < poll_attempts - 1 and poll_delay:
            await asyncio.sleep(poll_delay)
    return []


async def wait_until_orders_absent(exchange, symbol, owned_ids, poll_attempts, poll_delay):
    """Poll a read-only endpoint until all owned order ids disappear."""
    open_orders = []
    for attempt in range(poll_attempts):
        open_orders = await exchange.fetch_open_orders(symbol)
        if owned_ids.isdisjoint({order.get("id") for order in open_orders}):
            return open_orders
        if attempt < poll_attempts - 1 and poll_delay:
            await asyncio.sleep(poll_delay)
    return open_orders


async def cleanup_owned_orders(
    exchange,
    symbol,
    owned_ids,
    client_ids,
    *,
    label,
    poll_attempts=10,
    poll_delay=0.5,
):
    """Cancel only this run's orders and prove that none remains open."""
    discovered_ids = set()
    cancel_attempted_ids = set()
    discovery_attempts = max(1, poll_attempts)
    for attempt in range(discovery_attempts):
        try:
            open_orders = await exchange.fetch_open_orders(symbol)
        except Exception as error:
            raise RuntimeError(
                f"could not inspect test orders during failed {label} cleanup"
            ) from error
        cleanup_orders = [
            order
            for order in open_orders
            if order.get("id") in owned_ids or order.get("clientOrderId") in client_ids
        ]
        for order in cleanup_orders:
            order_id = order.get("id")
            if order_id is None:
                continue
            discovered_ids.add(order_id)
            owned_ids.add(order_id)
            if order_id in cancel_attempted_ids:
                continue
            cancel_attempted_ids.add(order_id)
            try:
                await exchange.cancel_order(order_id, symbol)
            except Exception:
                continue
        if attempt < discovery_attempts - 1 and poll_delay:
            await asyncio.sleep(poll_delay)
    try:
        if discovered_ids:
            remaining = await wait_until_orders_absent(
                exchange,
                symbol,
                discovered_ids,
                poll_attempts,
                poll_delay,
            )
        else:
            remaining = await exchange.fetch_open_orders(symbol)
    except Exception as error:
        raise RuntimeError(f"could not verify test-order cleanup after failed {label}") from error
    if any(
        order.get("id") in owned_ids or order.get("clientOrderId") in client_ids
        for order in remaining
    ):
        raise RuntimeError(f"a test order may remain open after failed {label}")
