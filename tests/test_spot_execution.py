"""Exercise SDK evidence rules with synthetic, offline venue responses."""

import asyncio
import copy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import ccxt
import pytest

from ccxt_cm import create_exchange
from ccxt_cm.bybit_cash import cash_query, collect_bybit_transaction_pages, transaction_rows
from ccxt_cm.order_lookup import ClientOrderLookupUnconfirmed, OriginalLookup
from ccxt_cm.spot_execution import (
    BinanceSpot,
    BybitSpot,
    SpotExecution,
    SpotMarket,
    legacy_spot_market,
    managed_spot_market,
    managed_spot_markets,
    require_managed_spot,
    spot_execution,
)
from tests.test_order_lookup import MARKET, ROW, SYMBOL, payload


@pytest.fixture(params=["bybit", "binance"])
async def bound(request):
    name = request.param
    profile = spot_execution(name, "sandbox")
    client = create_exchange(
        name,
        {
            "apiKey": "fixture-key",
            "secret": "fixture-secret",
            "enableRateLimit": False,
            "options": {"defaultType": "spot"},
        },
    )
    profile.configure(client, "sandbox")
    client.set_sandbox_mode(True)
    profile.bind(client, "sandbox")
    client.set_markets([dict(MARKET, option=False, swap=False, future=False)])
    client.fetch = AsyncMock()
    try:
        yield profile, client
    finally:
        await client.close()


async def test_market_is_not_inferred_from_sdk_installation(monkeypatch):
    import ccxt_cm.spot_execution as module

    assert legacy_spot_market().exchange == "bybit"
    assert isinstance(spot_execution("bybit", None), BybitSpot)
    assert managed_spot_market("bybit", None) is None
    assert managed_spot_market("binance", "sandbox", "swap") is None
    assert managed_spot_market("binance", "mainnet") is None
    default = spot_execution("unknown-fixture-exchange", "sandbox")
    assert type(default) is SpotExecution
    assert managed_spot_market("bifu", "sandbox") is None
    assert managed_spot_market("binance", "sandbox").valid_uid("123")
    assert not managed_spot_market("binance", "sandbox").valid_uid("abc")

    class Registered(SpotExecution):
        market = SpotMarket("fixture", "Fixture", "demo", "Demo", "[A-Z][0-9]+")

    adapter = Registered()
    registry_class = SimpleNamespace(cm_spot_execution={"demo": adapter})
    monkeypatch.setattr(module, "exchange_class", lambda _: registry_class)
    monkeypatch.setattr(module, "list_exchanges", lambda: ["fixture"])
    assert adapter.market in managed_spot_markets()
    assert managed_spot_market("fixture", "demo") is adapter.market
    assert managed_spot_market("fixture", "sandbox") is None

    call = AsyncMock(return_value={"id": "1"})
    assert await default.fetch_original(
        None, call, SYMBOL, "1", "cid", None, 1, qualified=False
    ) == (
        {"id": "1"},
        False,
    )
    with pytest.raises(ccxt.NotSupported):
        await default.fetch_original(None, call, SYMBOL, None, "cid", None, 1, qualified=False)
    with pytest.raises(ccxt.NotSupported):
        await default.verify_account(None, "123", call)
    assert await default.open_directory(None, call, SYMBOL) is None
    assert await default.private_window(None, call, SYMBOL, 1, 10, 100) is None
    assert await default.public_window(None, SYMBOL, 1, 10) is None
    assert (await default.original_evidence(None, MARKET, None))[
        "outcome"
    ] == OriginalLookup.UNCONFIRMED
    assert await default.account_fee(None, call, MARKET, None) == {"id": "1"}
    assert default.order_diagnostic(ccxt.RequestTimeout())["reason"] == "response_unconfirmed"
    assert default.order_fields({}, {}, bound=False) == {}
    assert default.cancel_ack({}, SYMBOL, "1", bound=False) is None
    assert default.cancellation_evidence(None, {}, SYMBOL, bound=False) == {}
    assert default.stream_status(None) is None and default.wallet_stream_complete({})
    assert (
        default.wallet_fields(None, {}, environment=None, bound=False, identity=None, logger=None)
        == {}
    )
    assert default.trade_fields({}) == {}
    with pytest.raises(ccxt.NotSupported):
        await default.native_read(None, None, "unknown", {})
    with pytest.raises(ccxt.NotSupported):
        await default.price_band(None, MARKET)


