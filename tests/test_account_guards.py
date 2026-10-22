from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import ccxt
import pytest

from ccxt_cm import create_exchange, verify_bybit_testnet_account
from ccxt_cm.account_guards import (
    fetch_bybit_testnet_spot_public_window,
    fetch_bybit_testnet_spot_smp_settings,
)


def client():
    return SimpleNamespace(
        id="bybit",
        isSandboxModeEnabled=True,
        hostname="bybit.com",
        options={"defaultType": "spot"},
        urls={
            "api": {
                "public": "https://api-testnet.{hostname}",
                "private": "https://api-testnet.{hostname}",
            }
        },
        private_get_v5_user_query_api=AsyncMock(
            return_value={
                "retCode": 0,
                "result": {
                    "userID": 101,
                    "uta": 1,
                    "readOnly": 0,
                    "permissions": {"Spot": ["SpotTrade"]},
                    "apiKey": "never-return-this",
                },
            }
        ),
        private_get_v5_account_info=AsyncMock(
            return_value={
                "retCode": 0,
                "result": {"unifiedMarginStatus": 5, "marginMode": "REGULAR_MARGIN"},
            }
        ),
        private_get_v5_account_user_setting_config=AsyncMock(
            return_value={"retCode": 0, "result": {"lpaSpot": False, "smsef": False}}
        ),
    )


async def test_verified_identity_is_redacted():
    exchange = client()
    result = await verify_bybit_testnet_account(exchange, "101")
    assert result == {
        "uid": "101",
        "environment": "sandbox",
        "market_type": "spot",
        "unified_margin_status": 5,
        "margin_mode": "REGULAR_MARGIN",
        "spot_price_auto_adjustment": False,
        "spot_mnt_fee_deduction": False,
    }
    assert "apiKey" not in result


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("ISOLATED_MARGIN", "ISOLATED_MARGIN"),
        ("PORTFOLIO_MARGIN", "PORTFOLIO_MARGIN"),
        (None, None),
        ("untrusted-secret", None),
    ],
)
async def test_only_official_account_modes_are_returned(mode, expected):
    exchange = client()
    exchange.private_get_v5_account_info.return_value["result"]["marginMode"] = mode
    result = await verify_bybit_testnet_account(exchange, "101")
    assert result["margin_mode"] == expected
    assert "untrusted-secret" not in str(result)
    exchange.private_get_v5_account_info.assert_awaited_once()


@pytest.mark.parametrize(
    "change",
    [
        {"id": "other"},
        {"isSandboxModeEnabled": False},
        {"hostname": "custom.invalid"},
        {"options": {"defaultType": "swap"}},
        {"urls": {"api": {"public": "https://api.bybit.com", "private": "https://api.bybit.com"}}},
        {
            "urls": {
                "api": {
                    "public": "https://api-testnet.bybit.com:443",
                    "private": "https://api-testnet.bybit.com",
                }
            }
        },
    ],
)
async def test_wrong_environment_never_reads_key_info(change):
    exchange = client()
    for key, value in change.items():
        setattr(exchange, key, value)
    with pytest.raises(ccxt.NotSupported):
        await verify_bybit_testnet_account(exchange, "101")
    exchange.private_get_v5_user_query_api.assert_not_called()


@pytest.mark.parametrize(
    "field,value,exception",
    [
        ("userID", 999, ccxt.AuthenticationError),
        ("uta", 0, ccxt.NotSupported),
        ("readOnly", 1, ccxt.PermissionDenied),
        ("permissions", {"Spot": []}, ccxt.PermissionDenied),
    ],
)
async def test_wrong_identity_or_permissions_fail_closed(field, value, exception):
    exchange = client()
    exchange.private_get_v5_user_query_api.return_value["result"][field] = value
    with pytest.raises(exception):
        await verify_bybit_testnet_account(exchange, "101")
    exchange.private_get_v5_account_info.assert_not_called()


async def test_non_unified_account_fails():
    exchange = client()
    exchange.private_get_v5_account_info.return_value["result"]["unifiedMarginStatus"] = 1
    with pytest.raises(ccxt.NotSupported):
        await verify_bybit_testnet_account(exchange, "101")


@pytest.mark.parametrize(
    "field,value", [("lpaSpot", True), ("lpaSpot", None), ("smsef", True), ("smsef", None)]
)
async def test_unsafe_or_unknown_trade_behavior_fails_closed(field, value):
    exchange = client()
    exchange.private_get_v5_account_user_setting_config.return_value["result"][field] = value
    with pytest.raises(ccxt.NotSupported):
        await verify_bybit_testnet_account(exchange, "101")


