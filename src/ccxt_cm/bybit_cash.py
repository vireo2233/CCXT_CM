"""Bound Bybit cash-log pages and their original completeness evidence."""

import asyncio
from copy import deepcopy

MAX_WINDOW_MS = 7 * 24 * 60 * 60 * 1000
PAGE_SIZE = 50


def _fail(code):
    raise ValueError("bybit_settlement:" + code) from None


def cash_query(start_ms, end_ms):
    if type(start_ms) is not int or type(end_ms) is not int or not 0 <= start_ms < end_ms:
        _fail("invalid_window")
    return {"accountType": "UNIFIED", "startTime": start_ms, "endTime": end_ms, "limit": PAGE_SIZE}


def _page(response):
    if (
        not isinstance(response, dict)
        or type(response.get("retCode")) is not int
        or response["retCode"] != 0
    ):
        _fail("page_failed")
    result = response.get("result")
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("list"), list)
        or "nextPageCursor" not in result
    ):
        _fail("page_shape")
    rows, cursor = result["list"], result["nextPageCursor"]
    if len(rows) > PAGE_SIZE or any(not isinstance(row, dict) for row in rows):
        _fail("page_rows")
    terminal = cursor == ""
    if cursor is None:
        # Observed native terminal shape, not a missing-cursor fallback.
        if rows:
            _fail("unproven_null_cursor")
        terminal = True
    elif not isinstance(cursor, str):
        _fail("cursor_shape")
    return rows, cursor, terminal


async def collect_bybit_transaction_pages(
    fetch_page,
    *,
    start_ms,
    end_ms,
    timeout=5,
    max_pages=None,
):
    """Read the bound native transaction-log endpoint, without event filters.

    ``fetch_page(params)`` is the bound native endpoint, or the caller's existing
    private ``_call`` partially bound to that endpoint. Errors never log payloads.
    """
    query = cash_query(start_ms, end_ms)
    if end_ms - start_ms > MAX_WINDOW_MS:
        segments, start = [], start_ms
        while start < end_ms:
            end = min(end_ms, start + MAX_WINDOW_MS)
            segments.append(
                await collect_bybit_transaction_pages(
                    fetch_page,
                    start_ms=start,
                    end_ms=end,
                    timeout=timeout,
                    max_pages=max_pages,
                )
            )
            start = end
        return {"query": query, "segments": segments}
    if max_pages is not None and (type(max_pages) is not int or max_pages <= 0):
        _fail("invalid_page_limit")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout < float("inf")
    ):
        _fail("invalid_timeout")
    pages, seen, cursor = [], set(), None
    while max_pages is None or len(pages) < max_pages:
        params = dict(query)
        if cursor is not None:
            params["cursor"] = cursor
        try:
            async with asyncio.timeout(timeout):
                response = await fetch_page(params)
        except TimeoutError:
            _fail("page_timeout")
        except Exception:
            _fail("page_read_failed")
        rows, next_cursor, terminal = _page(response)
        if not rows and not terminal:
            _fail("empty_cursor_page")
        pages.append({"cursor": cursor, "response": deepcopy(response)})
        if terminal:
            return {"query": query, "pages": pages}
        if next_cursor in seen:
            _fail("cursor_loop")
        seen.add(next_cursor)
        cursor = next_cursor
    _fail("page_limit")


def transaction_rows(bundle, start_ms, end_ms):
    if not isinstance(bundle, dict) or bundle.get("query") != cash_query(start_ms, end_ms):
        _fail("filtered_or_wrong_window")
    if "segments" in bundle:
        segments = bundle["segments"]
        if not isinstance(segments, list) or not segments:
            _fail("missing_pages")
        result, seen, start = [], {}, start_ms
        for segment in segments:
            end = min(end_ms, start + MAX_WINDOW_MS)
            for row in transaction_rows(segment, start, end):
                # Adjacent inclusive API windows may repeat their boundary row.
                key = row.get("id")
                if key is None:
                    _fail("cash_identity")
                if key in seen:
                    if seen[key] != row:
                        _fail("cash_identity_conflict")
                    continue
                seen[key] = row
                result.append(row)
            start = end
        if start != end_ms:
            _fail("incomplete_pages")
        return result
    if end_ms - start_ms > MAX_WINDOW_MS:
        _fail("window_too_large")
    pages = bundle.get("pages")
    if not isinstance(pages, list) or not pages:
        _fail("missing_pages")
    cursor, seen, rows = None, set(), []
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or "cursor" not in page or page["cursor"] != cursor:
            _fail("cursor_chain")
        values, next_cursor, terminal = _page(page.get("response"))
        rows.extend(values)
        if terminal:
            if index != len(pages) - 1:
                _fail("pages_after_end")
            return rows
        if next_cursor in seen:
            _fail("cursor_loop")
        seen.add(next_cursor)
        cursor = next_cursor
    _fail("incomplete_pages")
