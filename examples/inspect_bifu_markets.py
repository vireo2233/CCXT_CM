"""Read-only Bifu test-environment market inspection for beginner acceptance."""

import argparse
import asyncio
import json

from ccxt_cm import create_exchange
from examples._bifu_readonly import retry_readonly_once


async def inspect_market(symbol="BTC/USDT", *, exchange=None, retry_delay=1.0):
    owns_exchange = exchange is None
    if owns_exchange:
        exchange = create_exchange("bifu", {"timeout": 30000}, mode="async")
        exchange.set_sandbox_mode(True)
    try:
        markets = await retry_readonly_once(
            exchange.load_markets,
            lambda: exchange.load_markets(True),
            retry_delay=retry_delay,
        )
        market = exchange.market(symbol)
        return {
            "environment": "test",
            "market_count": len(markets),
            "id": market["id"],
            "exchange_symbol": market["info"]["symbol"],
            "symbol": market["symbol"],
            "type": market["type"],
            "active": market["active"],
            "precision": market["precision"],
            "limits": {
                "amount": market["limits"]["amount"],
                "cost": market["limits"]["cost"],
            },
            "examples": {
                "amount_1.23456789": exchange.amount_to_precision(symbol, 1.23456789),
                "price_100.129": exchange.price_to_precision(symbol, 100.129),
            },
        }
    finally:
        if owns_exchange:
            await exchange.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", nargs="?", default="BTC/USDT")
    args = parser.parse_args()
    print(json.dumps(await inspect_market(args.symbol), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
