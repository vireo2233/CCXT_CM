import asyncio
import hashlib
import hmac
from types import SimpleNamespace

import ccxt
import pytest
from aiohttp import WSMsgType, web

from ccxt_cm import create_exchange
from ccxt_cm.exchanges.bifu import BifuPro


@pytest.fixture
async def bifu_ws_server(unused_tcp_port):
    state = SimpleNamespace(
        calls=[],
        clients=[],
        clients_by_channel={},
        private_clients=[],
        tickers_clients=[],
        public_auth_headers=[],
        pongs=0,
        depth_gate=None,
        auth_failure_status=401,
    )

    ticker = {
        "instrument_id": 90000001,
        "last_price": "80437.97",
        "open_price": "81282",
        "high_price": "83140.67",
        "low_price": "79774.48",
        "volume": "749.54364",
        "quote_volume": "60763507.8554635",
        "count": "109906",
        "price_change": "-844.03",
        "price_change_percent": "-1.03",
        "ts": "1789903883826",
    }
    eth_ticker = {**ticker, "instrument_id": 90000002, "last_price": "2450.12"}
    frames = {
        "ticker": {"type": "ticker", "data": ticker},
        "trade": {
            "type": "trade",
            "data": {
                "instrument_id": 90000001,
                "price": "80437.97",
                "qty": "0.01689",
                "taker_side": "SELL",
                "ts": "1789903883826",
            },
        },
        "depth": {
            "type": "depth",
            "data": {
                "instrument_id": 90000001,
                "last_id": "11",
                "prev_id": "10",
                "book_time": "1789903883954",
                "event_time": "1789903884063",
                "bids": [{"price": "80438", "qty": "0.23"}],
                "asks": [{"price": "80439", "qty": "0.11"}],
            },
        },
        "book_ticker": {
            "type": "book_ticker",
            "data": {
                "instrument_id": 90000001,
                "bid_price": "80438",
                "bid_qty": "0.23",
                "ask_price": "80439",
                "ask_qty": "0.11",
                "last_id": "11",
                "book_time": "1789903883954",
            },
        },
        "kline": {
            "type": "kline",
            "data": {
                "instrument_id": 90000001,
                "period": "1m",
                "open_time": "1789903860000",
                "open": "80400",
                "high": "80450",
                "low": "80390",
                "close": "80437.97",
                "volume": "1.5",
                "closed": False,
            },
        },
    }

    async def meta(request):
        state.calls.append((request.path, dict(request.query)))
        return web.json_response(
            {
                "assets": [
                    {"asset_id": 1, "code": "BTC"},
                    {"asset_id": 2, "code": "USDT"},
                    {"asset_id": 3, "code": "ETH"},
                ],
                "spots": [
                    {
                        "instrument_id": 90000001,
                        "base_asset_id": 1,
                        "quote_asset_id": 2,
                        "status": "TRADING",
                        "rules": {},
                    },
                    {
                        "instrument_id": 90000002,
                        "base_asset_id": 3,
                        "quote_asset_id": 2,
                        "status": "TRADING",
                        "rules": {},
                    },
                ],
            }
        )

    async def depth(request):
        state.calls.append((request.path, dict(request.query)))
        if state.depth_gate is not None:
            await state.depth_gate.wait()
        return web.json_response(
            {
                "instrument_id": 90000001,
                "last_id": "10",
                "book_time": "1789903883850",
                "bids": [],
                "asks": [],
            }
        )

    async def stream(request):
        state.calls.append((request.path, dict(request.query)))
        state.public_auth_headers.append("X-API-KEY" in request.headers)
        ws = web.WebSocketResponse(autoping=False)
        await ws.prepare(request)
        state.clients.append(ws)
        channel = request.query["channels"]
        state.clients_by_channel[channel] = ws
        await ws.send_json(frames[channel])
        await ws.ping(b"bifu-heartbeat")
        async for message in ws:
            if message.type == WSMsgType.PONG:
                state.pongs += 1
            elif message.type == WSMsgType.PING:
                await ws.pong(message.data)
        return ws

    async def tickers(request):
        state.calls.append((request.path, dict(request.query)))
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state.clients.append(ws)
        state.tickers_clients.append(ws)
        batch = [ticker, eth_ticker] if len(state.tickers_clients) == 1 else [ticker]
        await ws.send_json(
            {
                "type": "tickers",
                "data": [*batch, {**ticker, "instrument_id": 99999999}],
            }
        )
        async for _ in ws:
            pass
        return ws

    def private_request_authenticated(request):
        timestamp = request.headers.get("X-TS", "")
        payload = "\n".join((timestamp, request.method, request.path, ""))
        expected = hmac.new(b"fixture-secret", payload.encode(), hashlib.sha256).hexdigest()
        return request.headers.get("X-API-KEY") == "fixture-key" and hmac.compare_digest(
            request.headers.get("X-SIGN", ""), expected
        )

    async def user_stream(request):
        authenticated = private_request_authenticated(request)
        state.calls.append((request.path, {"authenticated": str(authenticated).lower()}))
        if not authenticated:
            return web.Response(status=state.auth_failure_status)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state.clients.append(ws)
        state.private_clients.append(ws)
        async for _ in ws:
            pass
        return ws

    app = web.Application()
    app.router.add_get("/market/v1/meta", meta)
    app.router.add_get("/market/v1/depth", depth)
    app.router.add_get("/market/v1/stream", stream)
    app.router.add_get("/market/v1/stream/tickers", tickers)
    app.router.add_get("/spot/v1/userDataStream", user_stream)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    state.rest_url = f"http://127.0.0.1:{unused_tcp_port}"
    state.ws_url = f"ws://127.0.0.1:{unused_tcp_port}"
    try:
        yield state
    finally:
        for ws in state.clients:
            await ws.close()
        await runner.cleanup()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_watch_ticker_uses_bifu_url_subscription_and_returns_ccxt_ticker(bifu_ws_server):
    exchange = create_exchange("bifu", mode="pro")
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update({"public": bifu_ws_server.rest_url, "ws": bifu_ws_server.ws_url})
    try:
        ticker = await exchange.watch_ticker("BTC/USDT")
        for _ in range(100):
            if bifu_ws_server.pongs:
                break
            await asyncio.sleep(0.01)
    finally:
        await exchange.close()

    assert type(exchange) is BifuPro
    assert exchange.has["watchTicker"] is True
    assert ticker["symbol"] == "BTC/USDT"
    assert ticker["last"] == 80437.97
    assert ticker["timestamp"] == 1789903883826
    assert bifu_ws_server.pongs == 1
    assert bifu_ws_server.calls == [
        ("/market/v1/meta", {}),
        (
            "/market/v1/stream",
            {"instrument": "90000001", "channels": "ticker"},
        ),
    ]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_watch_public_market_streams_return_standard_ccxt_structures(bifu_ws_server):
    exchange = create_exchange("bifu", mode="pro")
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update({"public": bifu_ws_server.rest_url, "ws": bifu_ws_server.ws_url})
    try:
        trades = await exchange.watch_trades("BTC/USDT")
        book = await exchange.watch_order_book("BTC/USDT", 1)
        bids_asks = await exchange.watch_bids_asks(["BTC/USDT"])
        candles = await exchange.watch_ohlcv("BTC/USDT", "1m")
        tickers = await exchange.watch_tickers(["BTC/USDT"])
    finally:
        await exchange.close()

    assert trades[-1]["symbol"] == "BTC/USDT"
    assert trades[-1]["side"] == "sell"
    assert trades[-1]["price"] == 80437.97
    assert book["symbol"] == "BTC/USDT"
    assert book["nonce"] == 11
    assert book["bids"] == [[80438.0, 0.23]]
    assert bids_asks["BTC/USDT"]["ask"] == 80439.0
    assert candles[-1] == [1789903860000, 80400.0, 80450.0, 80390.0, 80437.97, 1.5]
    assert tickers["BTC/USDT"]["last"] == 80437.97
    assert ("/market/v1/depth", {"instrument_id": "90000001"}) in bifu_ws_server.calls
    stream_index = next(
        index
        for index, call in enumerate(bifu_ws_server.calls)
        if call[0] == "/market/v1/stream" and call[1].get("channels") == "depth"
    )
    snapshot_index = bifu_ws_server.calls.index(("/market/v1/depth", {"instrument_id": "90000001"}))
    assert stream_index < snapshot_index


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_watch_tickers_reconnect_drops_symbols_missing_from_fresh_batch(bifu_ws_server):
    exchange = create_exchange("bifu", mode="pro")
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update({"public": bifu_ws_server.rest_url, "ws": bifu_ws_server.ws_url})
    try:
        first = await exchange.watch_tickers()
        assert set(first) == {"BTC/USDT", "ETH/USDT"}
        await bifu_ws_server.tickers_clients[0].close()
        await asyncio.sleep(0.05)

        second = await asyncio.wait_for(exchange.watch_tickers(), 1)
    finally:
        await exchange.close()

    assert set(second) == {"BTC/USDT"}
    assert "ETH/USDT" not in exchange.tickers


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_watch_trades_honors_since_limit_and_new_updates(bifu_ws_server):
    exchange = create_exchange("bifu", mode="pro")
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update({"public": bifu_ws_server.rest_url, "ws": bifu_ws_server.ws_url})
    first_timestamp = 1789903883826
    try:
        exchange.newUpdates = False
        first = await exchange.watch_trades("BTC/USDT", since=first_timestamp, limit=1)
        second_waiter = asyncio.create_task(
            exchange.watch_trades("BTC/USDT", since=first_timestamp + 1, limit=1)
        )
        await asyncio.sleep(0.03)
        await bifu_ws_server.clients_by_channel["trade"].send_json(
            {
                "type": "trade",
                "data": {
                    "instrument_id": 90000001,
                    "price": "80438.00",
                    "qty": "0.01",
                    "taker_side": "BUY",
                    "ts": str(first_timestamp + 1000),
                },
            }
        )
        second = await asyncio.wait_for(second_waiter, 1)

        exchange.newUpdates = True
        third_waiter = asyncio.create_task(exchange.watch_trades("BTC/USDT", limit=1))
        await asyncio.sleep(0.03)
        await bifu_ws_server.clients_by_channel["trade"].send_json(
            {
                "type": "trade",
                "data": {
                    "instrument_id": 90000001,
                    "price": "80439.00",
                    "qty": "0.02",
                    "taker_side": "SELL",
                    "ts": str(first_timestamp + 2000),
                },
            }
        )
        third = await asyncio.wait_for(third_waiter, 1)
    finally:
        await exchange.close()

    assert len(first) == 1 and first[0]["timestamp"] == first_timestamp
    assert len(second) == 1 and second[0]["timestamp"] == first_timestamp + 1000
    assert len(third) == 1 and third[0]["timestamp"] == first_timestamp + 2000


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_depth_increment_updates_cache_and_gap_rejects_waiter(bifu_ws_server):
    exchange = create_exchange("bifu", {"enableRateLimit": False}, mode="pro")
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update({"public": bifu_ws_server.rest_url, "ws": bifu_ws_server.ws_url})
    try:
        await exchange.watch_order_book("BTC/USDT")
        next_book = asyncio.create_task(exchange.watch_order_book("BTC/USDT"))
        await asyncio.sleep(0.03)
        await bifu_ws_server.clients[-1].send_json(
            {
                "type": "depth",
                "data": {
                    "instrument_id": 90000001,
                    "last_id": "12",
                    "prev_id": "11",
                    "book_time": "1789903884254",
                    "bids": [{"price": "80438", "qty": "0"}],
                    "asks": [{"price": "80440", "qty": "0.5"}],
                },
            }
        )
        updated = await asyncio.wait_for(next_book, 1)
        assert updated["bids"] == []
        assert updated["asks"] == [[80439.0, 0.11], [80440.0, 0.5]]

        await bifu_ws_server.clients[-1].send_json(
            {
                "type": "depth",
                "data": {
                    "instrument_id": 90000001,
                    "last_id": "12",
                    "prev_id": "11",
                    "book_time": "1789903884254",
                    "bids": [{"price": "1", "qty": "99"}],
                    "asks": [],
                },
            }
        )
        await asyncio.sleep(0.03)
        assert exchange.orderbooks["BTC/USDT"]["nonce"] == 12
        assert list(exchange.orderbooks["BTC/USDT"]["bids"]) == []

        broken = asyncio.create_task(exchange.watch_order_book("BTC/USDT"))
        await asyncio.sleep(0.03)
        await bifu_ws_server.clients[-1].send_json(
            {
                "type": "depth",
                "data": {
                    "instrument_id": 90000001,
                    "last_id": "14",
                    "prev_id": "13",
                    "book_time": "1789903884354",
                    "bids": [],
                    "asks": [],
                },
            }
        )
        with pytest.raises(ccxt.InvalidNonce):
            await asyncio.wait_for(broken, 1)
        assert "BTC/USDT" not in exchange.orderbooks
        await asyncio.sleep(0.05)
        refreshed = await asyncio.wait_for(exchange.watch_order_book("BTC/USDT"), 1)
        assert refreshed["nonce"] == 11
        assert len(bifu_ws_server.clients) == 2
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_closing_ticker_stream_does_not_corrupt_live_depth_cache(bifu_ws_server):
    exchange = create_exchange("bifu", mode="pro")
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update({"public": bifu_ws_server.rest_url, "ws": bifu_ws_server.ws_url})
    try:
        await exchange.watch_ticker("BTC/USDT")
        await exchange.watch_order_book("BTC/USDT")
        await bifu_ws_server.clients_by_channel["ticker"].close()
        await asyncio.sleep(0.05)

        next_book = asyncio.create_task(exchange.watch_order_book("BTC/USDT"))
        await asyncio.sleep(0.03)
        await bifu_ws_server.clients_by_channel["depth"].send_json(
            {
                "type": "depth",
                "data": {
                    "instrument_id": 90000001,
                    "last_id": "12",
                    "prev_id": "11",
                    "book_time": "1789903884254",
                    "bids": [{"price": "80438", "qty": "0"}],
                    "asks": [{"price": "80440", "qty": "0.5"}],
                },
            }
        )
        updated = await asyncio.wait_for(next_book, 1)
    finally:
        await exchange.close()

    assert updated["nonce"] == 12
    assert updated["bids"] == []
    assert updated["asks"] == [[80439.0, 0.11], [80440.0, 0.5]]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_depth_disconnect_during_snapshot_cannot_restore_stale_book(bifu_ws_server):
    bifu_ws_server.depth_gate = asyncio.Event()
    exchange = create_exchange("bifu", {"enableRateLimit": False}, mode="pro")
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update({"public": bifu_ws_server.rest_url, "ws": bifu_ws_server.ws_url})
    try:
        first = asyncio.create_task(exchange.watch_order_book("BTC/USDT"))
        for _ in range(100):
            depth_client = bifu_ws_server.clients_by_channel.get("depth")
            snapshot_started = any(call[0] == "/market/v1/depth" for call in bifu_ws_server.calls)
            if depth_client is not None and snapshot_started:
                break
            await asyncio.sleep(0.01)
        await depth_client.close()
        with pytest.raises(ccxt.ExchangeNotAvailable, match="snapshot"):
            await asyncio.wait_for(first, 1)
        assert "BTC/USDT" not in exchange.orderbooks

        bifu_ws_server.depth_gate.set()
        refreshed = await asyncio.wait_for(exchange.watch_order_book("BTC/USDT"), 1)
    finally:
        bifu_ws_server.depth_gate.set()
        await exchange.close()

    assert refreshed["nonce"] == 11
    assert len(bifu_ws_server.clients) == 2


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_depth_buffer_is_bounded_while_snapshot_is_pending(bifu_ws_server):
    bifu_ws_server.depth_gate = asyncio.Event()
    exchange = create_exchange(
        "bifu",
        {"enableRateLimit": False, "options": {"depthBufferLimit": 2}},
        mode="pro",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update({"public": bifu_ws_server.rest_url, "ws": bifu_ws_server.ws_url})
    try:
        waiter = asyncio.create_task(exchange.watch_order_book("BTC/USDT"))
        for _ in range(100):
            depth_client = bifu_ws_server.clients_by_channel.get("depth")
            if depth_client is not None:
                break
            await asyncio.sleep(0.01)
        for last_id in (12, 13):
            await depth_client.send_json(
                {
                    "type": "depth",
                    "data": {
                        "instrument_id": 90000001,
                        "last_id": str(last_id),
                        "prev_id": str(last_id - 1),
                        "book_time": "1789903884254",
                        "bids": [],
                        "asks": [],
                    },
                }
            )
        with pytest.raises(ccxt.ExchangeNotAvailable, match="buffer exceeded"):
            await asyncio.wait_for(waiter, 1)
    finally:
        bifu_ws_server.depth_gate.set()
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_buffered_depth_gap_stops_replay_without_masking_invalid_nonce(bifu_ws_server):
    bifu_ws_server.depth_gate = asyncio.Event()
    exchange = create_exchange("bifu", {"enableRateLimit": False}, mode="pro")
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update({"public": bifu_ws_server.rest_url, "ws": bifu_ws_server.ws_url})
    try:
        waiter = asyncio.create_task(exchange.watch_order_book("BTC/USDT"))
        for _ in range(100):
            depth_client = bifu_ws_server.clients_by_channel.get("depth")
            if depth_client is not None:
                break
            await asyncio.sleep(0.01)
        for last_id, previous_id in ((13, 12), (14, 13)):
            await depth_client.send_json(
                {
                    "type": "depth",
                    "data": {
                        "instrument_id": 90000001,
                        "last_id": str(last_id),
                        "prev_id": str(previous_id),
                        "book_time": "1789903884254",
                        "bids": [],
                        "asks": [],
                    },
                }
            )
        bifu_ws_server.depth_gate.set()
        with pytest.raises(ccxt.InvalidNonce, match="gap"):
            await asyncio.wait_for(waiter, 1)
    finally:
        bifu_ws_server.depth_gate.set()
        await exchange.close()

    assert "BTC/USDT" not in exchange.orderbooks


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_malformed_depth_sequence_invalidates_book_and_reloads_snapshot(bifu_ws_server):
    exchange = create_exchange("bifu", {"enableRateLimit": False}, mode="pro")
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update({"public": bifu_ws_server.rest_url, "ws": bifu_ws_server.ws_url})
    try:
        await exchange.watch_order_book("BTC/USDT")
        waiter = asyncio.create_task(exchange.watch_order_book("BTC/USDT"))
        await asyncio.sleep(0.03)
        await bifu_ws_server.clients_by_channel["depth"].send_json(
            {
                "type": "depth",
                "data": {
                    "instrument_id": 90000001,
                    "prev_id": "11",
                    "book_time": "1789903884254",
                    "bids": [],
                    "asks": [],
                },
            }
        )
        with pytest.raises(ccxt.BadResponse, match="sequence"):
            await asyncio.wait_for(waiter, 1)
        assert "BTC/USDT" not in exchange.orderbooks
        await asyncio.sleep(0.05)

        refreshed = await asyncio.wait_for(exchange.watch_order_book("BTC/USDT"), 1)
    finally:
        await exchange.close()

    assert refreshed["nonce"] == 11


async def test_public_ws_rejects_unsupported_options_without_network():
    exchange = create_exchange("bifu", mode="pro")
    try:
        with pytest.raises(ccxt.NotSupported, match="1m"):
            await exchange.watch_ohlcv("BTC/USDT", "5m")
        with pytest.raises(ccxt.NotSupported, match="does not accept params"):
            await exchange.watch_ticker("BTC/USDT", {"unexpected": True})
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_private_ws_requires_credentials_before_authenticated_connection(bifu_ws_server):
    exchange = create_exchange("bifu", {"enableRateLimit": False}, mode="pro")
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update(
        {
            "public": bifu_ws_server.rest_url,
            "private": bifu_ws_server.rest_url,
            "ws": bifu_ws_server.ws_url,
        }
    )
    try:
        with pytest.raises(ccxt.AuthenticationError):
            await exchange.watch_balance()
    finally:
        await exchange.close()

    assert bifu_ws_server.calls == [("/market/v1/meta", {})]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_private_ws_rejects_invalid_server_authentication(bifu_ws_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "wrong-secret", "enableRateLimit": False},
        mode="pro",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update(
        {
            "public": bifu_ws_server.rest_url,
            "private": bifu_ws_server.rest_url,
            "ws": bifu_ws_server.ws_url,
        }
    )
    try:
        with pytest.raises(ccxt.AuthenticationError, match="authentication failed"):
            await exchange.watch_balance()
    finally:
        await exchange.close()

    assert ("/spot/v1/userDataStream", {"authenticated": "false"}) in bifu_ws_server.calls


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_private_ws_maps_forbidden_handshake_to_permission_denied(bifu_ws_server):
    bifu_ws_server.auth_failure_status = 403
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "wrong-secret", "enableRateLimit": False},
        mode="pro",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update(
        {
            "public": bifu_ws_server.rest_url,
            "private": bifu_ws_server.rest_url,
            "ws": bifu_ws_server.ws_url,
        }
    )
    try:
        with pytest.raises(ccxt.PermissionDenied, match="permission denied"):
            await exchange.watch_balance()
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_private_ws_rejects_order_update_without_order_id(bifu_ws_server):
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret", "enableRateLimit": False},
        mode="pro",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update(
        {
            "public": bifu_ws_server.rest_url,
            "private": bifu_ws_server.rest_url,
            "ws": bifu_ws_server.ws_url,
        }
    )
    try:
        waiter = asyncio.create_task(exchange.watch_orders("BTC/USDT"))
        for _ in range(100):
            if bifu_ws_server.private_clients:
                break
            await asyncio.sleep(0.01)
        await bifu_ws_server.private_clients[0].send_json(
            {
                "type": "order_update",
                "data": {"instrument_id": 90000001, "status": "NEW"},
            }
        )
        with pytest.raises(ccxt.BadResponse, match="order_id"):
            await asyncio.wait_for(waiter, 1)
    finally:
        await exchange.close()

    assert exchange.orders is None or len(exchange.orders) == 0