async def test_existing_native_calls_can_be_used_without_enabling_optional_features(bound):
    profile, client = bound
    require_managed_spot(client)
    assert profile.market.environment == "sandbox"
    assert profile.market.public_trade_window == profile.market.limit_pair == (client.id == "bybit")
    if client.id == "binance":
        client.set_sandbox_mode(False)
        with pytest.raises(ccxt.NotSupported):
            profile.bind(client, "sandbox")
    client.fetch.assert_not_awaited()


@pytest.mark.parametrize("failure", ["identity", "permission", "wallet", "range", "fee"])
async def test_binance_rejects_incomplete_signed_evidence(failure):
    profile = BinanceSpot()
    client = SimpleNamespace(
        isSandboxModeEnabled=True,
        milliseconds=lambda: 100,
        timeout=1000,
        safe_currency_code=str,
        check_required_credentials=lambda: None,
        urls={"api": dict.fromkeys(("public", "private"), "https://testnet.binance.vision/api/v3")},
    )
    account = {
        "accountType": "SPOT",
        "uid": 123,
        "canTrade": True,
        "updateTime": 99,
        "balances": [{"asset": "BTC", "free": "2", "locked": "1"}],
    }
    raw = {"info": account}
    call = AsyncMock(return_value=raw)
    assert (await profile.verify_account(client, "123", call))["uid"] == "123"
    assert profile.wallet_fields(
        client, raw, environment="sandbox", bound=True, identity={"uid": "123"}, logger=None
    )["total"] == {"BTC": Decimal(3)}
    if failure in {"identity", "permission"}:
        account["canTrade"] = failure != "permission"
        with pytest.raises(ccxt.PermissionDenied):
            await profile.verify_account(client, "456" if failure == "identity" else "123", call)
    if failure == "wallet":
        with pytest.raises(ccxt.PermissionDenied):
            profile.wallet_fields(
                client, raw, environment="sandbox", bound=True, identity={"uid": "456"}, logger=None
            )
        account["balances"] *= 2
        with pytest.raises(ccxt.ExchangeError):
            profile.wallet_fields(
                client, raw, environment="sandbox", bound=True, identity=None, logger=None
            )
    if failure == "range":
        with pytest.raises(ccxt.ExchangeError):
            await profile.private_window(client, call, SYMBOL, 10, 10, 2)
    if failure == "fee":
        client.private_get_account_commission = AsyncMock(return_value={"symbol": "WRONG"})
        with pytest.raises(ccxt.ExchangeError):
            await profile.account_fee(client, call, MARKET, asyncio.Semaphore(1))


@pytest.mark.parametrize("saturated", [False, True])
async def test_binance_private_window_proves_every_split_or_fails_closed(saturated):
    client = SimpleNamespace(
        isSandboxModeEnabled=True,
        timeout=1000,
        urls={"api": dict.fromkeys(("public", "private"), "https://testnet.binance.vision/api/v3")},
    )
    profile = BinanceSpot()
    fills = [
        {"order": "42", "id": str(i), "symbol": SYMBOL, "timestamp": at}
        for i, at in enumerate((10, 10 if saturated else 14, 17))
    ]
    windows = []

    async def read(method, symbol, *, private, params):
        assert (method, symbol, private) == ("fetch_my_trades", SYMBOL, True)
        lo, end = params["startTime"], params["endTime"]
        windows.append((lo, end))
        return [row for row in fills if lo <= row["timestamp"] <= end][: params["limit"]]

    if saturated:
        with pytest.raises(ccxt.ExchangeError, match="saturated"):
            await profile.private_window(client, read, SYMBOL, 10, 18, 2)
    else:
        assert await profile.private_window(client, read, SYMBOL, 10, 18, 2) == (fills, True, 18)
        assert windows == [(10, 17), (10, 13), (14, 17), (14, 15), (16, 17)]


