import asyncio
from types import SimpleNamespace

import ccxt
import pytest
from aiohttp import web

from ccxt_cm import create_exchange
from examples.inspect_bifu_markets import inspect_market


def new_bifu(config=None):
    return create_exchange("bifu", config, mode="async")


@pytest.fixture
async def bifu_market_server(unused_tcp_port):
    state = SimpleNamespace(
        calls=[],
        depth={
            "instrument_id": 90000001,
            "last_id": "42",
            "book_time": "1700000000123",
            "bids": [{"price": "100", "qty": "2"}],
            "asks": [{"price": "102", "qty": "3"}],
        },
        klines={
            "klines": [
                {
                    "instrument_id": 90000001,
                    "period": "1m",
                    "open_time": "1700000000000",
                    "open": "100",
                    "high": "105",
                    "low": "99",
                    "close": "102",
                    "volume": "12.5",
                    "quote_volume": "1275",
                    "count": "7",
                    "closed": True,
                }
            ]
        },
        trades={
            "trades": [
                {
                    "instrument_id": 90000001,
                    "price": "0.1",
                    "qty": "0.2",
                    "taker_side": "BUY",
                    "ts": "1700000000456",
                }
            ]
        },
        response={
            "globals": {"revision": 1000, "trading": "OPEN"},
            "assets": [
                {"asset_id": 1, "code": "BTC", "name": "Bitcoin", "scale": 8},
                {"asset_id": 2, "code": "USDT", "name": "Tether", "scale": 8},
            ],
            "spots": [
                {
                    "instrument_id": 90000001,
                    "symbol": "BTC-USDT",
                    "base_asset_id": 1,
                    "quote_asset_id": 2,
                    "status": "TRADING",
                    "display_status": "LISTED",
                    "rules": {
                        "price_tick": "0.01",
                        "qty_step": "0.00001",
                        "min_qty": "0.00001",
                        "max_qty": "9000",
                        "min_notional": "5",
                        "max_notional": "1000000",
                    },
                    "taker_fee_rate": "0.0005",
                    "maker_fee_rate": "0.0002",
                }
            ],
        },
        delay_first_seconds=0,
    )

    async def meta(request):
        state.calls.append((request.method, request.path, dict(request.query)))
        if state.delay_first_seconds:
            delay = state.delay_first_seconds
            state.delay_first_seconds = 0
            await asyncio.sleep(delay)
        return web.json_response(state.response)

    async def depth(request):
        state.calls.append((request.method, request.path, dict(request.query)))
        return web.json_response(state.depth)

    async def klines(request):
        state.calls.append((request.method, request.path, dict(request.query)))
        return web.json_response(state.klines)

    async def trades(request):
        state.calls.append((request.method, request.path, dict(request.query)))
        return web.json_response(state.trades)

    app = web.Application()
    app.router.add_get("/market/v1/meta", meta)
    app.router.add_get("/market/v1/depth", depth)
    app.router.add_get("/market/v1/klines", klines)
    app.router.add_get("/market/v1/trades", trades)
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
async def test_load_markets_returns_standard_spot_market(bifu_market_server):
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_market_server.url
    try:
        markets = await exchange.load_markets()
    finally:
        await exchange.close()

    market = markets["BTC/USDT"]
    assert market["id"] == "90000001"
    assert market["info"]["symbol"] == "BTC-USDT"
    assert market["type"] == "spot"
    assert market["spot"] is True
    assert market["settle"] is None
    assert market["contractSize"] is None
    assert market["active"] is True
    assert market["precision"] == {"amount": 0.00001, "price": 0.01}
    assert market["limits"]["amount"] == {"min": 0.00001, "max": 9000.0}
    assert market["limits"]["cost"] == {"min": 5.0, "max": 1000000.0}
    assert bifu_market_server.calls == [("GET", "/market/v1/meta", {})]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_order_book_returns_ccxt_order_book(bifu_market_server):
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_market_server.url
    try:
        book = await exchange.fetch_order_book("BTC/USDT", 20)
    finally:
        await exchange.close()

    assert book == {
        "symbol": "BTC/USDT",
        "bids": [[100.0, 2.0]],
        "asks": [[102.0, 3.0]],
        "timestamp": 1700000000123,
        "datetime": "2023-11-14T22:13:20.123Z",
        "nonce": 42,
    }
    assert bifu_market_server.calls == [
        ("GET", "/market/v1/meta", {}),
        (
            "GET",
            "/market/v1/depth",
            {"instrument_id": "90000001", "limit": "20"},
        ),
    ]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_ohlcv_returns_ccxt_candles(bifu_market_server):
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_market_server.url
    try:
        candles = await exchange.fetch_ohlcv("BTC/USDT", "1m", limit=10)
    finally:
        await exchange.close()

    assert candles == [[1700000000000, 100.0, 105.0, 99.0, 102.0, 12.5]]
    assert bifu_market_server.calls[-1] == (
        "GET",
        "/market/v1/klines",
        {"instrument_id": "90000001", "limit": "10", "period": "1m"},
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_fetch_trades_returns_ccxt_trades(bifu_market_server):
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_market_server.url
    try:
        trades = await exchange.fetch_trades("BTC/USDT", limit=25)
    finally:
        await exchange.close()

    assert len(trades) == 1
    assert trades[0] == {
        "id": None,
        "order": None,
        "info": bifu_market_server.trades["trades"][0],
        "timestamp": 1700000000456,
        "datetime": "2023-11-14T22:13:20.456Z",
        "symbol": "BTC/USDT",
        "type": None,
        "side": "buy",
        "takerOrMaker": "taker",
        "price": 0.1,
        "amount": 0.2,
        "cost": 0.02,
        "fee": {"cost": None, "currency": None},
        "fees": [],
    }
    assert bifu_market_server.calls[-1] == (
        "GET",
        "/market/v1/trades",
        {"instrument_id": "90000001", "limit": "25"},
    )


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(
    ("method", "response"),
    [
        ("fetch_order_book", "depth"),
        ("fetch_ohlcv", "klines"),
        ("fetch_trades", "trades"),
    ],
)
async def test_public_market_methods_reject_a_different_instrument(
    bifu_market_server, method, response
):
    if response == "depth":
        bifu_market_server.depth["instrument_id"] = 999
    else:
        bifu_market_server.__dict__[response][response][0]["instrument_id"] = 999
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_market_server.url
    try:
        with pytest.raises(ccxt.BadResponse, match="instrument id"):
            await getattr(exchange, method)("BTC/USDT")
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_load_markets_rejects_asset_without_code(bifu_market_server):
    del bifu_market_server.response["assets"][0]["code"]
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_market_server.url
    try:
        with pytest.raises(ccxt.BadResponse, match="asset code"):
            await exchange.load_markets()
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_load_markets_rejects_unsupported_params_before_request(bifu_market_server):
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_market_server.url
    try:
        with pytest.raises(ccxt.NotSupported, match="does not accept params"):
            await exchange.load_markets(True, {"unexpected": "value"})
    finally:
        await exchange.close()

    assert bifu_market_server.calls == []


async def test_public_query_uses_ccxt_encoding_and_stable_order():
    exchange = new_bifu()
    try:
        exchange.set_sandbox_mode(True)

        signed = exchange.sign(
            "market/v1/meta",
            params={"q": "a b+/", "enabled": True},
        )

        assert signed["url"] == (
            "https://flame-api.bifu.dev/market/v1/meta?enabled=true&q=a%20b%2B%2F"
        )
    finally:
        await exchange.close()


async def test_load_markets_fails_when_production_url_is_closed():
    exchange = new_bifu()
    try:
        with pytest.raises(ccxt.BadRequest, match="URL is not configured"):
            await exchange.load_markets()
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(
    ("response", "message"),
    [
        ([], "JSON object"),
        ({}, "missing assets or spots"),
        ({"assets": [None], "spots": []}, "asset entry"),
        ({"assets": [], "spots": ["invalid"]}, "spot entry"),
        (
            {
                "assets": [{"asset_id": 2, "code": "USDT"}],
                "spots": [{"base_asset_id": 999, "quote_asset_id": 2}],
            },
            "unknown asset",
        ),
        (
            {
                "assets": [
                    {"asset_id": 1, "code": "BTC"},
                    {"asset_id": 2, "code": "USDT"},
                ],
                "spots": [{"base_asset_id": 1, "quote_asset_id": 2}],
            },
            "instrument id",
        ),
    ],
)
async def test_load_markets_rejects_malformed_metadata(bifu_market_server, response, message):
    bifu_market_server.response = response
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_market_server.url
    try:
        with pytest.raises(ccxt.BadResponse, match=message):
            await exchange.load_markets()
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(("status", "active"), [("HALTED", False), (None, None)])
async def test_load_markets_preserves_paused_and_unknown_status(bifu_market_server, status, active):
    spot = bifu_market_server.response["spots"][0]
    if status is None:
        del spot["status"]
    else:
        spot["status"] = status
    spot["margin"] = {"max_leverage": 10}
    exchange = new_bifu()
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_market_server.url
    try:
        markets = await exchange.load_markets()
    finally:
        await exchange.close()

    assert markets["BTC/USDT"]["active"] is active
    assert markets["BTC/USDT"]["margin"] is False


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_readonly_inspector_retries_one_timeout(bifu_market_server):
    bifu_market_server.delay_first_seconds = 0.05
    exchange = new_bifu({"timeout": 10})
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = bifu_market_server.url
    try:
        result = await inspect_market("BTC/USDT", exchange=exchange, retry_delay=0)
    finally:
        await exchange.close()

    assert result["market_count"] == 1
    assert len(bifu_market_server.calls) == 2
