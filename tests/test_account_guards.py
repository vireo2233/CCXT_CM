from types import SimpleNamespace
from unittest.mock import AsyncMock

import ccxt
import pytest

from ccxt_cm import create_exchange, verify_bybit_testnet_account


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
                "result": {"unifiedMarginStatus": 5},
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
        "spot_price_auto_adjustment": False,
        "spot_mnt_fee_deduction": False,
    }
    assert "apiKey" not in result


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