@pytest.mark.parametrize("bound", ["binance"], indirect=True)
@pytest.mark.parametrize("response", ["order", "absent", "timeout", "foreign"])
async def test_binance_original_and_cancel_never_adopt_an_unrelated_id(bound, response):
    profile, client = bound
    cid, oid = "original-1", "42"
    order = {"id": oid, "clientOrderId": cid, "symbol": SYMBOL, "status": "open"}
    cancel = {
        "id": oid,
        "clientOrderId": profile.cancel_id(SYMBOL, oid, cid),
        "symbol": SYMBOL,
        "status": "canceled",
    }
    cancel["info"] = {
        "origClientOrderId": cid,
        "clientOrderId": profile.cancel_id(SYMBOL, oid, cid),
    }
    call = AsyncMock(return_value=order)
    slots = asyncio.Semaphore(1)
    if response == "absent":
        call.side_effect = ccxt.OrderNotFound('fixture {"code":-2013}')
        with pytest.raises(ClientOrderLookupUnconfirmed) as absent:
            await profile.fetch_original(client, call, SYMBOL, None, cid, slots, 1, qualified=True)
        assert absent.value.outcome == OriginalLookup.ABSENT
        call.side_effect = ccxt.OrderNotFound("unstructured")
        with pytest.raises(ClientOrderLookupUnconfirmed) as unknown:
            await profile.fetch_original(client, call, SYMBOL, None, cid, slots, 1, qualified=True)
        assert unknown.value.outcome == OriginalLookup.UNCONFIRMED
    elif response == "timeout":
        call.side_effect = ccxt.RequestTimeout()
        with pytest.raises(ccxt.RequestTimeout):
            await profile.fetch_original(client, call, SYMBOL, None, cid, slots, 1, qualified=True)
        assert call.await_count == 1
    elif response == "foreign":
        call.side_effect = [order, dict(cancel, info={})]
        with pytest.raises(ccxt.ExchangeError):
            await profile.cancel_original(client, call, SYMBOL, oid)
        assert call.await_count == 2  # No write retry.
    else:
        assert await profile.fetch_original(
            client, call, SYMBOL, None, cid, slots, 1, qualified=True
        ) == (order, True)
        call.side_effect = [order, cancel]
        assert (await profile.cancel_original(client, call, SYMBOL, oid))["clientOrderId"] == cid
        alias = cancel["info"]["clientOrderId"]
        call.side_effect = None
        call.return_value = dict(cancel, clientOrderId=alias)
        normalized, _ = await profile.fetch_original(
            client, call, SYMBOL, oid, cid, slots, 1, qualified=True
        )
        assert normalized["clientOrderId"] == cid
        terminal = await profile.cancel_original(client, call, SYMBOL, oid)
        assert terminal["status"] == "canceled"


@pytest.mark.parametrize("fault", [None, "duplicate", "loop", "incomplete", "timeout"])
async def test_bybit_native_spot_pages_require_terminal_and_unique_rows(fault):
    profile = BybitSpot()
    pages = [payload([ROW], nextPageCursor="next"), payload([dict(ROW, orderId="second")])]
    if fault == "duplicate":
        pages = [payload([ROW, ROW])]
    elif fault == "loop":
        pages[1]["result"]["nextPageCursor"] = "next"
    elif fault == "incomplete":
        del pages[1]["result"]["nextPageCursor"]
    elif fault == "timeout":
        pages[1] = TimeoutError()
    fetch = AsyncMock(side_effect=pages)
    query = {"limit": 2}
    if fault:
        with pytest.raises(ValueError):
            await profile.read_native_pages(fetch, "private_get_v5_order_history", query, timeout=1)
    else:
        rows, proof = await profile.read_native_pages(
            fetch, "private_get_v5_order_history", query, timeout=1
        )
        assert [r["orderId"] for r in rows] == [ROW["orderId"], "second"]
        assert [p["cursor"] for p in proof["pages"]] == [None, "next"]


