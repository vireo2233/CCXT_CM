"""Safely accept Bifu single and batch amend in the test environment."""

import argparse
import asyncio
import json
from decimal import Decimal, InvalidOperation

from examples._bifu_write import (
    cleanup_owned_orders,
    load_markets_with_retry,
    new_client_order_id,
    prepare_sandbox_exchange,
    wait_until_orders_absent,
)

_CONFIRMATION = "BIFU_TEST_AMEND_WRITE"


def _decimal(value, label):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise RuntimeError(f"{label} must be a finite number") from error
    if not result.is_finite():
        raise RuntimeError(f"{label} must be a finite number")
    return result


def _check_passive_prices(side, prices, last):
    last_value = _decimal(last, "ticker last price")
    if last_value <= 0:
        raise RuntimeError("ticker last price must be positive")
    values = [_decimal(price, "order price") for price in prices]
    if any(value <= 0 for value in values):
        raise RuntimeError("order prices must be positive")
    if side == "buy" and any(value >= last_value for value in values):
        raise RuntimeError("buy prices must stay below the current last price")
    if side == "sell" and any(value <= last_value for value in values):
        raise RuntimeError("sell prices must stay above the current last price")


async def _wait_for_prices(exchange, symbol, expected_prices, poll_attempts, poll_delay):
    open_orders = []
    for attempt in range(poll_attempts):
        open_orders = await exchange.fetch_open_orders(symbol)
        if _has_expected_prices(open_orders, expected_prices):
            return open_orders
        if attempt < poll_attempts - 1 and poll_delay:
            await asyncio.sleep(poll_delay)
    return open_orders


def _has_expected_prices(open_orders, expected_prices):
    prices = {
        order.get("id"): _decimal(order.get("price"), "open-order price")
        for order in open_orders
        if order.get("id") in expected_prices
    }
    return len(prices) == len(expected_prices) and all(
        prices[order_id] == _decimal(price, "expected price")
        for order_id, price in expected_prices.items()
    )


async def accept_amend_orders(
    symbol,
    side,
    amount,
    initial_price,
    single_price,
    batch_first_price,
    batch_second_price,
    *,
    confirmation,
    exchange=None,
    poll_attempts=10,
    poll_delay=0.5,
):
    """Create two passive orders, amend them, verify, then remove them."""
    if confirmation != _CONFIRMATION:
        raise RuntimeError(f"test write requires confirmation {_CONFIRMATION}")
    if side not in ("buy", "sell"):
        raise RuntimeError("side must be buy or sell")

    exchange, owns_exchange = prepare_sandbox_exchange(exchange)
    client_ids = {new_client_order_id() for _ in range(2)}
    while len(client_ids) < 2:
        client_ids.add(new_client_order_id())
    created = []
    owned_ids = set()
    write_started = False
    cleanup_verified = False
    try:
        await load_markets_with_retry(exchange)
        ticker = await exchange.fetch_ticker(symbol)
        _check_passive_prices(
            side,
            (initial_price, single_price, batch_first_price, batch_second_price),
            ticker.get("last"),
        )
        if await exchange.fetch_open_orders(symbol):
            raise RuntimeError("test market already has open orders; refusing amend write")

        requests = [
            {
                "symbol": symbol,
                "type": "limit",
                "side": side,
                "amount": amount,
                "price": initial_price,
                "params": {"clientOrderId": client_id, "postOnly": True},
            }
            for client_id in sorted(client_ids)
        ]
        write_started = True
        created = await exchange.create_orders(requests)
        owned_ids = {order.get("id") for order in created if order.get("id")}
        if len(created) != 2 or len(owned_ids) != 2:
            raise RuntimeError("batch create did not return two owned order ids")

        initial_orders = await _wait_for_prices(
            exchange,
            symbol,
            {order_id: initial_price for order_id in owned_ids},
            poll_attempts,
            poll_delay,
        )
        if {order.get("id") for order in initial_orders} != owned_ids:
            raise RuntimeError("created orders were not the only open test orders")

        first_id, second_id = [order["id"] for order in created]
        single_ack = await exchange.edit_order(
            first_id, symbol, "limit", side, amount, single_price
        )
        if single_ack.get("id") != first_id:
            raise RuntimeError("single amend ACK did not match the requested order")
        after_single = await _wait_for_prices(
            exchange,
            symbol,
            {first_id: single_price, second_id: initial_price},
            poll_attempts,
            poll_delay,
        )
        single_verified = _has_expected_prices(
            after_single, {first_id: single_price, second_id: initial_price}
        )
        if not single_verified:
            raise RuntimeError("single amend was not visible in current open orders")

        batch_requests = [
            {
                "id": first_id,
                "symbol": symbol,
                "type": "limit",
                "side": side,
                "amount": amount,
                "price": batch_first_price,
            },
            {
                "id": second_id,
                "symbol": symbol,
                "type": "limit",
                "side": side,
                "amount": amount,
                "price": batch_second_price,
            },
        ]
        batch_acks = await exchange.edit_orders(batch_requests)
        ack_ids = {ack.get("id") for ack in batch_acks}
        all_accepted = (
            len(batch_acks) == 2
            and ack_ids == owned_ids
            and all(ack.get("info", {}).get("accepted") is True for ack in batch_acks)
        )
        if not all_accepted:
            raise RuntimeError("batch amend did not accept both owned orders")

        expected_batch_prices = {
            first_id: batch_first_price,
            second_id: batch_second_price,
        }
        after_batch = await _wait_for_prices(
            exchange,
            symbol,
            expected_batch_prices,
            poll_attempts,
            poll_delay,
        )
        visible_batch_prices = {
            order.get("id"): _decimal(order.get("price"), "open-order price")
            for order in after_batch
            if order.get("id") in owned_ids
        }
        batch_verified = len(visible_batch_prices) == 2 and all(
            visible_batch_prices[order_id] == _decimal(price, "batch amend price")
            for order_id, price in expected_batch_prices.items()
        )
        if not batch_verified:
            raise RuntimeError("batch amend prices were not visible in current open orders")

        cancel_acks = await exchange.cancel_orders([first_id, second_id], symbol)
        if {ack.get("id") for ack in cancel_acks} != owned_ids:
            raise RuntimeError("batch cancel ACKs did not match the owned orders")
        after_cancel = await wait_until_orders_absent(
            exchange, symbol, owned_ids, poll_attempts, poll_delay
        )
        both_absent = owned_ids.isdisjoint({order.get("id") for order in after_cancel})
        if not both_absent or after_cancel:
            raise RuntimeError("test orders remain open after amend acceptance cleanup")
        cleanup_verified = True

        return {
            "environment": "test",
            "symbol": symbol,
            "side": side,
            "order_type": "limit",
            "post_only": True,
            "created_order_count": len(created),
            "single_amend_ack_status": single_ack.get("status"),
            "single_amend_verified": single_verified,
            "batch_amend_ack_count": len(batch_acks),
            "batch_amend_all_accepted": all_accepted,
            "batch_amend_verified": batch_verified,
            "both_absent_after_cancel": both_absent,
            "remaining_open_order_count": len(after_cancel),
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
                    label="amend acceptance",
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
    parser.add_argument("initial_price", type=float)
    parser.add_argument("single_price", type=float)
    parser.add_argument("batch_first_price", type=float)
    parser.add_argument("batch_second_price", type=float)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    result = await accept_amend_orders(
        args.symbol,
        args.side,
        args.amount,
        args.initial_price,
        args.single_price,
        args.batch_first_price,
        args.batch_second_price,
        confirmation=args.confirm,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
