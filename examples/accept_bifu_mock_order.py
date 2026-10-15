"""Accept Bifu's sandbox-only mock order without exposing account identifiers."""

import argparse
import asyncio
import json
from decimal import Decimal, InvalidOperation

from examples._bifu_write import (
    cleanup_owned_orders,
    new_client_order_id,
    prepare_sandbox_exchange,
    wait_for_order_trades,
)

_CONFIRMATION = "BIFU_TEST_MOCK_WRITE"


def _validate_value(value):
    try:
        decimal_value = Decimal(str(value))
        if not decimal_value.is_finite() or decimal_value <= 0:
            raise ValueError
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("mock-order value must be a positive finite number") from exc


async def accept_mock_order(
    symbol,
    side,
    value,
    *,
    confirmation,
    exchange=None,
    poll_attempts=10,
    poll_delay=0.5,
):
    """Create one simulated Bifu trade and verify trade evidence without a book order."""
    if confirmation != _CONFIRMATION:
        raise RuntimeError(f"test mock write requires confirmation {_CONFIRMATION}")
    if side not in ("buy", "sell"):
        raise ValueError("mock-order side must be buy or sell")
    _validate_value(value)

    exchange, owns_exchange = prepare_sandbox_exchange(exchange)
    client_order_id = new_client_order_id()
    client_ids = {client_order_id}
    owned_ids = set()
    write_started = False
    completion_verified = False
    try:
        write_started = True
        created = await exchange.create_mock_order(
            symbol,
            side,
            value,
            {"clientOrderId": client_order_id},
        )
        order_id = created["id"]
        owned_ids.add(order_id)
        trades = await wait_for_order_trades(
            exchange,
            order_id,
            symbol,
            poll_attempts,
            poll_delay,
        )
        open_orders = await exchange.fetch_open_orders(symbol)
        still_open = any(order.get("id") == order_id for order in open_orders)
        if not trades:
            raise RuntimeError("mock order has no matching private trade evidence")
        if still_open:
            raise RuntimeError("mock order unexpectedly entered the open order book")
        completion_verified = True

        order_fields = (
            "id",
            "clientOrderId",
            "symbol",
            "type",
            "side",
            "amount",
            "price",
            "cost",
            "status",
            "filled",
        )
        trade_fields = ("id", "order", "symbol", "price", "amount", "cost")
        return {
            "environment": "test",
            "symbol": symbol,
            "side": side,
            "special_method": "create_mock_order",
            "bifu_order_type": "SANDBOX_MARKET",
            "requested_value": value,
            "requested_value_unit": "quote" if side == "buy" else "base",
            "create_ack_standard_fields_present": all(field in created for field in order_fields),
            "create_status": created["status"],
            "trade_count": len(trades),
            "trade_standard_fields_present": all(
                field in trade for trade in trades for field in trade_fields
            ),
            "order_absent_from_open_orders": True,
            "identifiers_redacted": True,
        }
    finally:
        try:
            if write_started and not completion_verified:
                await cleanup_owned_orders(
                    exchange,
                    symbol,
                    owned_ids,
                    client_ids,
                    label="mock-order acceptance",
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
    parser.add_argument(
        "value",
        type=float,
        help="buy: quote-currency amount; sell: base-currency amount",
    )
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    result = await accept_mock_order(
        args.symbol,
        args.side,
        args.value,
        confirmation=args.confirm,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