@pytest.mark.parametrize("fault", [None, "stale", "error", "found", "wrong_id"])
async def test_bybit_uncached_three_read_audit_produces_typed_outcome(fault, monkeypatch):
    from ccxt_cm import spot_execution as module

    profile = BybitSpot()
    client = SimpleNamespace(parse_order=lambda row, _: row, parse_trade=lambda row, _: row)
    monkeypatch.setattr(module, "_require_bybit_testnet_spot", lambda _: None)
    seen = []

    async def read(method, query):
        seen.append((method, query))
        if len(seen) == 2 and fault == "error":
            raise ValueError("fixture incomplete")
        rows = []
        if len(seen) == 2 and fault in {"found", "wrong_id"}:
            rows.append(
                dict(ROW, createdTime="120", orderLinkId="cid" if fault == "found" else "wrong")
            )
        return rows, {"pages": [{"response": {"time": 140 if fault == "stale" else 150}}]}

    result = await profile.original_evidence(
        client, MARKET, read, client_order_id="cid", start_ms=100, end_ms=145
    )
    assert len(seen) == 3 and all(q["orderLinkId"] == "cid" for _, q in seen)
    assert result["outcome"] == (
        OriginalLookup.ABSENT
        if fault is None
        else OriginalLookup.FOUND
        if fault == "found"
        else OriginalLookup.UNCONFIRMED
    )
    assert ("absence" in result) == (fault is None)


@pytest.mark.parametrize("fault", [None, "null", "shape", "loop", "empty_continuation", "read"])
async def test_bybit_cash_transport_proves_original_cursor_chain(fault):
    def page(rows, cursor):
        return {"retCode": 0, "result": {"list": rows, "nextPageCursor": cursor}}

    if fault in (None, "null"):
        pages = [page([{"id": "one"}], "next"), page([], None if fault else "")]
    elif fault == "shape":
        pages = [{"retCode": 0, "result": {"list": []}}]
    elif fault == "loop":
        pages = [page([{"id": "one"}], "next")] * 2
    elif fault == "empty_continuation":
        pages = [page([], "next")]
    else:
        pages = [TimeoutError()]
    fetch = AsyncMock(side_effect=pages)
    if fault not in (None, "null"):
        with pytest.raises(ValueError):
            await collect_bybit_transaction_pages(fetch, start_ms=100, end_ms=200)
    else:
        evidence = await collect_bybit_transaction_pages(fetch, start_ms=100, end_ms=200)
        assert fetch.await_args_list[0].args[0] == cash_query(100, 200)
        assert transaction_rows(evidence, 100, 200) == [{"id": "one"}]
        malformed = copy.deepcopy(evidence)
        malformed["pages"].pop()
        with pytest.raises(ValueError):
            transaction_rows(malformed, 100, 200)