@pytest.mark.parametrize("production_ws", [False, True])
async def test_actual_pro_client_nested_websocket_routes(production_ws):
    exchange = create_exchange("bybit", {"options": {"defaultType": "spot"}}, mode="pro")
    exchange.set_sandbox_mode(True)
    fake = client()
    for name in (
        "private_get_v5_user_query_api",
        "private_get_v5_account_info",
        "private_get_v5_account_user_setting_config",
    ):
        setattr(exchange, name, getattr(fake, name))
    if production_ws:
        exchange.urls["api"]["ws"]["private"]["spot"]["unified"] = (
            "wss://stream.bybit.com/v5/private"
        )
    try:
        if production_ws:
            with pytest.raises(ccxt.NotSupported):
                await verify_bybit_testnet_account(exchange, "101")
            exchange.private_get_v5_user_query_api.assert_not_called()
        else:
            assert (await verify_bybit_testnet_account(exchange, "101"))["uid"] == "101"
    finally:
        await exchange.close()


def public_client():
    exchange = client()
    exchange.timeout = 5000
    exchange.market = Mock(return_value={"id": "JUPUSDT", "symbol": "JUP/USDT", "spot": True})
    exchange.milliseconds = Mock(side_effect=[2000, 2002])
    rows = [{"execId": "trade-" + str(at), "symbol": "JUPUSDT", "side": "Buy",
             "price": "0.31", "size": "20", "time": str(at)} for at in (2000, 1999, 1000, 999)]
    exchange.public_get_v5_market_recent_trade = AsyncMock(return_value={
        "retCode": 0, "result": {"category": "spot", "list": rows}, "time": 2001,
    })
    return exchange


async def test_public_suffix_keeps_native_predecessor_and_explicit_coverage_basis():
    exchange = public_client()
    proof = await fetch_bybit_testnet_spot_public_window(exchange, "JUP/USDT", start_ms=1000, end_ms=2000)
    assert proof["complete"] is True and proof["oldest_trade_ms"] == 999
    assert proof["window_start_ms"] == 1000 and proof["window_end_ms"] == 2000
    assert proof["read_start_ms"] == 2000 and proof["read_end_ms"] == 2002 and proof["source_time_ms"] == 2001
    assert proof["coverage_basis"] == "endpoint_latest_suffix_normal_api_semantics_inference"
    assert proof["source_time_is_completion_watermark"] is proof["zero_latency_guaranteed"] is False
    exchange.public_get_v5_market_recent_trade.assert_awaited_once_with({"category": "spot", "symbol": "JUPUSDT", "limit": 60})
    exchange.private_get_v5_user_query_api.assert_not_awaited()
    proof["native_response"]["result"]["list"].clear()
    assert len(exchange.public_get_v5_market_recent_trade.return_value["result"]["list"]) == 4


@pytest.mark.parametrize("case", ["equal", "newer", "empty", "full_equal", "long", "source_before_cutoff"])
async def test_public_suffix_incomplete_evidence_never_claims_window(case):
    exchange = public_client()
    raw = exchange.public_get_v5_market_recent_trade.return_value
    start, end = 1000, 2000
    if case in ("equal", "newer"):
        raw["result"]["list"] = raw["result"]["list"][:3 if case == "equal" else 2]
    elif case == "empty":
        raw["result"]["list"] = []
    elif case == "full_equal":
        row = raw["result"]["list"][2]
        raw["result"]["list"] = [dict(row, execId="tie-" + str(index)) for index in range(60)]
    elif case == "long":
        start, end = 0, 60001
        exchange.milliseconds.side_effect = [60001, 60003]
        raw["time"] = 60002
    else:
        exchange.milliseconds.side_effect = [1999, 2002]
        raw["time"] = 1999
        raw["result"]["list"].pop(0)
    proof = await fetch_bybit_testnet_spot_public_window(exchange, "JUP/USDT", start_ms=start, end_ms=end)
    assert proof["complete"] is False and proof["native_response"] == raw


@pytest.mark.parametrize("read_start", [1999, 2000])
async def test_public_suffix_right_open_cutoff_allows_same_ms_invocation(read_start):
    exchange = public_client()
    exchange.milliseconds.side_effect = [read_start, 2002]
    proof = await fetch_bybit_testnet_spot_public_window(exchange, "JUP/USDT", start_ms=1000, end_ms=2000)
    assert proof["complete"] is True


