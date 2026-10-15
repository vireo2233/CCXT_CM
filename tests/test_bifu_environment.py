import hashlib
import hmac

import pytest

from ccxt_cm import Registry, create_exchange, exchange_class, list_exchanges
from ccxt_cm.exchanges.bifu import BIFU_EXTENSION, BifuREST
from examples.inspect_bifu_environment import inspect_environment


@pytest.fixture
async def exchanges():
    registry = Registry()
    registry.register(BIFU_EXTENSION)
    test_exchange = registry.create_exchange(
        "bifu",
        {"apiKey": "test-key", "secret": "test-secret"},
        mode="async",
    )
    closed_production_exchange = registry.create_exchange("bifu", mode="async")
    yield registry, test_exchange, closed_production_exchange
    await test_exchange.close()
    await closed_production_exchange.close()


async def test_bifu_is_an_explicit_async_extension(exchanges):
    registry, test_exchange, _ = exchanges

    assert registry.exchange_class("bifu", mode="async") is BifuREST
    assert type(test_exchange) is BifuREST
    assert test_exchange.id == "bifu"
    assert test_exchange.has["fetchMarkets"] is True


async def test_bifu_is_available_from_the_public_factory_without_manual_registration():
    exchange = create_exchange("bifu", mode="async")
    try:
        assert type(exchange) is BifuREST
        assert exchange_class("bifu", mode="async") is BifuREST
        assert "bifu" in list_exchanges(mode="async")
    finally:
        await exchange.close()


async def test_test_environment_requires_the_standard_ccxt_switch(exchanges):
    _, test_exchange, _ = exchanges

    assert test_exchange.urls["api"] == {
        "public": None,
        "private": None,
        "ws": None,
    }
    assert test_exchange.isSandboxModeEnabled is False

    test_exchange.set_sandbox_mode(True)

    assert test_exchange.isSandboxModeEnabled is True
    assert test_exchange.urls["api"] == {
        "public": "https://flame-api.bifu.dev",
        "private": "https://flame-api.bifu.dev",
        "ws": "wss://flame-api.bifu.dev",
    }


async def test_test_and_production_instances_do_not_share_environment_or_credentials(exchanges):
    _, test_exchange, closed_production_exchange = exchanges

    test_exchange.set_sandbox_mode(True)

    assert closed_production_exchange.isSandboxModeEnabled is False
    assert closed_production_exchange.urls["api"] == {
        "public": None,
        "private": None,
        "ws": None,
    }
    assert closed_production_exchange.apiKey is None
    assert closed_production_exchange.secret is None
    assert test_exchange.apiKey == "test-key"
    assert test_exchange.secret == "test-secret"

    test_exchange.urls["api"]["public"] = "https://changed.example.invalid"
    assert closed_production_exchange.urls["api"]["public"] is None


@pytest.mark.parametrize(
    ("environment", "sandbox", "configured"),
    [("test", True, True), ("production", False, False)],
)
async def test_beginner_inspection_reports_the_environment_without_credentials(
    environment, sandbox, configured
):
    result = await inspect_environment(environment)

    assert result["id"] == "bifu"
    assert result["environment"] == environment
    assert result["sandbox"] is sandbox
    assert result["configured"] is configured
    assert "apiKey" not in result
    assert "secret" not in result


async def test_explicit_production_configuration_uses_the_same_adapter():
    api_urls = {
        "public": "https://api.example.invalid",
        "private": "https://api.example.invalid",
        "ws": "wss://stream.example.invalid",
    }
    exchange = create_exchange(
        "bifu",
        {
            "apiKey": "production-key",
            "secret": "production-secret",
            "urls": {"api": api_urls},
        },
        mode="async",
    )
    try:
        assert exchange.urls["api"] == api_urls
        assert exchange.isSandboxModeEnabled is False
    finally:
        await exchange.close()


async def test_private_requests_use_the_documented_bifu_signature():
    exchange = create_exchange(
        "bifu",
        {"apiKey": "fixture-key", "secret": "fixture-secret"},
        mode="async",
    )
    try:
        exchange.set_sandbox_mode(True)
        exchange.milliseconds = lambda: 1700000000000

        signed = exchange.sign(
            "spot/v1/order/query",
            "private",
            "GET",
            {"order_id": "abc +/", "instrument_id": 90000001},
        )

        payload = "1700000000000\nGET\n/spot/v1/order/query\n"
        expected = hmac.new(b"fixture-secret", payload.encode(), hashlib.sha256).hexdigest()
        assert signed["url"].endswith(
            "/spot/v1/order/query?instrument_id=90000001&order_id=abc%20%2B%2F"
        )
        assert signed["headers"] == {
            "X-API-KEY": "fixture-key",
            "X-TS": "1700000000000",
            "X-SIGN": expected,
        }

        signed = exchange.sign(
            "spot/v1/order",
            "private",
            "POST",
            {"price": "100.10", "instrument_id": 90000001},
        )
        assert signed["body"] == '{"instrument_id":90000001,"price":"100.10"}'
        payload = "1700000000000\nPOST\n/spot/v1/order\n" + signed["body"]
        assert (
            signed["headers"]["X-SIGN"]
            == hmac.new(b"fixture-secret", payload.encode(), hashlib.sha256).hexdigest()
        )
    finally:
        await exchange.close()