@pytest.mark.parametrize("bound", ["bybit"], indirect=True)
async def test_bybit_original_uses_same_oid_or_verified_exact_client_id(bound):
    profile, client = bound
    oid, cid = ROW["orderId"], ROW["orderLinkId"]
    call = AsyncMock(return_value={"id": oid})
    slots = asyncio.Semaphore(1)
    assert await profile.fetch_original(
        client,
        call,
        SYMBOL,
        oid,
        cid,
        slots,
        1,
        qualified=False,
    ) == ({"id": oid}, False)
    call.assert_awaited_once_with(
        "fetch_order", oid, SYMBOL, private=True, params={"acknowledged": True}
    )
    call.side_effect = ccxt.OrderNotFound()
    client.fetch.return_value = payload([ROW])
    original, verified = await profile.fetch_original(
        client,
        call,
        SYMBOL,
        oid,
        cid,
        slots,
        1,
        qualified=True,
    )
    assert verified and original["id"] == oid and original["clientOrderId"] == cid
    with pytest.raises(ccxt.OrderNotFound):
        await profile.fetch_original(client, call, SYMBOL, oid, cid, slots, 1, qualified=False)
    raw = {"id": oid, "info": {"orderId": oid, "orderLinkId": cid}}
    assert profile.cancel_ack(raw, SYMBOL, oid, bound=True) == cid
    assert profile.cancel_ack(raw, SYMBOL, oid, bound=False) is None
    with pytest.raises(ValueError):
        profile.cancel_ack(raw, SYMBOL, "foreign", bound=True)
    assert profile.cancel_ack_evidence(oid, cid)["bybit_cancel_ack"]["client_order_id"] == cid
    assert await profile.cancel_original(client, AsyncMock(return_value=raw), SYMBOL, oid) == raw


@pytest.mark.parametrize("bound", ["bybit"], indirect=True)
async def test_bybit_verified_and_legacy_private_windows_keep_their_evidence(bound):
    profile, client = bound
    trade = {
        "symbol": MARKET["id"],
        "orderId": "42",
        "orderLinkId": "cid",
        "execId": "99",
        "execTime": "120",
        "execType": "Trade",
        "side": "Buy",
        "execQty": "0.4",
        "execPrice": "10",
        "execValue": "4",
        "feeCurrency": "BTC",
        "execFee": "0.0004",
    }
    native = AsyncMock(return_value=payload([trade]))
    call = AsyncMock(side_effect=AssertionError("managed private path must prove terminal page"))
    parsed, complete, end = await profile.private_window(
        client,
        call,
        SYMBOL,
        100,
        150,
        10,
        native=native,
    )
    assert complete and end == 150 and [r["id"] for r in parsed] == ["99"]
    assert profile.trade_fields(parsed[0])["fees"] == [{"currency": "BTC", "cost": "0.0004"}]
    assert not profile.trade_fields(dict(parsed[0], info=dict(trade, extraFees=[1])))["fees"]
    with pytest.raises(ValueError):
        await profile.private_window(client, call, SYMBOL, 150, 150, 10, native=native)
    native.return_value = payload([dict(trade, execTime="99")])
    with pytest.raises(ccxt.ExchangeError):
        await profile.private_window(client, call, SYMBOL, 100, 150, 10, native=native)
    # Keep the preexisting unified path outside verified managed accounts.
    first = dict(parsed[0], info=dict(trade, nextPageCursor="n"))
    last = dict(parsed[0], info=dict(trade, nextPageCursor=""))
    call = AsyncMock(side_effect=[[first], [last]])
    merged, complete, _ = await profile.private_window(client, call, SYMBOL, 100, 150, 10)
    assert complete and merged == [first]
    assert call.await_args_list[1].kwargs["params"]["cursor"] == "n"
    call.side_effect = None
    call.return_value = [dict(parsed[0], info=trade)]
    with pytest.raises(ccxt.ExchangeError):
        await profile.private_window(client, call, SYMBOL, 100, 150, 10)
    call.return_value = []
    assert await profile.private_window(client, call, SYMBOL, None, 150, 10) == ([], False, 150)


