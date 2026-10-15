import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web
from ccxt import BadResponse, NotSupported

from ccxt_cm import create_exchange
from examples.inspect_bifu_ticker import inspect_ticker, summarize_trend


def new_bifu(config=None):
    return create_exchange("bifu", config, mode="async")


@pytest.fixture
async def bifu_ticker_server(unused_tcp_port):
    state = SimpleNamespace(
        calls=[],
        additional_assets=[],
        additional_spots=[],
        book_ticker_responses={},
        book_ticker_response={
            "instrument_id": 90000001,
            "bid_price": "538943.01",
            "bid_qty": "0.12",
            "ask_price": "538945.02",
            "ask_qty": "0.34",
            "last_id": "405781215",
            "book_time": "1789712422000",
        },
        ticker_response={
            "instrument_id": 90000001,
            "last_price": "538944.47",
            "open_price": "538000.00",
            "high_price": "540000.00",
            "low_price": "530000.00",
            "volume": "0.44998",
            "quote_volume": "242966.07064",
            "count": "460",
            "price_change": "944.47",
            "price_change_percent": "0.18",
            "ts": "1789712421923",
        },
        ticker_status=200,
        tickers_response=None,
        trends_response={
            "trends": [
                {
                    "instrument_id": 90000001,
                    "open_time": "1789626000000",
                    "closes": ["537000.01", "538944.47"],
                }
            ]
        },
        delay_ticker_first_seconds=0,
    )

    async def meta(request):
        state.calls.append((request.method, request.path, dict(request.query)))
        return web.json_response(
            {
                "assets": [
                    {"asset_id": 1, "code": "BTC"},
                    {"asset_id": 2, "code": "USDT"},
                ]
                + state.additional_assets,
                "spots": [
                    {
                        "instrument_id": 90000001,
                        "base_asset_id": 1,
                        "quote_asset_id": 2,
                        "status": "TRADING",
                        "rules": {},
                    }
                ]
                + state.additional_spots,
            }
        )

    async def ticker(request):
        state.calls.append((request.method, request.path, dict(request.query)))
        if state.delay_ticker_first_seconds:
            delay = state.delay_ticker_first_seconds
            state.delay_ticker_first_seconds = 0
            await asyncio.sleep(delay)
        return web.json_response(state.ticker_response, status=state.ticker_status)

    async def tickers(request):
        state.calls.append((request.method, request.path, dict(request.query)))
        response = state.tickers_response or {"tickers": [state.ticker_response]}
        return web.json_response(response)

    async def book_ticker(request):
        state.calls.append((request.method, request.path, dict(request.query)))
        response = state.book_ticker_responses.get(
            request.query.get("instrument_id"), state.book_ticker_response
        )
        return web.json_response(response)

    async def trends(request):
        state.calls.append((request.method, request.path, dict(request.query)))
        return web.json_response(state.trends_response)

    app = web.Application()
    app.router.add_get("/market/v1/meta", meta)
    app.router.add_get("/market/v1/ticker", ticker)
    app.router.add_get("/market/v1/tickers", tickers)
    app.router.add_get("/market/v1/bookTicker", book_ticker)
    app.router.add_get("/market/v1/trends", trends)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    state.url = f"http://127.0.0.1:{unused_tcp_port}"
    try:
        yield state
    finally:
        await runner.cleanup()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ticker_returns_standard_24h_statistics(bifu_ticker_server):
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        ticker = await exchange.fetch_ticker("BTC/USDT")
    finally:
        await exchange.close()

    assert ticker["symbol"] == "BTC/USDT"
    assert ticker["timestamp"] == 1789712421923
    assert ticker["datetime"] == "2026-09-18T06:20:21.923Z"
    assert ticker["last"] == 538944.47
    assert ticker["open"] == 538000.0
    assert ticker["high"] == 540000.0
    assert ticker["low"] == 530000.0
    assert ticker["baseVolume"] == 0.44998
    assert ticker["quoteVolume"] == 242966.07064
    assert ticker["change"] == 944.47
    assert ticker["percentage"] == 0.18
    assert ticker["average"] is None
    assert ticker["bid"] is None
    assert ticker["ask"] is None
    assert ticker["info"]["count"] == "460"
    assert bifu_ticker_server.calls == [
        ("GET", "/market/v1/meta", {}),
        ("GET", "/market/v1/ticker", {"instrument_id": "90000001"}),
    ]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_tickers_returns_symbol_indexed_ccxt_tickers(bifu_ticker_server):
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        tickers = await exchange.fetch_tickers(["BTC/USDT"])
    finally:
        await exchange.close()

    assert list(tickers) == ["BTC/USDT"]
    assert tickers["BTC/USDT"]["last"] == 538944.47
    assert tickers["BTC/USDT"]["average"] is None
    assert bifu_ticker_server.calls == [
        ("GET", "/market/v1/meta", {}),
        ("GET", "/market/v1/tickers", {"type": "FULL"}),
    ]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_tickers_rejects_unknown_response_market(bifu_ticker_server):
    bifu_ticker_server.tickers_response = {
        "tickers": [{**bifu_ticker_server.ticker_response, "instrument_id": 999}]
    }
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        with pytest.raises(BadResponse, match="unknown market"):
            await exchange.fetch_tickers()
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_bids_asks_returns_standard_best_prices(bifu_ticker_server):
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        tickers = await exchange.fetch_bids_asks(["BTC/USDT"])
    finally:
        await exchange.close()

    assert exchange.has["fetchBidsAsks"] == "emulated"
    assert list(tickers) == ["BTC/USDT"]
    ticker = tickers["BTC/USDT"]
    assert ticker["symbol"] == "BTC/USDT"
    assert ticker["timestamp"] == 1789712422000
    assert ticker["datetime"] == "2026-09-18T06:20:22.000Z"
    assert ticker["bid"] == 538943.01
    assert ticker["bidVolume"] == 0.12
    assert ticker["ask"] == 538945.02
    assert ticker["askVolume"] == 0.34
    assert ticker["last"] is None
    assert ticker["info"]["last_id"] == "405781215"
    assert bifu_ticker_server.calls == [
        ("GET", "/market/v1/meta", {}),
        ("GET", "/market/v1/bookTicker", {"instrument_id": "90000001"}),
    ]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_bids_asks_without_symbols_reads_all_loaded_markets(
    bifu_ticker_server,
):
    bifu_ticker_server.additional_assets.append({"asset_id": 3, "code": "ETH"})
    bifu_ticker_server.additional_spots.append(
        {
            "instrument_id": 90000002,
            "base_asset_id": 3,
            "quote_asset_id": 2,
            "status": "TRADING",
            "rules": {},
        }
    )
    bifu_ticker_server.book_ticker_responses["90000002"] = {
        "instrument_id": 90000002,
        "bid_price": "3024.01",
        "bid_qty": "1.2",
        "ask_price": "3024.02",
        "ask_qty": "0.8",
        "book_time": "1789712423000",
    }
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        tickers = await exchange.fetch_bids_asks()
    finally:
        await exchange.close()

    assert list(tickers) == ["BTC/USDT", "ETH/USDT"]
    assert tickers["BTC/USDT"]["bid"] == 538943.01
    assert tickers["ETH/USDT"]["ask"] == 3024.02
    assert bifu_ticker_server.calls == [
        ("GET", "/market/v1/meta", {}),
        ("GET", "/market/v1/bookTicker", {"instrument_id": "90000001"}),
        ("GET", "/market/v1/bookTicker", {"instrument_id": "90000002"}),
    ]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(
    ("response", "message"),
    [
        ([], "JSON object"),
        ({"instrument_id": 999, "book_time": 1}, "instrument id"),
        ({"instrument_id": 90000001, "book_time": "invalid"}, "timestamp"),
    ],
)
async def test_fetch_bids_asks_rejects_malformed_response(bifu_ticker_server, response, message):
    bifu_ticker_server.book_ticker_response = response
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        with pytest.raises(BadResponse, match=message):
            await exchange.fetch_bids_asks(["BTC/USDT"])
    finally:
        await exchange.close()


