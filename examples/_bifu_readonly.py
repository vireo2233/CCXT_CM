"""Shared retry boundary for Bifu's read-only manual acceptance tools."""

import asyncio
import os
import sys

from ccxt import RequestTimeout


def credentials_from_environment():
    """Build a CCXT config without logging or persisting Bifu credentials."""
    api_key = os.environ.get("BIFU_API_KEY")
    secret = os.environ.get("BIFU_API_SECRET")
    if not api_key or not secret:
        raise RuntimeError("BIFU_API_KEY and BIFU_API_SECRET must be set")
    return {"apiKey": api_key, "secret": secret, "timeout": 30000}


async def retry_readonly_once(first_call, retry_call=None, *, retry_delay=1.0):
    try:
        return await first_call()
    except RequestTimeout:
        print(
            "Bifu 测试环境首次请求超时，正在进行第 2 次只读重试...",
            file=sys.stderr,
        )
        if retry_delay:
            await asyncio.sleep(retry_delay)
        return await (retry_call or first_call)()
