"""Read-only Bifu test-environment fund-flow inspection; never moves funds."""

import argparse
import asyncio
import json

from ccxt_cm import create_exchange
from examples._bifu_readonly import credentials_from_environment, retry_readonly_once


async def inspect_ledger(code=None, limit=5, *, exchange=None, retry_delay=1.0):
    owns_exchange = exchange is None
    if owns_exchange:
        exchange = create_exchange("bifu", credentials_from_environment(), mode="async")
        exchange.set_sandbox_mode(True)
    try:
        entries = await retry_readonly_once(
            lambda: exchange.fetch_ledger(code, limit=limit),
            retry_delay=retry_delay,
        )
        raw_response = exchange.last_json_response or {}
        next_cursor = raw_response.get("next_cursor") if isinstance(raw_response, dict) else None
        return {
            "environment": "test",
            "currency_filter": code,
            "sample_count": len(entries),
            "directions": sorted(
                {entry["direction"] for entry in entries if entry.get("direction")}
            ),
            "types": sorted({entry["type"] for entry in entries if entry.get("type")}),
            "currencies": sorted({entry["currency"] for entry in entries if entry.get("currency")}),
            "standard_fields_present": (
                all(
                    field in entry
                    for entry in entries
                    for field in (
                        "id",
                        "timestamp",
                        "datetime",
                        "direction",
                        "account",
                        "type",
                        "currency",
                        "amount",
                        "status",
                        "info",
                    )
                )
                if entries
                else None
            ),
            "next_page_available": bool(next_cursor),
            "identifiers_and_amounts_redacted": True,
        }
    finally:
        if owns_exchange:
            await exchange.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("code", nargs="?", default=None)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    print(
        json.dumps(
            await inspect_ledger(args.code, args.limit),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
