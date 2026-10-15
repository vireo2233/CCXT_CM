"""Safely accept Bifu batch create and batch cancel in the test environment."""

import argparse
import asyncio
import json

from examples._bifu_write import (
    cleanup_owned_orders,
    load_markets_with_retry,
    new_client_order_id,
    prepare_sandbox_exchange,
    wait_until_orders_absent,
)

_CONFIRMATION = "BIFU_TEST_BATCH_WRITE"


async def _wait_for_owned_orders(exchange, symbol, owned_ids, poll_attempts, poll_delay):
    open_orders = []
    for attempt in range(poll_attempts):
        open_orders = await exchange.fetch_open_orders(symbol)
        open_ids = {order["id"] for order in open_orders}
        if owned_ids.issubset(open_ids):
            return open_orders
        if attempt < poll_attempts - 1 and poll_delay:
            await asyncio.sleep(poll_delay)
    return open_orders


async def accept_batch_orders(
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
    """Create and cancel two passive orders through the two batch endpoints."""
    if confirmation != _CONFIRMATION:
        raise RuntimeError(f"test write requires confirmation {_CONFIRMATION}")

    exchange, owns_exchange = prepare_sandbox_exchange(exchange)
    client_ids = {new_client_order_id() for _ in range(2)}
    while len(client_ids) < 2:
        client_ids.add(new_client_order_id())
    requests = [
        {
            "symbol": symbol,
            "type": "limit",
            "side": side,
            "amount": amount,
            "price": price,
            "params": {"clientOrderId": client_id, "postOnly": True},
        }
        for client_id in sorted(client_ids)
    ]
    created = []
    owned_ids = set()
    write_started = False
    batch_cancel_verified = False
    try:
        await load_markets_with_retry(exchange)
        initial_open_orders = await exchange.fetch_open_orders(symbol)
        if initial_open_orders:
            raise RuntimeError("test market already has open orders; refusing batch write")

        write_started = True
        created = await exchange.create_orders(requests)
        owned_ids = {order["id"] for order in created if order.get("id")}
        all_accepted = (
            len(created) == 2
            and len(owned_ids) == 2
            and all(order.get("status") != "rejected" for order in created)
        )
        if not all_accepted:
            raise RuntimeError("batch create did not accept all orders")

        before_cancel = await _wait_for_owned_orders(
            exchange, symbol, owned_ids, poll_attempts, poll_delay
        )
        before_ids = {order["id"] for order in before_cancel}
        both_open = before_ids == owned_ids
        if not both_open:
            raise RuntimeError("batch-created orders were not the only open test orders")

        acknowledgements = await exchange.cancel_orders([order["id"] for order in created], symbol)
        acknowledged_ids = {order["id"] for order in acknowledgements}
        if len(acknowledgements) != 2 or acknowledged_ids != owned_ids:
            raise RuntimeError("batch cancel ACKs did not match the created orders")

        after_cancel = await wait_until_orders_absent(
            exchange, symbol, owned_ids, poll_attempts, poll_delay
        )
        after_ids = {order["id"] for order in after_cancel}
        both_absent = owned_ids.isdisjoint(after_ids)
        if not both_absent:
            raise RuntimeError("batch-created orders are still open after batch cancel")
        if after_cancel:
            raise RuntimeError("test market still has open orders after batch cancel")
        batch_cancel_verified = True
        return {
            "environment": "test",
            "symbol": symbol,
            "side": side,
            "order_type": "limit",
            "post_only": True,
            "batch_size": 2,
            "create_ack_count": len(created),
            "both_open_before_cancel": both_open,
            "cancel_ack_count": len(acknowledgements),
            "both_absent_after_cancel": both_absent,
            "remaining_open_order_count": len(after_cancel),
            "identifiers_redacted": True,
        }
    finally:
        try:
            if write_started and not batch_cancel_verified:
                await cleanup_owned_orders(
                    exchange,
                    symbol,
                    owned_ids,
                    client_ids,
                    label="batch acceptance",
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
    result = await accept_batch_orders(
        args.symbol,
        args.side,
        args.amount,
        args.price,
        confirmation=args.confirm,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