async def test_fetch_bids_asks_rejects_unsupported_params_before_network():
    exchange = new_bifu()
    try:
        with pytest.raises(NotSupported, match="does not accept params"):
            await exchange.fetch_bids_asks(["BTC/USDT"], {"unexpected": True})
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_native_trends_is_available_as_a_raw_implicit_api(bifu_ticker_server):
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        response = await exchange.public_get_market_v1_trends(
            {"instrument_ids": "90000001,90000002"}
        )
    finally:
        await exchange.close()

    assert not hasattr(exchange, "fetch_trends")
    assert response == bifu_ticker_server.trends_response
    assert bifu_ticker_server.calls == [
        (
            "GET",
            "/market/v1/trends",
            {"instrument_ids": "90000001,90000002"},
        )
    ]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (None, "trends list"),
        ({"trends": []}, "exactly one market"),
        (
            {"trends": [{"instrument_id": 2, "open_time": "1", "closes": []}]},
            "instrument id",
        ),
        (
            {"trends": [{"instrument_id": 1, "open_time": "1", "closes": ["nan"]}]},
            "invalid points",
        ),
        (
            {"trends": [{"instrument_id": 1, "open_time": "1", "closes": "12"}]},
            "invalid points",
        ),
        (
            {"trends": [{"instrument_id": 1, "open_time": float("inf"), "closes": []}]},
            "invalid points",
        ),
        (
            {"trends": [{"instrument_id": 1, "open_time": True, "closes": [True]}]},
            "invalid points",
        ),
    ],
)
def test_trends_inspection_rejects_malformed_responses(response, message):
    with pytest.raises(BadResponse, match=message):
        summarize_trend(response, "1")


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_tickers_rejects_mini_shape_in_unified_method(bifu_ticker_server):
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        with pytest.raises(NotSupported, match="FULL"):
            await exchange.fetch_tickers(params={"type": "MINI"})
    finally:
        await exchange.close()

    assert bifu_ticker_server.calls == []


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ticker_rejects_unsupported_params_before_request(bifu_ticker_server):
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        with pytest.raises(NotSupported, match="does not accept params"):
            await exchange.fetch_ticker("BTC/USDT", {"unexpected": "value"})
    finally:
        await exchange.close()

    assert bifu_ticker_server.calls == []


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ticker_rejects_a_different_instrument(bifu_ticker_server):
    bifu_ticker_server.ticker_response["instrument_id"] = 90000002
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        with pytest.raises(BadResponse, match="instrument id"):
            await exchange.fetch_ticker("BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ticker_rejects_invalid_timestamp(bifu_ticker_server):
    bifu_ticker_server.ticker_response["ts"] = "not-a-timestamp"
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        with pytest.raises(BadResponse, match="timestamp"):
            await exchange.fetch_ticker("BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ticker_rejects_non_object_response(bifu_ticker_server):
    bifu_ticker_server.ticker_response = []
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        with pytest.raises(BadResponse, match="JSON object"):
            await exchange.fetch_ticker("BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ticker_keeps_empty_percentage_unknown(bifu_ticker_server):
    bifu_ticker_server.ticker_response["open_price"] = "0"
    bifu_ticker_server.ticker_response["price_change_percent"] = ""
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        ticker = await exchange.fetch_ticker("BTC/USDT")
    finally:
        await exchange.close()

    assert ticker["open"] == 0.0
    assert ticker["percentage"] is None
    assert ticker["average"] is None


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ticker_reports_a_market_with_no_trades(bifu_ticker_server):
    bifu_ticker_server.ticker_status = 404
    bifu_ticker_server.ticker_response = {"code": 6005, "message": "no trades yet"}
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        with pytest.raises(BadResponse, match="has no trades"):
            await exchange.fetch_ticker("BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_readonly_ticker_inspector_explains_the_standard_result(bifu_ticker_server):
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        result = await inspect_ticker("BTC/USDT", exchange=exchange)
    finally:
        await exchange.close()

    assert result == {
        "environment": "test",
        "symbol": "BTC/USDT",
        "instrument_id": "90000001",
        "timestamp": 1789712421923,
        "datetime": "2026-09-18T06:20:21.923Z",
        "last": 538944.47,
        "open": 538000.0,
        "high": 540000.0,
        "low": 530000.0,
        "base_volume": 0.44998,
        "quote_volume": 242966.07064,
        "change": 944.47,
        "percentage": 0.18,
        "bid": 538943.01,
        "bid_volume": 0.12,
        "ask": 538945.02,
        "ask_volume": 0.34,
        "book_timestamp": 1789712422000,
        "trend_open_time": 1789626000000,
        "trend_point_count": 2,
        "trend_first_close": 537000.01,
        "trend_last_close": 538944.47,
    }


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_readonly_ticker_inspector_retries_one_timeout(bifu_ticker_server):
    bifu_ticker_server.delay_ticker_first_seconds = 0.05
    exchange = new_bifu({"timeout": 10})
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_ticker_server.url
    try:
        result = await inspect_ticker("BTC/USDT", exchange=exchange, retry_delay=0)
    finally:
        await exchange.close()

    assert result["last"] == 538944.47
    assert [call[1] for call in bifu_ticker_server.calls] == [
        "/market/v1/meta",
        "/market/v1/ticker",
        "/market/v1/ticker",
        "/market/v1/bookTicker",
        "/market/v1/trends",
    ]
