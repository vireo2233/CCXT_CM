"""Inspect Bifu environment selection offline; no credentials or network requests."""

import argparse
import asyncio
import json
from typing import Literal

from ccxt_cm import create_exchange

Environment = Literal["test", "production"]


async def inspect_environment(environment: Environment) -> dict:
    exchange = create_exchange("bifu", mode="async")
    try:
        if environment == "test":
            exchange.set_sandbox_mode(True)
        elif environment != "production":
            raise ValueError("environment must be 'test' or 'production'")
        urls = dict(exchange.urls["api"])
        return {
            "id": exchange.id,
            "environment": environment,
            "sandbox": exchange.isSandboxModeEnabled,
            "configured": all(urls.values()),
            "urls": urls,
        }
    finally:
        await exchange.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=("test", "production"))
    args = parser.parse_args()
    print(json.dumps(await inspect_environment(args.environment), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
