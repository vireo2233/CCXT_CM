"""Read one real update from every public Bifu WebSocket stream."""

import argparse
import asyncio
import json

from ccxt_cm import create_exchange


async def inspect_public_streams(symbol):
    exchange = create_exchange("bifu", {"timeout": 30000}, mode="pro")
    exchange.set_sandbox_mode(True)
    try:
        ticker, trades, book, bids_asks, candles, tickers = await asyncio.gather(
            exchange.watch_ticker(symbol),
            exchange.watch_trades(symbol),
            exchange.watch_order_book(symbol, 5),
            exchange.watch_bids_asks([symbol]),
            exchange.watch_ohlcv(symbol, "1m"),
            exchange.watch_tickers([symbol]),
        )
        return {
            "environment": "test",
            "symbol": symbol,
            "watch_ticker": {
                "received": ticker["symbol"] == symbol,
                "standard_fields_present": all(
                    field in ticker for field in ("symbol", "timestamp", "last", "info")
                ),
            },
            "watch_trades": {
                "received": bool(trades),
                "standard_fields_present": all(
                    field in trades[-1]
                    for field in ("symbol", "timestamp", "side", "price", "amount", "info")
                ),
            },
            "watch_order_book": {
                "received": book["symbol"] == symbol,
                "standard_fields_present": all(
                    field in book for field in ("symbol", "timestamp", "bids", "asks", "nonce")
                ),
            },
            "watch_bids_asks": {
                "received": symbol in bids_asks,
                "standard_fields_present": all(
                    field in bids_asks[symbol]
                    for field in ("symbol", "timestamp", "bid", "ask", "info")
                ),
            },
            "watch_ohlcv": {
                "received": bool(candles),
                "standard_six_fields": len(candles[-1]) == 6,
            },
            "watch_tickers": {
                "received": symbol in tickers,
                "standard_fields_present": all(
                    field in tickers[symbol] for field in ("symbol", "timestamp", "last", "info")
                ),
            },
        }
    finally:
        await exchange.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", nargs="?", default="BTC/USDT")
    args = parser.parse_args()
    result = await asyncio.wait_for(inspect_public_streams(args.symbol), timeout=45)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
