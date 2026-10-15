import pytest
from aiohttp import web
from ccxt import (
    AccountNotEnabled,
    AuthenticationError,
    BadRequest,
    BadResponse,
    BadSymbol,
    DuplicateOrderId,
    ExchangeError,
    ExchangeNotAvailable,
    InsufficientFunds,
    InvalidNonce,
    InvalidOrder,
    MarketClosed,
    OperationRejected,
    OrderImmediatelyFillable,
    OrderNotFillable,
    OrderNotFound,
    PermissionDenied,
    RateLimitExceeded,
    RequestTimeout,
)

from ccxt_cm import create_exchange


@pytest.mark.parametrize(
    ("code", "expected_exception"),
    [
        (1000, BadRequest),
        (1001, BadSymbol),
        (1002, InvalidOrder),
        (1003, InvalidOrder),
        (1004, InvalidOrder),
        (1005, InvalidOrder),
        (1006, InvalidOrder),
        (1007, InvalidOrder),
        (1008, MarketClosed),
        (1009, InvalidOrder),
        (1010, InvalidOrder),
        (1011, InvalidOrder),
        (1012, InvalidOrder),
        (1013, InvalidOrder),
        (1014, InvalidOrder),
        (1015, OperationRejected),
        (1016, InvalidOrder),
        (1017, OperationRejected),
        (1018, OperationRejected),
        (1019, OrderNotFillable),
        (1020, OrderImmediatelyFillable),
        (1021, OrderImmediatelyFillable),
        (1022, OperationRejected),
        (1023, BadRequest),
        (1024, AccountNotEnabled),
        (1025, InvalidOrder),
        (2000, InsufficientFunds),
        (2001, OperationRejected),
        (2002, InsufficientFunds),
        (3000, OrderNotFound),
        (3001, OrderNotFound),
        (3002, InvalidOrder),
        (3003, DuplicateOrderId),
        (3004, InvalidOrder),
        (3005, OperationRejected),
        (3006, AccountNotEnabled),
        (3007, OperationRejected),
        (4000, AuthenticationError),
        (4001, PermissionDenied),
        (4002, AuthenticationError),
        (4003, InvalidNonce),
        (4004, PermissionDenied),
        (4006, AccountNotEnabled),
        (4007, OperationRejected),
        (4008, OperationRejected),
        (5000, RateLimitExceeded),
        (5001, RateLimitExceeded),
        (6000, ExchangeNotAvailable),
        (6001, RequestTimeout),
        (6003, RequestTimeout),
        (6004, ExchangeNotAvailable),
        (6005, BadResponse),
        (7000, ExchangeError),
    ],
)
def test_handle_errors_maps_documented_business_codes(code, expected_exception):
    exchange = create_exchange("bifu", mode="async")

    expected_message = "order not found" if code in (3000, 3001) else str(code)
    with pytest.raises(expected_exception, match=expected_message):
        exchange.handle_errors(
            400,
            "Bad Request",
            "https://example.invalid/test",
            "POST",
            {},
            "",
            {"code": code, "message": "fixture error"},
            {},
            None,
        )


def test_handle_errors_does_not_invent_mapping_for_unknown_business_code():
    exchange = create_exchange("bifu", mode="async")

    result = exchange.handle_errors(
        400,
        "Bad Request",
        "https://example.invalid/test",
        "POST",
        {},
        "",
        {"code": 9999, "message": "future error"},
        {},
        None,
    )

    assert result is None


def test_order_missing_and_not_owned_are_indistinguishable():
    exchange = create_exchange("bifu", mode="async")
    messages = []

    for code, message in ((3000, "missing"), (3001, "belongs to another account")):
        with pytest.raises(OrderNotFound) as caught:
            exchange.handle_errors(
                404,
                "Not Found",
                "https://example.invalid/test",
                "GET",
                {},
                "",
                {"code": code, "message": message},
                {},
                None,
            )
        messages.append(str(caught.value))

    assert messages == ["bifu order not found", "bifu order not found"]


@pytest.mark.loopback
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_unknown_business_code_falls_through_to_ccxt_http_error(unused_tcp_port):
    async def unknown_error(_request):
        return web.json_response({"code": 9999, "message": "future error"}, status=400)

    app = web.Application()
    app.router.add_get("/market/v1/meta", unknown_error)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", unused_tcp_port)
    await site.start()

    exchange = create_exchange("bifu", mode="async")
    exchange.set_sandbox_mode(True)
    exchange.urls["api"]["public"] = f"http://127.0.0.1:{unused_tcp_port}"
    try:
        with pytest.raises(ExchangeNotAvailable, match="9999"):
            await exchange.public_get_market_v1_meta()
    finally:
        await exchange.close()
        await runner.cleanup()