@pytest.mark.parametrize("start,end", [(True, 2000), (1000, False), (1.0, 2000), (-1, 2000), (2000, 2000), (2001, 2000), (1000, 2002)])
async def test_public_suffix_invalid_or_not_yet_arrived_window_never_reads(start, end):
    exchange = public_client()
    with pytest.raises(ValueError, match="bybit_public_window:"):
        await fetch_bybit_testnet_spot_public_window(exchange, "JUP/USDT", start_ms=start, end_ms=end)
    exchange.public_get_v5_market_recent_trade.assert_not_awaited()


@pytest.mark.parametrize("change", [
    "retcode", "retcode_bool", "category", "not_list", "overfull", "duplicate", "duplicate_conflict",
    "missing_id", "blank_id", "symbol", "side", "zero_price", "negative_size", "nan", "infinity", "float_price",
    "missing_time", "bool_time", "float_time", "fractional_time", "negative_time", "future_trade",
    "missing_source", "string_source", "future_source", "old_source", "backwards_read", "slow_read",
])
async def test_public_suffix_invalid_native_facts_fail_without_leaking_payload(change):
    exchange = public_client()
    raw = exchange.public_get_v5_market_recent_trade.return_value
    raw["retMsg"] = "FAKE_SECRET_NEVER_LOG"
    row = raw["result"]["list"][0]
    if change in ("retcode", "retcode_bool"):
        raw["retCode"] = 1 if change == "retcode" else False
    elif change == "category":
        raw["result"]["category"] = "linear"
    elif change == "not_list":
        raw["result"]["list"] = {}
    elif change == "overfull":
        raw["result"]["list"] = [dict(row, execId=str(i)) for i in range(61)]
    elif change in ("duplicate", "duplicate_conflict"):
        raw["result"]["list"].append(dict(row, price="0.32") if change == "duplicate_conflict" else dict(row))
    elif change in ("missing_id", "blank_id", "symbol", "side", "zero_price", "negative_size", "nan", "infinity", "float_price"):
        field, value = {"missing_id": ("execId", None), "blank_id": ("execId", " "),
                        "symbol": ("symbol", "BTCUSDT"), "side": ("side", "Unknown"),
                        "zero_price": ("price", "0"), "negative_size": ("size", "-1"),
                        "nan": ("price", "NaN"), "infinity": ("size", "Infinity"), "float_price": ("price", .31)}[change]
        row[field] = value
    elif change in ("missing_time", "bool_time", "float_time", "fractional_time", "negative_time", "future_trade"):
        row["time"] = {"missing_time": None, "bool_time": True, "float_time": 2000.0,
                       "fractional_time": "1999.5", "negative_time": "-1", "future_trade": "2002"}[change]
    elif change in ("missing_source", "string_source", "future_source", "old_source"):
        raw["time"] = {"missing_source": None, "string_source": "2001", "future_source": 2003, "old_source": 1999}[change]
    else:
        exchange.milliseconds.side_effect = [2000, 1999 if change == "backwards_read" else 7001]
    with pytest.raises(ValueError, match="bybit_public_window:") as error:
        await fetch_bybit_testnet_spot_public_window(exchange, "JUP/USDT", start_ms=1000, end_ms=2000)
    assert "FAKE_SECRET" not in str(error.value)
    exchange.public_get_v5_market_recent_trade.assert_awaited_once()


@pytest.mark.parametrize("case", ["production", "non_spot", "bad_market", "timeout_config"])
async def test_public_suffix_route_and_market_guards_precede_io(case):
    exchange = public_client()
    if case == "production":
        exchange.isSandboxModeEnabled = False
    elif case == "non_spot":
        exchange.options["defaultType"] = "swap"
    elif case == "bad_market":
        exchange.market.return_value["spot"] = False
    else:
        exchange.timeout = float("nan")
    with pytest.raises((ValueError, ccxt.NotSupported)):
        await fetch_bybit_testnet_spot_public_window(exchange, "JUP/USDT", start_ms=1000, end_ms=2000)
    exchange.public_get_v5_market_recent_trade.assert_not_awaited()


async def test_public_suffix_timeout_and_cancellation_do_not_retry():
    import asyncio

    exchange = public_client()
    exchange.timeout = 1

    async def blocked(params):
        await asyncio.Event().wait()

    exchange.public_get_v5_market_recent_trade.side_effect = blocked
    with pytest.raises(TimeoutError):
        await fetch_bybit_testnet_spot_public_window(exchange, "JUP/USDT", start_ms=1000, end_ms=2000)
    exchange.public_get_v5_market_recent_trade.assert_awaited_once()
    exchange = public_client()
    exchange.public_get_v5_market_recent_trade.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await fetch_bybit_testnet_spot_public_window(exchange, "JUP/USDT", start_ms=1000, end_ms=2000)
    exchange.public_get_v5_market_recent_trade.assert_awaited_once()