@pytest.mark.parametrize("field", ["valid", "debt", "equity", "interest", "isolated"])
def test_bybit_wallet_keeps_debt_and_freshness_guards(field):
    profile = BybitSpot()
    coin = dict.fromkeys(
        (
            "spotBorrow",
            "borrowAmount",
            "totalOrderIM",
            "totalPositionIM",
            "totalPositionMM",
            "unrealisedPnl",
            "bonus",
            "spotHedgingQty",
            "accruedInterest",
        ),
        "0",
    )
    coin.update(coin="BTC", walletBalance="5", locked="2", equity="5")
    account = {
        "accountType": "UNIFIED",
        "coin": [coin],
        "totalPerpUPL": "0",
        "totalInitialMargin": "",
        "totalMaintenanceMargin": "",
    }
    raw = {"info": [account], "timestamp": 100}
    identity = {"margin_mode": "ISOLATED_MARGIN"} if field == "isolated" else None
    if field == "debt":
        coin["spotBorrow"] = "1"
    elif field == "equity":
        coin["equity"] = "4"
    elif field in ("interest", "isolated"):
        coin["accruedInterest"] = ""
        if field == "interest":
            account["totalInitialMargin"] = "1"
    client = SimpleNamespace(safe_currency_code=str)
    args = {"environment": "sandbox", "bound": True, "identity": identity, "logger": None}
    if field in ("valid", "isolated"):
        data = profile.wallet_fields(client, raw, **args)
        assert data == {
            "free": {"BTC": 3},
            "used": {"BTC": 2},
            "total": {"BTC": 5},
            "timestamp": 100,
        }
        assert profile.wallet_stream_complete(raw)
        assert not profile.wallet_stream_complete(dict(raw, timestamp=None))
        del coin["locked"]
        assert not profile.wallet_stream_complete(raw)
    else:
        with pytest.raises(ccxt.ExchangeError):
            profile.wallet_fields(client, raw, **args)


@pytest.mark.parametrize("bound", ["bybit"], indirect=True)
async def test_bybit_native_audits_preserve_existing_order_and_cash_windows(bound):
    profile, client = bound
    client.fetch.return_value = payload([])
    slots = asyncio.Semaphore(1)
    raw = await profile.native_read(
        client, slots, "private_get_v5_order_history", {"category": "spot", "symbol": MARKET["id"]}
    )
    assert raw["result"]["nextPageCursor"] == ""
    with pytest.raises(ValueError):
        await profile.native_read(client, slots, "private_post_v5_order_create", {})
    data = [dict(ROW, createdTime="120"), dict(ROW, orderId="other", orderLinkId="other")]
    reader = AsyncMock(return_value=(data, {"pages": []}))
    history = await profile.run_order_history(
        client, MARKET, reader, prefix=ROW["orderLinkId"], start_ms=100, end_ms=150
    )
    assert set(history["orders"]) == {ROW["orderLinkId"]}
    reader.return_value = ([dict(ROW, symbol="OTHER")], {"pages": []})
    with pytest.raises(ValueError, match="history_unconfirmed"):
        await profile.run_order_history(
            client, MARKET, reader, prefix=ROW["orderLinkId"], start_ms=100, end_ms=150
        )
    reader.return_value = (
        [
            {"orderId": "42", "execId": "99", "execTime": "100"},
            {"orderId": "43", "execId": "98", "execTime": "130"},
        ],
        {"pages": []},
    )
    fetch = AsyncMock(return_value={"retCode": 0, "result": {"list": [], "nextPageCursor": ""}})
    executions, _, cash = await profile.cash_windows(
        MARKET, reader, fetch, start_ms=100, end=150, timeout=1
    )
    assert [row["execId"] for row in executions] == ["98"]
    assert transaction_rows(cash, 100, 150) == []


def test_bybit_private_stream_requires_a_current_authenticated_session():
    profile = BybitSpot()
    auth = SimpleNamespace(done=lambda: True, cancelled=lambda: False, result=lambda: True)
    ws = SimpleNamespace(
        url="wss://fixture/v5/private",
        connectionEstablished=10,
        futures={"authenticated": auth},
        lastPong=130,
        last_message_at=120,
        isConnected=True,
        error=None,
        connection=SimpleNamespace(closed=False),
        rejections={},
        subscriptions={"balances:unified": 1, "orders:spot": 1, "myTrades:spot": 1},
        keepAlive=20,
        maxPingPongMisses=2,
    )
    client = SimpleNamespace(
        clients={"public": SimpleNamespace(url="wss://fixture/public"), "private": ws},
        milliseconds=lambda: 140,
    )
    assert profile.stream_status(client) == {"connected": True, "session": (id(ws), 10)}
    ws.rejections["old"] = "denied"
    assert not profile.stream_status(client)["connected"]
    assert not profile.stream_status(None)["connected"]


