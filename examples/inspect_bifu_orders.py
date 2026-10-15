"""Read-only Bifu test-environment order inspection; never creates or cancels orders."""

import argparse
import asyncio
import json

from ccxt import OrderNotFound

from ccxt_cm import create_exchange
from examples._bifu_readonly import credentials_from_environment, retry_readonly_once


def _sorted_statuses(items):
    statuses = {item["status"] for item in items}
    return sorted(statuses, key=lambda status: (status is not None, status or ""))


async def inspect_orders(symbol="BTC/USDT", *, exchange=None, retry_delay=1.0):
    owns_exchange = exchange is None
    if owns_exchange:
        exchange = create_exchange("bifu", credentials_from_environment(), mode="async")
        exchange.set_sandbox_mode(True)
    try:
        orders = await retry_readonly_once(
            lambda: exchange.fetch_open_orders(symbol),
            retry_delay=retry_delay,
        )
        history = await retry_readonly_once(
            lambda: exchange.fetch_closed_orders(symbol, limit=1),
            retry_delay=retry_delay,
        )
        trades = await retry_readonly_once(
            lambda: exchange.fetch_my_trades(symbol, limit=1),
            retry_delay=retry_delay,
        )
        queried_order = None
        query_candidate = orders[0] if orders else (history[0] if history else None)
        if query_candidate:
            try:
                queried_order = await retry_readonly_once(
                    lambda: exchange.fetch_order(query_candidate["id"], symbol),
                    retry_delay=retry_delay,
                )
            except OrderNotFound:
                pass
        inspected_orders = orders + history
        return {
            "environment": "test",
            "symbol": symbol,
            "open_order_count": len(orders),
            "open_statuses": _sorted_statuses(orders),
            "history_sample_count": len(history),
            "history_statuses": _sorted_statuses(history),
            "trade_sample_count": len(trades),
            "trade_roles": sorted(
                {trade["takerOrMaker"] for trade in trades if trade["takerOrMaker"]}
            ),
            "trade_standard_fields_present": (
                all(
                    field in trade
                    for trade in trades
                    for field in ("id", "order", "symbol", "side", "price", "amount", "fee")
                )
                if trades
                else None
            ),
            "single_order_query_verified": queried_order is not None,
            "standard_fields_present": (
                all(
                    field in order
                    for order in inspected_orders
                    for field in ("id", "symbol", "type", "side", "status", "amount", "filled")
                )
                if inspected_orders
                else None
            ),
        }
    finally:
        if owns_exchange:
            await exchange.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", nargs="?", default="BTC/USDT")
    args = parser.parse_args()
    print(json.dumps(await inspect_orders(args.symbol), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