def smp_client():
    exchange = client()
    exchange.timeout = 5000
    exchange.milliseconds = Mock(side_effect=[2000, 2003])
    exchange.private_get_v5_account_user_setting_config.return_value = {
        "retCode": 0, "result": {"smpType": 0, "extra": "FAKE_SECRET"}, "time": 2001,
    }
    exchange.private_get_v5_account_smp_group = AsyncMock(return_value={
        "retCode": 0, "result": {"smpGroup": 0}, "time": 2002,
    })
    return exchange


@pytest.mark.parametrize("kind", [0, 1, 2, 3])
@pytest.mark.parametrize("group", [0, 7])
async def test_smp_reader_returns_facts_not_matching_permission(kind, group):
    exchange = smp_client()
    exchange.private_get_v5_account_user_setting_config.return_value["result"]["smpType"] = kind
    exchange.private_get_v5_account_smp_group.return_value["result"]["smpGroup"] = group
    proof = await fetch_bybit_testnet_spot_smp_settings(exchange)
    assert proof == {"smp_type": kind, "smp_group": group, "source_ms": 2001,
                     "settings_source_ms": 2001, "group_source_ms": 2002,
                     "read_start_ms": 2000, "read_end_ms": 2003}
    assert "FAKE_SECRET" not in repr(proof) and "same_account_matching" not in proof
    exchange.private_get_v5_account_user_setting_config.assert_awaited_once_with()
    exchange.private_get_v5_account_smp_group.assert_awaited_once_with()
    exchange.private_get_v5_user_query_api.assert_not_awaited()
    exchange.private_get_v5_account_info.assert_not_awaited()


@pytest.mark.parametrize("change", [
    {"id": "other"}, {"isSandboxModeEnabled": False}, {"isSandboxModeEnabled": None},
    {"options": {"defaultType": "swap"}},
    {"urls": {"api": {"public": "https://api.bybit.com", "private": "https://api.bybit.com"}}},
    {"urls": {"api": {"public": "https://api-demo.bybit.com", "private": "https://api-demo.bybit.com"}}},
    {"urls": {"api": {"public": "https://api-testnet.bybit.com", "private": "https://api.bybit.com"}}},
])
async def test_smp_wrong_environment_never_reads_accounts(change):
    exchange = smp_client()
    for key, value in change.items():
        setattr(exchange, key, value)
    with pytest.raises(ccxt.NotSupported):
        await fetch_bybit_testnet_spot_smp_settings(exchange)
    exchange.private_get_v5_account_user_setting_config.assert_not_awaited()
    exchange.private_get_v5_account_smp_group.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ["settings", "group"])
@pytest.mark.parametrize("field,value", [
    ("retCode", False), ("retCode", "0"), ("retCode", 1), ("retCode", None),
    ("result", None), ("result", []), ("time", None), ("time", "2001"),
    ("time", True), ("time", 2001.0),
])
async def test_smp_native_shape_is_strict_and_diagnostics_do_not_echo_raw(endpoint, field, value):
    exchange = smp_client()
    method = exchange.private_get_v5_account_user_setting_config if endpoint == "settings" else exchange.private_get_v5_account_smp_group
    method.return_value[field] = value
    method.return_value["retMsg"] = "FAKE_SECRET"
    with pytest.raises(ValueError, match="bybit_smp:") as caught:
        await fetch_bybit_testnet_spot_smp_settings(exchange)
    assert "FAKE_SECRET" not in str(caught.value)


@pytest.mark.parametrize("field,value", [
    ("smpType", None), ("smpType", True), ("smpType", "0"), ("smpType", 0.0),
    ("smpType", -1), ("smpType", 4), ("smpGroup", None), ("smpGroup", False),
    ("smpGroup", "0"), ("smpGroup", 0.0), ("smpGroup", -1),
])
async def test_smp_unknown_fields_never_default_to_disabled(field, value):
    exchange = smp_client()
    method = exchange.private_get_v5_account_user_setting_config if field == "smpType" else exchange.private_get_v5_account_smp_group
    method.return_value["result"][field] = value
    with pytest.raises(ValueError, match="bybit_smp:"):
        await fetch_bybit_testnet_spot_smp_settings(exchange)