@pytest.mark.parametrize("bound", ["binance"], indirect=True)
async def test_binance_core_fee_and_order_directory_do_not_claim_missing_facts(bound):
    profile, client = bound
    component = {"maker": "0.001", "taker": "0.002", "buyer": "0.0001", "seller": "0.0002"}
    zero = dict.fromkeys(component, "0")
    commission = {
        "symbol": MARKET["id"],
        "standardCommission": component,
        "specialCommission": zero,
        "taxCommission": zero,
    }
    client.fetch.return_value = commission
    slots, call = asyncio.Semaphore(1), AsyncMock(return_value=[])
    fees = await profile.account_fee(client, call, MARKET, slots)
    assert fees["maker"] == Decimal("0.0012") and fees["taker"] == Decimal("0.0022")
    component["buyer"] = "NaN"
    with pytest.raises(ValueError, match="fee evidence"):
        await profile.account_fee(client, call, MARKET, slots)
    assert await profile.open_directory(client, call, SYMBOL) == []
    call.assert_awaited_once_with("fetch_open_orders", SYMBOL, private=True)
    assert await profile.private_window(client, call, SYMBOL, None, None, 5) == ([], False, None)
    for rows in ({}, [None], [{"order": "42", "id": "99", "timestamp": 80, "symbol": SYMBOL}]):
        call.return_value = rows
        with pytest.raises(ccxt.ExchangeError):
            await profile.private_window(client, call, SYMBOL, 90, 100, 5)
    row = {
        "info": {"price": "1.1", "qty": "3", "quoteQty": "3.3", "commission": "0.004"},
        "fee": {"currency": "BTC", "cost": 0.004},
    }
    assert profile.trade_fields(row)["fees"] == [{"currency": "BTC", "cost": "0.004"}]
    assert profile.trade_fields(row)["cost"] == "3.3"
    assert profile.trade_fields({"fee": None}) == {"fee": None}


@pytest.mark.parametrize("bound", ["bybit"], indirect=True)
async def test_bybit_normalized_cancellation_and_optional_reads(bound, monkeypatch):
    import ccxt_cm.spot_execution as module

    profile, client = bound
    info = dict(ROW, cancelType="CancelByUser")
    raw = client.parse_order(info, MARKET)
    raw["lastUpdateTimestamp"] = int(info["updatedTime"])
    assert (
        profile.cancellation_evidence(client, raw, SYMBOL, bound=True)["bybit_order_cancellation"][
            "cancel_type"
        ]
        == "CancelByUser"
    )
    for key, val in (
        ("orderId", "wrong"),
        ("cumExecQty", "-1"),
        ("updatedTime", "none"),
        ("updatedTime", "1700000000020"),
        ("side", "Sell"),
        ("qty", "bad"),
    ):
        changed = dict(raw, info=dict(info, **{key: val}))
        assert profile.cancellation_evidence(client, changed, SYMBOL, bound=True) == {}
    with pytest.raises(ValueError):
        profile.order_fields(dict(raw, timeInForce="FOK"), {}, bound=True)
    market_intent = {
        "type": "market",
        "amount": Decimal("2"),
        "params": profile.market_order_params("amm-b-cid"),
    }
    market_row = {
        "type": "market",
        "clientOrderId": "amm-b-cid",
        "info": {"orderType": "Market", "marketUnit": "baseCoin", "qty": "2"},
    }
    assert profile.order_fields(market_row, {}, bound=True)["amount"] == "2"
    assert profile.order_fields({"type": "market"}, market_intent, bound=True)["price"] is None
    market_row["info"]["marketUnit"] = "quoteCoin"
    with pytest.raises(ValueError):
        profile.order_fields(market_row, {}, bound=True)

    fee = {"symbol": MARKET["id"], "makerFeeRate": "0.001", "takerFeeRate": "0.002"}
    call = AsyncMock(return_value={"info": fee})
    data = await profile.account_fee(client, call, MARKET, None)
    assert data["taker"] == Decimal("0.002") and data["percentage"] is True
    fee["symbol"] = "foreign"
    with pytest.raises(ccxt.ExchangeError):
        await profile.account_fee(client, call, MARKET, None)
    client.fetch.return_value = payload([])
    assert await profile.open_directory(client, call, SYMBOL) == []
    monkeypatch.setattr(
        module, "fetch_bybit_testnet_spot_price_band", AsyncMock(return_value=({}, {}))
    )
    market = dict(MARKET, info={"lotSizeFilter": {"maxMarketOrderQty": "1"}})
    band, _ = await profile.price_band(client, market)
    assert band["max_market_amount"] == Decimal(1)
    with pytest.raises(ValueError):
        await profile.price_band(client, MARKET)
    public = {
        "native_response": payload(
            [{"execId": "1", "time": "100", "price": "2", "size": "1", "side": "Buy"}]
        ),
        "complete": True,
    }
    monkeypatch.setattr(
        module, "fetch_bybit_testnet_spot_public_window", AsyncMock(return_value=public)
    )
    rows, complete, _ = await profile.public_window(client, SYMBOL, 90, 110)
    assert complete and rows[0]["side"] == "buy"