def test_malformed_ws_message_is_rejected_as_ccxt_bad_response():
    class FakeClient:
        def __init__(self):
            self.error = None

        def reject(self, error, message_hash=None):
            self.error = error

    exchange = create_exchange("bifu", mode="pro")
    client = FakeClient()

    exchange.handle_message(client, {"type": "ticker", "data": []})

    assert isinstance(client.error, ccxt.BadResponse)


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_unknown_ws_message_type_rejects_pending_waiter(bifu_ws_server):
    exchange = create_exchange("bifu", mode="pro")
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update({"public": bifu_ws_server.rest_url, "ws": bifu_ws_server.ws_url})
    try:
        await exchange.watch_ticker("BTC/USDT")
        waiter = asyncio.create_task(exchange.watch_ticker("BTC/USDT"))
        await asyncio.sleep(0.03)
        await bifu_ws_server.clients_by_channel["ticker"].send_json(
            {"type": "unknown", "data": {"instrument_id": 90000001}}
        )
        with pytest.raises(ccxt.BadResponse, match="unknown type"):
            await asyncio.wait_for(waiter, 1)
    finally:
        await exchange.close()


async def test_all_ws_methods_reject_undocumented_params_without_network():
    exchange = create_exchange("bifu", mode="pro")
    params = {"unsupported": True}
    try:
        calls = [
            exchange.watch_tickers(params=params),
            exchange.watch_trades("BTC/USDT", params=params),
            exchange.watch_order_book("BTC/USDT", params=params),
            exchange.watch_bids_asks(params=params),
            exchange.watch_ohlcv("BTC/USDT", params=params),
            exchange.watch_orders(params=params),
            exchange.watch_my_trades(params=params),
            exchange.watch_balance(params=params),
        ]
        for call in calls:
            with pytest.raises(ccxt.NotSupported, match="does not accept params"):
                await call
    finally:
        await exchange.close()


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_private_streams_use_signed_headers_without_leaking_them_to_public_ws(
    bifu_ws_server,
):
    exchange = create_exchange(
        "bifu",
        {
            "apiKey": "fixture-key",
            "secret": "fixture-secret",
            "enableRateLimit": False,
        },
        mode="pro",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update(
        {
            "public": bifu_ws_server.rest_url,
            "private": bifu_ws_server.rest_url,
            "ws": bifu_ws_server.ws_url,
        }
    )
    order = {
        "order_id": "order-123",
        "client_order_id": "client-123",
        "account_id": 7,
        "instrument_id": 90000001,
        "side": "BUY",
        "type": "LIMIT",
        "time_in_force": "POST_ONLY",
        "price": "100.10",
        "orig_qty": "0.2",
        "quote_qty": "0",
        "status": "PARTIALLY_FILLED",
        "filled_qty": "0.05",
        "cum_quote": "5.005",
        "created_ts": "1700000000000",
        "updated_ts": "1700000000500",
        "origin": "USER",
    }
    try:
        order_waiter = asyncio.create_task(exchange.watch_orders("BTC/USDT"))
        trades_waiter = asyncio.create_task(exchange.watch_my_trades("BTC/USDT"))
        balance_waiter = asyncio.create_task(exchange.watch_balance())
        for _ in range(100):
            if bifu_ws_server.private_clients:
                break
            if order_waiter.done():
                await order_waiter
            await asyncio.sleep(0.01)
        await exchange.watch_ticker("BTC/USDT")
        await bifu_ws_server.private_clients[0].send_json({"type": "order_update", "data": order})
        orders = await asyncio.wait_for(order_waiter, 1)
        first_order_status = orders[-1]["status"]

        await bifu_ws_server.private_clients[0].send_json(
            {
                "type": "order_update",
                "data": {
                    "order_id": order["order_id"],
                    "instrument_id": order["instrument_id"],
                    "status": "FILLED",
                    "filled_qty": "0.2",
                    "cum_quote": "20.02",
                    "updated_ts": "1700000001000",
                    "fill": {
                        "trade_id": "trade-456",
                        "price": "100.10",
                        "qty": "0.15",
                        "fee": "0.002",
                        "fee_asset_id": 2,
                        "is_maker": True,
                    },
                },
            }
        )
        trades = await asyncio.wait_for(trades_waiter, 1)

        await bifu_ws_server.private_clients[0].send_json(
            {
                "type": "balance_update",
                "data": {"asset_id": 2, "available": "100", "frozen": "5"},
            }
        )
        balance = await asyncio.wait_for(balance_waiter, 1)
    finally:
        await exchange.close()

    assert orders[-1]["id"] == "order-123"
    assert first_order_status == "open"
    assert orders[-1]["status"] == "closed"
    assert orders[-1]["filled"] == 0.2
    assert orders[-1]["side"] == "buy"
    assert orders[-1]["type"] == "limit"
    assert orders[-1]["price"] == 100.1
    assert orders[-1]["amount"] == 0.2
    assert trades[-1]["id"] == "trade-456"
    assert trades[-1]["order"] == "order-123"
    assert trades[-1]["symbol"] == "BTC/USDT"
    assert trades[-1]["side"] == "buy"
    assert trades[-1]["takerOrMaker"] == "maker"
    assert balance["USDT"] == {"free": 100.0, "used": 5.0, "total": 105.0}
    assert exchange.has["watchOrders"] is True
    assert exchange.has["watchBalance"] is True
    assert exchange.has["watchMyTrades"] is True
    assert (
        "/spot/v1/userDataStream",
        {"authenticated": "true"},
    ) in bifu_ws_server.calls
    assert bifu_ws_server.public_auth_headers == [False]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_private_stream_reconnects_with_fresh_authenticated_handshake(bifu_ws_server):
    exchange = create_exchange(
        "bifu",
        {
            "apiKey": "fixture-key",
            "secret": "fixture-secret",
            "enableRateLimit": False,
        },
        mode="pro",
    )
    exchange.set_sandbox_mode(True)
    exchange.urls["api"].update(
        {
            "public": bifu_ws_server.rest_url,
            "private": bifu_ws_server.rest_url,
            "ws": bifu_ws_server.ws_url,
        }
    )
    try:
        first = asyncio.create_task(exchange.watch_balance())
        for _ in range(100):
            if bifu_ws_server.private_clients:
                break
            await asyncio.sleep(0.01)
        await bifu_ws_server.private_clients[0].send_json(
            {"type": "balance_update", "data": {"asset_id": 2, "available": "10", "frozen": "1"}}
        )
        await asyncio.wait_for(first, 1)
        await bifu_ws_server.private_clients[0].close()
        await asyncio.sleep(0.05)

        second = asyncio.create_task(exchange.watch_balance())
        for _ in range(100):
            if len(bifu_ws_server.private_clients) == 2:
                break
            await asyncio.sleep(0.01)
        await bifu_ws_server.private_clients[1].send_json(
            {"type": "balance_update", "data": {"asset_id": 2, "available": "12", "frozen": "0"}}
        )
        balance = await asyncio.wait_for(second, 1)
    finally:
        await exchange.close()

    private_handshakes = [
        call
        for call in bifu_ws_server.calls
        if call == ("/spot/v1/userDataStream", {"authenticated": "true"})
    ]
    assert len(private_handshakes) == 2
    assert balance["USDT"] == {"free": 12.0, "used": 0.0, "total": 12.0}