@pytest.mark.parametrize("start,end,setting_source,group_source", [
    (-1, 2003, 2001, 2002), (True, 2003, 2001, 2002), (2000.0, 2003, 2001, 2002),
    (2000, 1999, 2001, 2002), (2000, 7001, 2001, 2002), (2000, 2003, 1999, 2002),
    (2000, 2003, 2001, 2004), (2000, 2003, 2002, 2001), (0, 3, 0, 1),
])
async def test_smp_source_and_local_read_order_have_no_implicit_clock_tolerance(start, end, setting_source, group_source):
    exchange = smp_client()
    exchange.milliseconds.side_effect = [start, end]
    exchange.private_get_v5_account_user_setting_config.return_value["time"] = setting_source
    exchange.private_get_v5_account_smp_group.return_value["time"] = group_source
    with pytest.raises(ValueError, match="bybit_smp:"):
        await fetch_bybit_testnet_spot_smp_settings(exchange)


@pytest.mark.parametrize("timeout", [0, -1, True, "5000", float("nan"), float("inf")])
async def test_smp_invalid_existing_timeout_precedes_io(timeout):
    exchange = smp_client()
    exchange.timeout = timeout
    with pytest.raises(ValueError, match="bybit_smp:invalid_timeout"):
        await fetch_bybit_testnet_spot_smp_settings(exchange)
    exchange.private_get_v5_account_user_setting_config.assert_not_awaited()


async def test_smp_read_failure_timeout_and_cancellation_never_retry_or_echo_secrets():
    import asyncio

    for failure in (ccxt.RequestTimeout("FAKE_SECRET"), RuntimeError("FAKE_SECRET"), asyncio.CancelledError()):
        exchange = smp_client()
        exchange.private_get_v5_account_smp_group.side_effect = failure
        with pytest.raises((ValueError, asyncio.CancelledError)) as caught:
            await fetch_bybit_testnet_spot_smp_settings(exchange)
        assert "FAKE_SECRET" not in str(caught.value)
        exchange.private_get_v5_account_user_setting_config.assert_awaited_once()
        exchange.private_get_v5_account_smp_group.assert_awaited_once()
    exchange = smp_client()
    exchange.timeout = 1
    async def blocked():
        await asyncio.Event().wait()
    exchange.private_get_v5_account_smp_group.side_effect = blocked
    with pytest.raises(ValueError, match="bybit_smp:read_timeout"):
        await fetch_bybit_testnet_spot_smp_settings(exchange)
    exchange.private_get_v5_account_smp_group.assert_awaited_once()


async def test_smp_rechecks_same_client_route_before_second_read():
    exchange = smp_client()
    raw = exchange.private_get_v5_account_user_setting_config.return_value
    async def changed():
        exchange.urls["api"]["private"] = "https://api.bybit.com"
        return raw
    exchange.private_get_v5_account_user_setting_config.side_effect = changed
    with pytest.raises(ValueError, match="bybit_smp:native_read_failed"):
        await fetch_bybit_testnet_spot_smp_settings(exchange)
    exchange.private_get_v5_account_smp_group.assert_not_awaited()


async def test_smp_actual_ccxt_implicit_endpoints_are_two_private_gets_without_writes():
    from urllib.parse import urlsplit

    exchange = create_exchange("bybit", {"apiKey": "FAKE_KEY", "secret": "FAKE_SECRET",
        "enableRateLimit": False, "options": {"defaultType": "spot"}}, mode="async")
    exchange.set_sandbox_mode(True)
    exchange.milliseconds = Mock(return_value=1700000000000)
    exchange.fetch = AsyncMock(side_effect=[
        {"retCode": 0, "result": {"smpType": 0}, "time": 1700000000000},
        {"retCode": 0, "result": {"smpGroup": 0}, "time": 1700000000000},
    ])
    try:
        result = await fetch_bybit_testnet_spot_smp_settings(exchange)
        assert result["smp_type"] == result["smp_group"] == 0
        assert "FAKE" not in repr(result)
        requests = [call.args for call in exchange.fetch.await_args_list]
        assert [(urlsplit(row[0]).netloc, urlsplit(row[0]).path, row[1], row[3]) for row in requests] == [
            ("api-testnet.bybit.com", "/v5/account/user-setting-config", "GET", None),
            ("api-testnet.bybit.com", "/v5/account/smp-group", "GET", None),
        ]
    finally:
        await exchange.close()
