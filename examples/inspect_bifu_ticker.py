"""Read-only Bifu test-environment ticker inspection for beginner acceptance."""

import argparse
import asyncio
import json
import math

from ccxt import BadResponse

from ccxt_cm import create_exchange
from examples._bifu_readonly import retry_readonly_once


def summarize_trend(response, instrument_id):
    if not isinstance(response, dict) or not isinstance(response.get("trends"), list):
        raise BadResponse("bifu trends inspection requires a trends list")
    if len(response["trends"]) != 1 or not isinstance(response["trends"][0], dict):
        raise BadResponse("bifu trends inspection expected exactly one market")
    trend = response["trends"][0]
    if str(trend.get("instrument_id")) != str(instrument_id):
        raise BadResponse("bifu trends instrument id does not match requested market")
    if isinstance(trend.get("open_time"), bool) or not isinstance(trend.get("closes"), list):
        raise BadResponse("bifu trends inspection received invalid points")
    if any(isinstance(value, bool) for value in trend["closes"]):
        raise BadResponse("bifu trends inspection received invalid points")
    try:
        open_time = int(trend["open_time"])
        closes = [float(value) for value in trend["closes"]]
    except (KeyError, OverflowError, TypeError, ValueError) as exc:
        raise BadResponse("bifu trends inspection received invalid points") from exc
    if open_time <= 0 or len(closes) > 24 or any(not math.isfinite(value) for value in closes):
        raise BadResponse("bifu trends inspection received invalid points")
    return {
        "trend_open_time": open_time,
        "trend_point_count": len(closes),
        "trend_first_close": closes[0] if closes else None,
        "trend_last_close": closes[-1] if closes else None,
    }


async def inspect_ticker(symbol="BTC/USDT", *, exchange=None, retry_delay=1.0):
    owns_exchange = exchange is None
    if owns_exchange:
        exchange = create_exchange("bifu", {"timeout": 30000}, mode="async")
        exchange.set_sandbox_mode(True)
    try:
        ticker = await retry_readonly_once(
            lambda: exchange.fetch_ticker(symbol),
            retry_delay=retry_delay,
        )
        bids_asks = await retry_readonly_once(
            lambda: exchange.fetch_bids_asks([symbol]),
            retry_delay=retry_delay,
        )
        best = bids_asks[symbol]
        instrument_id = exchange.market(symbol)["id"]
        trends = await retry_readonly_once(
            lambda: exchange.public_get_market_v1_trends({"instrument_ids": instrument_id}),
            retry_delay=retry_delay,
        )
        result = {
            "environment": "test",
            "symbol": ticker["symbol"],
            "instrument_id": instrument_id,
            "timestamp": ticker["timestamp"],
            "datetime": ticker["datetime"],
            "last": ticker["last"],
            "open": ticker["open"],
            "high": ticker["high"],
            "low": ticker["low"],
            "base_volume": ticker["baseVolume"],
            "quote_volume": ticker["quoteVolume"],
            "change": ticker["change"],
            "percentage": ticker["percentage"],
            "bid": best["bid"],
            "bid_volume": best["bidVolume"],
            "ask": best["ask"],
            "ask_volume": best["askVolume"],
            "book_timestamp": best["timestamp"],
        }
        result.update(summarize_trend(trends, instrument_id))
        return result
    finally:
        if owns_exchange:
            await exchange.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", nargs="?", default="BTC/USDT")
    args = parser.parse_args()
    print(json.dumps(await inspect_ticker(args.symbol), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