async def test_bybit_matching_context_uses_exact_account_mode_and_live_settings(monkeypatch):
    from ccxt_cm import account_guards

    profile = BybitSpot()
    current = {"unified_margin_status": 5, "margin_mode": "ISOLATED_MARGIN"}
    identity = dict(
        current,
        environment="sandbox",
        market_type="spot",
        uid="42",
        spot_price_auto_adjustment=False,
        spot_mnt_fee_deduction=False,
    )
    assert profile.matching_identity(identity)
    assert not profile.matching_identity(dict(identity, spot_price_auto_adjustment=True))
    mode = AsyncMock(return_value=current)
    settings = {"smp_type": 0, "smp_group": 0, "source_ms": 100}
    monkeypatch.setattr(account_guards, "fetch_bybit_testnet_spot_account_mode", mode)
    monkeypatch.setattr(
        account_guards, "fetch_bybit_testnet_spot_smp_settings", AsyncMock(return_value=settings)
    )
    client = SimpleNamespace(milliseconds=lambda: 105)
    await profile.verify_matching_mode(client, identity)
    current["unified_margin_status"] += 1
    with pytest.raises(ValueError):
        await profile.verify_matching_mode(client, identity)
    assert await profile.read_matching_settings(client) == settings
    settings["smp_group"] = -1
    with pytest.raises(ValueError):
        await profile.read_matching_settings(client)


async def test_bybit_segmented_cash_window_deduplicates_identical_inclusive_boundary():
    from ccxt_cm.bybit_cash import MAX_WINDOW_MS

    row = {"id": "fixture-cash"}

    def response(item):
        return {"retCode": 0, "result": {"list": [item], "nextPageCursor": ""}}

    end = 100 + MAX_WINDOW_MS + 1
    proof = await collect_bybit_transaction_pages(
        AsyncMock(return_value=response(row)),
        start_ms=100,
        end_ms=end,
    )
    assert transaction_rows(proof, 100, end) == [row]
    with pytest.raises(ValueError):
        transaction_rows(proof, 101, end)
    bad = copy.deepcopy(proof)
    bad["segments"][1]["pages"][0]["response"] = response(dict(row, unexpected="changed"))
    with pytest.raises(ValueError, match="cash_identity_conflict"):
        transaction_rows(bad, 100, end)
    bad["segments"].pop()
    with pytest.raises(ValueError, match="incomplete_pages"):
        transaction_rows(bad, 100, end)
