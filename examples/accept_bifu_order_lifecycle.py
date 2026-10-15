"""Bifu test-only order lifecycle acceptance with an explicit write confirmation."""

import argparse
import asyncio
import json

from ccxt import OrderNotFound, RequestTimeout

from examples._bifu_write import (
    cleanup_owned_orders,
    load_markets_with_retry,
    new_client_order_id,
    prepare_sandbox_exchange,
    wait_until_orders_absent,
)

_CONFIRMATION = "BIFU_TEST_WRITE"
_TERMINAL_STATUSES = {"canceled", "closed", "expired", "rejected"}


async def _wait_for_order(
    exchange,
    order_id,
    symbol,
    *,
    terminal=False,
    poll_attempts=10,
    poll_delay=0.5,
):
    last_order = None
    for attempt in range(poll_attempts):
        try:
            last_order = await exchange.fetch_order(order_id, symbol)
            if not terminal or last_order["status"] in _TERMINAL_STATUSES:
                return last_order
        except (OrderNotFound, RequestTimeout):
            if attempt == poll_attempts - 1:
                raise
        if poll_delay:
            await asyncio.sleep(poll_delay)
    return last_order


async def accept_order_lifecycle(
    symbol,
    side,
    amount,
    price,
    *,
    confirmation,
    exchange=None,
    poll_attempts=10,
    poll_delay=0.5,
):
    """Create one passive test order, query it, cancel it, and query the final state."""
    if confirmation != _CONFIRMATION:
        raise RuntimeError(f"test write requires confirmation {_CONFIRMATION}")

    exchange, owns_exchange = prepare_sandbox_exchange(exchange)
    client_order_id = new_client_order_id()
    client_ids = {client_order_id}
    owned_ids = set()
    created = None
    write_started = False
    cleanup_verified = False
    try:
        await load_markets_with_retry(exchange)
        write_started = True
        created = await exchange.create_order(
            symbol,
            "limit",
            side,
            amount,
            price,
            {"clientOrderId": client_order_id, "postOnly": True},
        )
        order_id = created["id"]
        owned_ids.add(order_id)
        after_create = await _wait_for_order(
            exchange,
            order_id,
            symbol,
            poll_attempts=poll_attempts,
            poll_delay=poll_delay,
        )
        await exchange.cancel_order(order_id, symbol)
        try:
            after_cancel = await _wait_for_order(
                exchange,
                order_id,
                symbol,
                terminal=True,
                poll_attempts=poll_attempts,
                poll_delay=poll_delay,
            )
        except OrderNotFound:
            # The current Bifu test environment can stop exposing an ended order
            # through the single-order endpoint. Verify the same id is absent from
            # open orders instead of treating invisibility as proof of cancellation.
            after_cancel = None
        open_orders = await wait_until_orders_absent(
            exchange,
            symbol,
            owned_ids,
            poll_attempts,
            poll_delay,
        )
        absent_from_open_orders = all(order["id"] != order_id for order in open_orders)
        if not absent_from_open_orders:
            raise RuntimeError("test order is still present in open orders after cancel")
        cleanup_verified = True
        standard_fields = (
            "id",
            "clientOrderId",
            "symbol",
            "type",
            "side",
            "amount",
            "price",
            "status",
            "filled",
        )
        return {
            "environment": "test",
            "symbol": symbol,
            "side": side,
            "order_type": "limit",
            "post_only": True,
            "create_ack_standard_fields_present": all(
                field in created for field in standard_fields
            ),
            "create_status": created["status"],
            "query_after_create_status": after_create["status"],
            "cancel_command_accepted": True,
            "query_after_cancel_found": after_cancel is not None,
            "query_after_cancel_status": after_cancel["status"] if after_cancel else None,
            "order_absent_from_open_orders": absent_from_open_orders,
            "identifiers_redacted": True,
        }
    finally:
        try:
            if write_started and not cleanup_verified:
                await cleanup_owned_orders(
                    exchange,
                    symbol,
                    owned_ids,
                    client_ids,
                    label="order lifecycle acceptance",
                    poll_attempts=poll_attempts,
                    poll_delay=poll_delay,
                )
        finally:
            if owns_exchange:
                await exchange.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("side", choices=("buy", "sell"))
    parser.add_argument("amount", type=float)
    parser.add_argument("price", type=float)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    result = await accept_order_lifecycle(
        args.symbol,
        args.side,
        args.amount,
        args.price,
        confirmation=args.confirm,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
