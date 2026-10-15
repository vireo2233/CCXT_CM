"""Safely accept Bifu cancel-all for one test-environment market."""

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

_CONFIRMATION = "BIFU_TEST_CANCEL_ALL_WRITE"


async def accept_cancel_all_orders(
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
    """Create two owned passive orders, cancel the market, and verify no residue."""
    if confirmation != _CONFIRMATION:
        raise RuntimeError(f"test write requires confirmation {_CONFIRMATION}")

    exchange, owns_exchange = prepare_sandbox_exchange(exchange)
    client_ids = {new_client_order_id() for _ in range(2)}
    while len(client_ids) < 2:
        client_ids.add(new_client_order_id())
    created = []
    owned_ids = set()
    write_started = False
    cancel_all_verified = False
    try:
        await load_markets_with_retry(exchange)
        initial_open_orders = await exchange.fetch_open_orders(symbol)
        if initial_open_orders:
            raise RuntimeError("test market already has open orders; refusing cancel-all")

        write_started = True
        for client_order_id in sorted(client_ids):
            created.append(
                await exchange.create_order(
                    symbol,
                    "limit",
                    side,
                    amount,
                    price,
                    {"clientOrderId": client_order_id, "postOnly": True},
                )
            )
        owned_ids = {order["id"] for order in created}
        before_cancel = await exchange.fetch_open_orders(symbol)
        before_ids = {order["id"] for order in before_cancel}
        both_open = before_ids == owned_ids and len(owned_ids) == 2
        if not both_open:
            raise RuntimeError("unexpected open orders appeared before cancel-all")

        acknowledgements = await exchange.cancel_all_orders(symbol)
        canceled = acknowledgements[0]["info"]["canceled"]
        if canceled != 2:
            raise RuntimeError("cancel-all ACK count did not match expected 2")
        after_cancel = await wait_until_orders_absent(
            exchange,
            symbol,
            owned_ids,
            poll_attempts,
            poll_delay,
        )
        remaining_ids = {order["id"] for order in after_cancel}
        both_absent = owned_ids.isdisjoint(remaining_ids)
        if not both_absent:
            raise RuntimeError("test orders are still open after cancel-all")
        if after_cancel:
            raise RuntimeError("test market still has open orders after cancel-all")
        cancel_all_verified = True
        return {
            "environment": "test",
            "symbol": symbol,
            "side": side,
            "order_type": "limit",
            "post_only": True,
            "initial_open_order_count": len(initial_open_orders),
            "created_order_count": len(created),
            "both_open_before_cancel": both_open,
            "cancel_ack_count": canceled,
            "both_absent_after_cancel": both_absent,
            "remaining_open_order_count": len(after_cancel),
            "identifiers_redacted": True,
        }
    finally:
        try:
            if write_started and not cancel_all_verified:
                await cleanup_owned_orders(
                    exchange,
                    symbol,
                    owned_ids,
                    client_ids,
                    label="cancel-all acceptance",
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
    result = await accept_cancel_all_orders(
        args.symbol,
        args.side,
        args.amount,
        args.price,
        confirmation=args.confirm,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
