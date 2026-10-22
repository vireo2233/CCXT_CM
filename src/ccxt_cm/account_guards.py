"""Account identity checks using official CCXT implicit endpoints, with no writes."""

from urllib.parse import urlsplit

from ccxt import AuthenticationError, NotSupported, PermissionDenied


async def verify_bybit_testnet_account(exchange, expected_uid: str) -> dict:
    """Verify the actual key UID, unified account and spot-trading permission.

    Sandbox routing must already be enabled before any network request. Never
    return the raw query-api payload: it includes the complete API key.
    """
    if exchange.id != "bybit" or exchange.isSandboxModeEnabled is not True:
        raise NotSupported("Bybit Testnet sandbox routing is required")
    if exchange.options.get("defaultType") != "spot":
        raise NotSupported("The account guard is restricted to spot")
    if not isinstance(expected_uid, str) or not expected_uid.isdecimal():
        raise ValueError("A numeric expected UID is required")
    endpoints = exchange.urls.get("api", {})
    if not isinstance(endpoints, dict) or not {"public", "private"} <= endpoints.keys():
        raise NotSupported("Official Testnet REST endpoints are required")
    for endpoint in endpoints.values():
        if not isinstance(endpoint, str):
            raise NotSupported("Unexpected REST endpoint declaration")
        parsed = urlsplit(endpoint.replace("{hostname}", exchange.hostname))
        if parsed.scheme != "https" or parsed.netloc != "api-testnet.bybit.com":
            raise NotSupported("Only official Bybit Testnet REST endpoints are accepted")
    raw = await exchange.private_get_v5_user_query_api()
    result = raw.get("result", {})
    if raw.get("retCode") != 0 or str(result.get("userID")) != expected_uid:
        raise AuthenticationError("The API key UID does not match the expected account")
    if result.get("uta") != 1:
        raise NotSupported("A unified account is required")
    if result.get("readOnly") != 0 or "SpotTrade" not in result.get("permissions", {}).get(
        "Spot", []
    ):
        raise PermissionDenied("The verified API key needs spot-trading permission")
    account = await exchange.private_get_v5_account_info()
    status = account.get("result", {}).get("unifiedMarginStatus")
    if account.get("retCode") != 0 or status not in (3, 4, 5, 6):
        raise NotSupported("Unified account status could not be verified")
    settings = await exchange.private_get_v5_account_user_setting_config()
    behavior = settings.get("result", {})
    if settings.get("retCode") != 0 or behavior.get("lpaSpot") is not False:
        raise NotSupported("Spot limit-price auto-adjustment must be disabled")
    if behavior.get("smsef") is not False:
        raise NotSupported("Third-currency spot fee deduction is not supported")
    return {
        "uid": expected_uid,
        "environment": "sandbox",
        "market_type": "spot",
        "unified_margin_status": status,
        "spot_price_auto_adjustment": False,
        "spot_mnt_fee_deduction": False,
    }
