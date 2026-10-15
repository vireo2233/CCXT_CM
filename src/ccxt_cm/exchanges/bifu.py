"""CCXT-compatible Bifu adapter for one caller-selected API environment."""

import asyncio
import hashlib
import hmac
import json
from urllib.parse import urlencode

from ccxt import (
    AccountNotEnabled,
    ArgumentsRequired,
    AuthenticationError,
    BadRequest,
    BadResponse,
    BadSymbol,
    BaseError,
    DuplicateOrderId,
    ExchangeError,
    ExchangeNotAvailable,
    InsufficientFunds,
    InvalidNonce,
    InvalidOrder,
    MarketClosed,
    NetworkError,
    NotSupported,
    OperationRejected,
    OrderImmediatelyFillable,
    OrderNotFillable,
    OrderNotFound,
    PermissionDenied,
    RateLimitExceeded,
    RequestTimeout,
)
from ccxt.async_support.base.ws.cache import (
    ArrayCache,
    ArrayCacheBySymbolById,
    ArrayCacheByTimestamp,
)
from ccxt.base.decimal_to_precision import TICK_SIZE
from ccxt.base.precise import Precise
from ccxt.base.types import Entry

from ..base import AsyncExchange
from ..capabilities import SpecialMethod
from ..registry import Extension

_DEVELOPMENT_REST_URL = "https://flame-api.bifu.dev"
_DEVELOPMENT_WS_URL = "wss://flame-api.bifu.dev"

_BIFU_ERROR_EXCEPTIONS = {
    1000: BadRequest,
    1001: BadSymbol,
    1002: InvalidOrder,
    1003: InvalidOrder,
    1004: InvalidOrder,
    1005: InvalidOrder,
    1006: InvalidOrder,
    1007: InvalidOrder,
    1008: MarketClosed,
    1009: InvalidOrder,
    1010: InvalidOrder,
    1011: InvalidOrder,
    1012: InvalidOrder,
    1013: InvalidOrder,
    1014: InvalidOrder,
    1015: OperationRejected,
    1016: InvalidOrder,
    1017: OperationRejected,
    1018: OperationRejected,
    1019: OrderNotFillable,
    1020: OrderImmediatelyFillable,
    1021: OrderImmediatelyFillable,
    1022: OperationRejected,
    1023: BadRequest,
    1024: AccountNotEnabled,
    1025: InvalidOrder,
    2000: InsufficientFunds,
    2001: OperationRejected,
    2002: InsufficientFunds,
    3002: InvalidOrder,
    3003: DuplicateOrderId,
    3004: InvalidOrder,
    3005: OperationRejected,
    3006: AccountNotEnabled,
    3007: OperationRejected,
    4000: AuthenticationError,
    4001: PermissionDenied,
    4002: AuthenticationError,
    4003: InvalidNonce,
    4004: PermissionDenied,
    # Observed from the documented SANDBOX_MARKET endpoint when the API key
    # belongs to a regular account rather than a Bifu Sandbox account.
    4006: AccountNotEnabled,
    4007: OperationRejected,
    4008: OperationRejected,
    5000: RateLimitExceeded,
    5001: RateLimitExceeded,
    6000: ExchangeNotAvailable,
    6001: RequestTimeout,
    # The write may have reached Bifu. Callers must reconcile before retrying.
    6003: RequestTimeout,
    6004: ExchangeNotAvailable,
    7000: ExchangeError,
}


class BifuREST(AsyncExchange):
    """Async Bifu REST adapter; endpoint methods are added one accepted slice at a time."""

    id = "bifu"
    cm_special_methods = (
        SpecialMethod(
            "create_mock_order",
            "Create one Bifu sandbox-only simulated market order",
            "docs/learning/12-bifu-mock.md",
            private=True,
            mutating=True,
            returns="CCXT Order with raw Bifu acknowledgement in info",
        ),
    )
    public_get_market_v1_book_ticker = Entry("market/v1/bookTicker", "public", "GET", {"cost": 1})
    public_get_market_v1_depth = Entry("market/v1/depth", "public", "GET", {"cost": 1})
    public_get_market_v1_klines = Entry("market/v1/klines", "public", "GET", {"cost": 1})
    public_get_market_v1_meta = Entry("market/v1/meta", "public", "GET", {"cost": 1})
    public_get_market_v1_ticker = Entry("market/v1/ticker", "public", "GET", {"cost": 1})
    public_get_market_v1_tickers = Entry("market/v1/tickers", "public", "GET", {"cost": 1})
    public_get_market_v1_trades = Entry("market/v1/trades", "public", "GET", {"cost": 1})
    public_get_market_v1_trends = Entry("market/v1/trends", "public", "GET", {"cost": 1})
    private_get_spot_v1_account = Entry("spot/v1/account", "private", "GET", {"cost": 1})
    private_get_spot_v1_fund_flows = Entry("spot/v1/fundFlows", "private", "GET", {"cost": 1})
    private_get_spot_v1_history_orders = Entry(
        "spot/v1/historyOrders", "private", "GET", {"cost": 1}
    )
    private_get_spot_v1_my_trades = Entry("spot/v1/myTrades", "private", "GET", {"cost": 1})
    private_get_spot_v1_open_orders = Entry("spot/v1/openOrders", "private", "GET", {"cost": 1})
    private_get_spot_v1_order_query = Entry("spot/v1/order/query", "private", "GET", {"cost": 1})
    private_post_spot_v1_order = Entry("spot/v1/order", "private", "POST", {"cost": 1})
    private_post_spot_v1_order_amend = Entry("spot/v1/order/amend", "private", "POST", {"cost": 1})
    private_post_spot_v1_orders = Entry("spot/v1/orders", "private", "POST", {"cost": 1})
    private_post_spot_v1_orders_amend = Entry(
        "spot/v1/orders/amend", "private", "POST", {"cost": 1}
    )
    private_post_spot_v1_order_cancel = Entry(
        "spot/v1/order/cancel", "private", "POST", {"cost": 1}
    )
    private_post_spot_v1_orders_cancel = Entry(
        "spot/v1/orders/cancel", "private", "POST", {"cost": 1}
    )
    private_post_spot_v1_open_orders_cancel = Entry(
        "spot/v1/openOrders/cancel", "private", "POST", {"cost": 1}
    )

    def describe(self):
        return self.deep_extend(
            super().describe(),
            {
                "id": self.id,
                "name": "Bifu",
                "countries": [],
                "precisionMode": TICK_SIZE,
                "has": {
                    "publicAPI": True,
                    "privateAPI": True,
                    "spot": True,
                    "cancelAllOrders": True,
                    "cancelOrder": True,
                    "cancelOrders": True,
                    "createOrder": True,
                    "createOrders": True,
                    "createLimitOrder": True,
                    "createMarketBuyOrder": False,
                    "createMarketBuyOrderWithCost": True,
                    "createMarketOrder": False,
                    "createMarketOrderWithCost": False,
                    "createMarketSellOrder": True,
                    "createMarketSellOrderWithCost": False,
                    "editOrder": True,
                    "editOrders": True,
                    "fetchBalance": True,
                    "fetchBidsAsks": "emulated",
                    "fetchLedger": True,
                    "fetchClosedOrders": True,
                    "fetchMarkets": True,
                    "fetchMyTrades": True,
                    "fetchOHLCV": True,
                    "fetchOpenOrders": True,
                    "fetchOrder": True,
                    "fetchOrderBook": True,
                    "fetchTicker": True,
                    "fetchTickers": True,
                    "fetchTrades": True,
                },
                "urls": {
                    # Production deliberately has no guessed endpoint. Deployment must
                    # inject confirmed production URLs into the instance configuration.
                    "api": {"public": None, "private": None, "ws": None},
                    "test": {
                        "public": _DEVELOPMENT_REST_URL,
                        "private": _DEVELOPMENT_REST_URL,
                        "ws": _DEVELOPMENT_WS_URL,
                    },
                    "www": "https://bifu.dev",
                    "doc": "https://flame-api.bifu.dev/docs",
                },
                "timeframes": {
                    period: period
                    for period in (
                        "1m",
                        "5m",
                        "15m",
                        "30m",
                        "1h",
                        "2h",
                        "4h",
                        "6h",
                        "12h",
                        "1d",
                        "1w",
                        "1M",
                    )
                },
                "options": {"createMarketBuyOrderRequiresPrice": True},
            },
        )

    def sign(self, path, api="public", method="GET", params=None, headers=None, body=None):
        base_url = self.urls["api"][api]
        if not base_url:
            raise BadRequest("bifu API URL is not configured")
        params = dict(params or {})
        headers = dict(headers or {})
        canonical_path = "/" + path.lstrip("/")
        url = base_url.rstrip("/") + canonical_path
        body = None
        if method == "GET":
            if params:
                url += "?" + self.urlencode(dict(sorted(params.items())))
        else:
            body = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            headers["Content-Type"] = "application/json"
        if api == "private":
            self.check_required_credentials()
            timestamp = str(self.milliseconds())
            payload = "\n".join((timestamp, method.upper(), canonical_path, body or ""))
            signature = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            headers.update({"X-API-KEY": self.apiKey, "X-TS": timestamp, "X-SIGN": signature})
        return {"url": url, "method": method, "body": body, "headers": headers}

    def handle_errors(
        self,
        status_code,
        status_text,
        url,
        method,
        response_headers,
        response_body,
        response,
        request_headers,
        request_body,
    ):
        code = self.safe_integer(response, "code")
        message = self.safe_string(response, "message", "unknown error")
        if code in (3000, 3001):
            raise OrderNotFound("bifu order not found")
        if code == 6005:
            raise BadResponse(f"bifu {code} market has no trades yet: {message}")
        exception_class = _BIFU_ERROR_EXCEPTIONS.get(code)
        if exception_class is not None:
            raise exception_class(f"bifu {code} {message}")

    async def fetch_markets(self, params=None):
        if params:
            raise NotSupported("bifu fetch_markets does not accept params")
        response = await self.public_get_market_v1_meta(params or {})
        if not isinstance(response, dict):
            raise BadResponse("bifu market metadata must be a JSON object")
        assets = response.get("assets")
        spots = response.get("spots")
        if not isinstance(assets, list) or not isinstance(spots, list):
            raise BadResponse("bifu market metadata is missing assets or spots")
        assets_by_id = {}
        for asset in assets:
            if not isinstance(asset, dict):
                raise BadResponse("bifu market metadata has an invalid asset entry")
            asset_id = self.safe_string(asset, "asset_id")
            if not asset_id:
                raise BadResponse("bifu market asset id is missing")
            assets_by_id[asset_id] = asset
        if any(not isinstance(spot, dict) for spot in spots):
            raise BadResponse("bifu market metadata has an invalid spot entry")
        self._bifu_assets_by_id = assets_by_id
        return [self.parse_market(spot, assets_by_id) for spot in spots]

    async def fetch_balance(self, params=None):
        self._check_supported_params("fetch_balance", params or {}, set())
        await self.load_markets()
        response = await self.private_get_spot_v1_account({})
        balances = self.safe_list(response, "balances")
        if balances is None:
            raise BadResponse("bifu account response is missing balances")
        assets = getattr(self, "_bifu_assets_by_id", {})
        result = {"info": response}
        for item in balances:
            asset_id = self.safe_string(item, "asset_id")
            asset = assets.get(asset_id)
            code = self.safe_string(asset, "code") if asset else self.safe_currency_code(asset_id)
            if not code:
                raise BadResponse("bifu balance asset id is missing")
            result[code] = {
                "free": self.safe_string(item, "available"),
                "used": self.safe_string(item, "frozen"),
            }
        return self.safe_balance(result)

    async def fetch_ledger(self, code=None, since=None, limit=None, params=None):
        params = dict(params or {})
        self._check_supported_params("fetch_ledger", params, {"cursor", "end_ts_ms", "until"})
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000
        ):
            raise BadRequest("bifu fetch_ledger limit must be an integer between 1 and 1000")
        until = params.pop("until", None)
        if until is not None and "end_ts_ms" in params:
            raise BadRequest("bifu fetch_ledger cannot use both until and end_ts_ms")
        if until is not None:
            params["end_ts_ms"] = until

        await self.load_markets()
        currency = self.currency(code) if code is not None else None
        request = dict(params)
        if since is not None:
            request["start_ts_ms"] = since
        if limit is not None:
            request["limit"] = limit
        response = await self.private_get_spot_v1_fund_flows(request)
        self.last_json_response = response
        if response == {}:
            return []
        flows = self.safe_list(response, "flows")
        if flows is None:
            raise BadResponse("bifu fund flows response is missing flows")
        entries = []
        for flow in flows:
            if not isinstance(flow, dict):
                raise BadResponse("bifu fund flows response has an invalid fund flow entry")
            entries.append(self.parse_ledger_entry(flow))
        code = currency["code"] if currency is not None else None
        return self.filter_by_currency_since_limit(
            self.sort_by(entries, "timestamp"), code, since, limit
        )

    def parse_ledger_entry(self, flow, currency=None):
        ticket = self.safe_string(flow, "ticket")
        kind = self.safe_string(flow, "kind")
        asset_id = self.safe_string(flow, "asset")
        timestamp = self.safe_integer(flow, "ts_ms")
        amount_raw = self.safe_string(flow, "amount")
        if not ticket or not kind or not asset_id or timestamp is None or amount_raw is None:
            raise BadResponse("bifu fund flows response has an invalid fund flow entry")
        try:
            amount = Precise.string_abs(amount_raw)
            direction = None
            if Precise.string_lt(amount_raw, "0"):
                direction = "out"
            elif Precise.string_gt(amount_raw, "0"):
                direction = "in"
            if self.parse_number(amount) is None:
                raise ValueError("invalid amount")
        except (TypeError, ValueError, OverflowError):
            raise BadResponse("bifu fund flows response has an invalid fund flow entry") from None

        assets = getattr(self, "_bifu_assets_by_id", {})
        asset = assets.get(asset_id)
        code = self.safe_string(asset, "code") if asset else self.safe_currency_code(asset_id)
        if not code:
            raise BadResponse("bifu fund flows response has an invalid fund flow entry")
        entry_currency = currency or {"id": asset_id, "code": code}

        product = self.safe_string(flow, "product")
        scope = self.safe_string(flow, "margin_scope")
        account = self._bifu_account_reference(product, scope)
        reference_account = None
        if kind == "TRANSFER":
            from_account = self._bifu_account_reference(
                self.safe_string(flow, "from_product"),
                self.safe_string(flow, "from_scope"),
            )
            to_account = self._bifu_account_reference(
                self.safe_string(flow, "to_product"),
                self.safe_string(flow, "to_scope"),
            )
            if direction == "out":
                account = account or from_account
                reference_account = to_account
            elif direction == "in":
                account = account or to_account
                reference_account = from_account

        ledger_types = {
            "CREDIT": "deposit",
            "WITHDRAW": "withdrawal",
            "TRANSFER": "transfer",
            "BORROW": "borrow",
            "REPAY": "repay",
            "INTEREST": "interest",
        }
        return self.safe_ledger_entry(
            {
                "info": flow,
                "id": ticket,
                "timestamp": timestamp,
                "direction": direction,
                "account": account,
                "referenceId": None,
                "referenceAccount": reference_account,
                "type": self.safe_string(ledger_types, kind, kind.lower()),
                "currency": code,
                "amount": amount,
                "before": None,
                "after": None,
                "status": None,
                "fee": None,
            },
            entry_currency,
        )

    @staticmethod
    def _bifu_account_reference(product, scope):
        if not product:
            return None
        return product if scope is None else f"{product}:{scope}"

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        request, market = await self._create_order_request(
            symbol, type, side, amount, price, params
        )
        response = await self.private_post_spot_v1_order(request)
        if not isinstance(response, dict):
            raise BadResponse("bifu create order response must be a JSON object")
        order_id = self.safe_string(response, "order_id")
        if not order_id:
            raise BadResponse("bifu create order response is missing order_id")
        return self._parse_create_ack(response, request, market)

    async def create_mock_order(self, symbol, side, value, params=None):
        """Create Bifu's sandbox-account-only simulated market order."""
        if not self.isSandboxModeEnabled:
            raise PermissionDenied("bifu mock orders require sandbox mode")
        params = dict(params or {})
        self._check_supported_params("create_mock_order", params, {"clientOrderId", "price"})
        if not isinstance(side, str) or side.lower() not in ("buy", "sell"):
            raise InvalidOrder("bifu mock order side must be buy or sell")
        order_side = side.lower()
        value_input = self._positive_decimal_string(value)
        if value_input is None:
            raise InvalidOrder("bifu mock order value must be a positive finite value")
        price_input = self._positive_decimal_string(params.get("price"))
        if "price" in params and price_input is None:
            raise InvalidOrder("bifu mock order price must be a positive finite value")

        await self.load_markets()
        market = self.market(symbol)
        instrument_id = self.safe_integer(market, "id")
        if instrument_id is None:
            raise BadResponse("bifu market instrument id must be an integer")
        request = {
            "instrument_id": instrument_id,
            "side": order_side.upper(),
            "type": "SANDBOX_MARKET",
            "client_order_id": self.safe_string(params, "clientOrderId") or self.uuid16(),
            "time_in_force": "IOC",
        }
        if order_side == "buy":
            quantity = self.cost_to_precision(symbol, value_input)
            limits = market["limits"]["cost"]
            label = "value"
            request["quote_qty"] = quantity
        else:
            quantity = self.amount_to_precision(symbol, value_input)
            limits = market["limits"]["amount"]
            label = "amount"
            request["qty"] = quantity
        if limits["min"] is not None and Precise.string_lt(quantity, str(limits["min"])):
            raise InvalidOrder(f"bifu mock order {label} is below the market minimum {label}")
        if limits["max"] is not None and Precise.string_gt(quantity, str(limits["max"])):
            raise InvalidOrder(f"bifu mock order {label} is above the market maximum {label}")
        if price_input is not None:
            request["price"] = self.price_to_precision(symbol, price_input)

        response = await self.private_post_spot_v1_order(request)
        if not isinstance(response, dict):
            raise BadResponse("bifu mock order response must be a JSON object")
        if not self.safe_string(response, "order_id"):
            raise BadResponse("bifu mock order response is missing order_id")
        order = self._parse_create_ack(response, request, market)
        # The sandbox endpoint returns an acknowledgement, not authoritative
        # final state. Keep any extra raw fields in info for later diagnosis.
        order["status"] = None
        return order

    async def create_orders(self, orders, params=None):
        params = dict(params or {})
        self._check_supported_params("create_orders", params, set())
        if not isinstance(orders, list) or not 1 <= len(orders) <= 100:
            raise ArgumentsRequired("bifu create_orders requires between 1 and 100 orders")
        symbols = []
        for order in orders:
            if not isinstance(order, dict):
                raise InvalidOrder("bifu create_orders entries must be objects")
            symbol = self.safe_string(order, "symbol")
            if symbol is None:
                raise ArgumentsRequired("bifu create_orders requires a symbol for each order")
            symbols.append(symbol)
        if len(set(symbols)) != 1:
            raise NotSupported("bifu create_orders supports one symbol per batch")

        entries = []
        requests = []
        market = None
        for order in orders:
            order_params = order.get("params", {})
            if not isinstance(order_params, dict):
                raise InvalidOrder("bifu create_orders params must be an object")
            request, order_market = await self._create_order_request(
                self.safe_string(order, "symbol"),
                self.safe_value(order, "type"),
                self.safe_value(order, "side"),
                self.safe_value(order, "amount"),
                self.safe_value(order, "price"),
                order_params,
            )
            market = order_market
            requests.append(request)
            entries.append(self.omit(request, "instrument_id"))
        response = await self.private_post_spot_v1_orders(
            {"instrument_id": self.safe_integer(market, "id"), "entries": entries}
        )
        if not isinstance(response, dict):
            raise BadResponse("bifu create orders response must be a JSON object")
        acknowledgements = self.safe_list(response, "acks")
        if acknowledgements is None or len(acknowledgements) != len(requests):
            raise BadResponse("bifu create orders ACK count does not match request count")
        result = []
        for index, acknowledgement in enumerate(acknowledgements):
            if not isinstance(acknowledgement, dict):
                raise BadResponse("bifu create orders response has an invalid ACK entry")
            request = requests[index]
            client_order_id = self.safe_string(acknowledgement, "client_order_id")
            if client_order_id != request["client_order_id"]:
                raise BadResponse("bifu create orders ACK client_order_id does not match request")
            status = self.safe_string(acknowledgement, "status")
            order_id = self.safe_string(acknowledgement, "order_id")
            if not status:
                raise BadResponse("bifu create orders ACK is missing status")
            if status == "REJECTED":
                if not self.safe_string(acknowledgement, "reject_code"):
                    raise BadResponse("bifu rejected create orders ACK is missing reject_code")
            elif status == "PENDING" and not order_id:
                raise BadResponse("bifu accepted create orders ACK is missing order_id")
            result.append(self._parse_create_ack(acknowledgement, request, market))
        return result

    async def _create_order_request(self, symbol, type, side, amount, price, params):
        params = dict(params or {})
        self._check_supported_params(
            "create_order", params, {"clientOrderId", "cost", "postOnly", "timeInForce"}
        )
        if not isinstance(type, str):
            raise InvalidOrder("bifu order type must be a string")
        if not isinstance(side, str):
            raise InvalidOrder("bifu order side must be a string")
        order_type = type.lower()
        order_side = side.lower()
        if order_type not in ("limit", "market"):
            raise NotSupported("bifu create_order supports limit and market orders only")
        if order_side not in ("buy", "sell"):
            raise InvalidOrder("bifu order side must be buy or sell")
        cost_value = params.get("cost")
        cost_input = self._positive_decimal_string(cost_value) if cost_value is not None else None
        if cost_value is not None and cost_input is None:
            raise InvalidOrder("bifu order cost must be a positive finite value")
        if cost_value is not None and not (order_type == "market" and order_side == "buy"):
            raise InvalidOrder("bifu cost is only supported for market buy orders")
        if order_type == "limit" and price is None:
            raise InvalidOrder("bifu limit orders require a price")
        if order_type == "market" and order_side == "buy" and price is None and cost_input is None:
            raise InvalidOrder(
                "bifu market buy orders require a price or cost to calculate the quote budget"
            )
        amount_input = self._positive_decimal_string(amount)
        price_input = self._positive_decimal_string(price) if price is not None else None
        if amount_input is None or (price is not None and price_input is None):
            raise InvalidOrder("bifu order amount and price must be positive finite values")

        await self.load_markets()
        market = self.market(symbol)
        instrument_id = self.safe_integer(market, "id")
        if instrument_id is None:
            raise BadResponse("bifu market instrument id must be an integer")
        quantity = self.amount_to_precision(symbol, amount_input)
        order_price = self.price_to_precision(symbol, price_input) if price_input else None
        amount_limits = market["limits"]["amount"]
        cost_limits = market["limits"]["cost"]
        if not (order_type == "market" and order_side == "buy"):
            if amount_limits["min"] is not None and Precise.string_lt(
                quantity, str(amount_limits["min"])
            ):
                raise InvalidOrder("bifu order amount is below the market minimum amount")
            if amount_limits["max"] is not None and Precise.string_gt(
                quantity, str(amount_limits["max"])
            ):
                raise InvalidOrder("bifu order amount is above the market maximum amount")
        quote_quantity = None
        if order_type == "limit":
            cost = Precise.string_mul(quantity, order_price)
        elif order_side == "buy":
            cost = cost_input or Precise.string_mul(amount_input, price_input)
            quote_quantity = self.cost_to_precision(symbol, cost)
            cost = quote_quantity
        else:
            cost = None
        if cost is not None:
            if cost_limits["min"] is not None and Precise.string_lt(cost, str(cost_limits["min"])):
                raise InvalidOrder("bifu order value is below the market minimum cost")
            if cost_limits["max"] is not None and Precise.string_gt(cost, str(cost_limits["max"])):
                raise InvalidOrder("bifu order value is above the market maximum cost")
        client_order_id = self.safe_string(params, "clientOrderId") or self.uuid16()
        time_in_force_value = params.get("timeInForce", "IOC" if order_type == "market" else "GTC")
        if not isinstance(time_in_force_value, str):
            raise InvalidOrder("bifu timeInForce must be a string")
        time_in_force = time_in_force_value.upper()
        if time_in_force == "PO":
            time_in_force = "POST_ONLY"
        post_only = self.safe_bool(params, "postOnly", False)
        if order_type == "market" and (post_only or time_in_force == "POST_ONLY"):
            raise InvalidOrder("bifu market orders do not support postOnly")
        if post_only:
            if time_in_force not in ("GTC", "POST_ONLY"):
                raise InvalidOrder("bifu postOnly conflicts with timeInForce")
            time_in_force = "POST_ONLY"
        if order_type == "market" and time_in_force != "IOC":
            raise InvalidOrder("bifu market order timeInForce must be IOC")
        if order_type == "limit" and time_in_force not in ("GTC", "IOC", "FOK", "POST_ONLY"):
            raise InvalidOrder("bifu limit order timeInForce must be GTC, IOC, FOK, or PO")
        request = {
            "instrument_id": instrument_id,
            "side": order_side.upper(),
            "type": order_type.upper(),
            "client_order_id": client_order_id,
            "time_in_force": time_in_force,
        }
        if order_type == "market" and order_side == "buy":
            request["quote_qty"] = quote_quantity
        else:
            request["qty"] = quantity
        if order_type == "limit":
            request["price"] = order_price
        return request, market

    def _parse_create_ack(self, response, request, market):
        order_id = self.safe_string(response, "order_id")
        quote_quantity = self.safe_string(request, "quote_qty")
        order = self.parse_order(
            {
                "order_id": order_id,
                "client_order_id": request["client_order_id"],
                "instrument_id": market["id"],
                "side": request["side"],
                "type": request["type"],
                "time_in_force": request["time_in_force"],
                "price": self.safe_string(request, "price"),
                "orig_qty": "0" if quote_quantity is not None else request.get("qty"),
                "quote_qty": quote_quantity,
                "status": self.safe_string(response, "status"),
            },
            market,
        )
        order["info"] = response
        return order

    async def edit_order(
        self,
        id,
        symbol,
        type,
        side,
        amount=None,
        price=None,
        params=None,
    ):
        request, market = await self._edit_order_request(
            id, symbol, type, side, amount, price, params
        )
        response = await self.private_post_spot_v1_order_amend(request)
        if not isinstance(response, dict):
            raise BadResponse("bifu edit order response must be a JSON object")
        if self.safe_string(response, "order_id") != id:
            raise BadResponse("bifu edit order response order_id does not match request")
        return self._parse_edit_ack(response, request, market, type, side, True)

    async def edit_orders(self, orders, params=None):
        params = dict(params or {})
        self._check_supported_params("edit_orders", params, set())
        if not isinstance(orders, list) or not 1 <= len(orders) <= 100:
            raise ArgumentsRequired("bifu edit_orders requires between 1 and 100 orders")
        symbols = []
        for order in orders:
            if not isinstance(order, dict):
                raise InvalidOrder("bifu edit_orders entries must be objects")
            symbol = self.safe_string(order, "symbol")
            if symbol is None:
                raise ArgumentsRequired("bifu edit_orders requires a symbol for each order")
            symbols.append(symbol)
        if len(set(symbols)) != 1:
            raise NotSupported("bifu edit_orders supports one symbol per batch")

        entries = []
        contexts = []
        market = None
        for order in orders:
            order_params = order.get("params", {})
            if not isinstance(order_params, dict):
                raise InvalidOrder("bifu edit_orders params must be an object")
            request, order_market = await self._edit_order_request(
                self.safe_string(order, "id"),
                self.safe_string(order, "symbol"),
                self.safe_string(order, "type"),
                self.safe_string(order, "side"),
                self.safe_value(order, "amount"),
                self.safe_value(order, "price"),
                order_params,
            )
            market = order_market
            entries.append(self.omit(request, "instrument_id"))
            contexts.append(
                (request, self.safe_string(order, "type"), self.safe_string(order, "side"))
            )

        response = await self.private_post_spot_v1_orders_amend(
            {"instrument_id": self.safe_integer(market, "id"), "entries": entries}
        )
        if not isinstance(response, dict):
            raise BadResponse("bifu edit orders response must be a JSON object")
        acknowledgements = self.safe_list(response, "acks")
        if acknowledgements is None or len(acknowledgements) != len(contexts):
            raise BadResponse("bifu edit orders ACK count does not match request count")
        result = []
        for index, acknowledgement in enumerate(acknowledgements):
            if not isinstance(acknowledgement, dict):
                raise BadResponse("bifu edit orders response has an invalid ACK entry")
            request, order_type, order_side = contexts[index]
            if self.safe_string(acknowledgement, "order_id") != request["order_id"]:
                raise BadResponse("bifu edit orders ACK order_id does not match request")
            accepted = self.safe_bool(acknowledgement, "accepted")
            if accepted is False and not self.safe_string(acknowledgement, "reject_code"):
                raise BadResponse("bifu rejected edit orders ACK is missing reject_code")
            result.append(
                self._parse_edit_ack(
                    acknowledgement,
                    request,
                    market,
                    order_type,
                    order_side,
                    accepted is True,
                )
            )
        return result

    async def _edit_order_request(
        self,
        id,
        symbol,
        type,
        side,
        amount,
        price,
        params,
    ):
        params = dict(params or {})
        trigger_fields = {
            "upper_trigger_price",
            "lower_trigger_price",
            "upper_order_price",
            "lower_order_price",
        }
        self._check_supported_params("edit_order", params, trigger_fields)
        if not isinstance(id, str) or not id:
            raise ArgumentsRequired("bifu edit_order requires a non-empty order id")
        if symbol is None:
            raise ArgumentsRequired("bifu edit_order requires a symbol")
        if not isinstance(type, str) or type.lower() not in ("limit", "trigger"):
            raise NotSupported("bifu edit_order supports limit and trigger orders only")
        if not isinstance(side, str) or side.lower() not in ("buy", "sell"):
            raise InvalidOrder("bifu edit_order side must be buy or sell")
        if amount is None and price is None and not params:
            raise ArgumentsRequired("bifu edit_order requires an amount or price change")

        await self.load_markets()
        market = self.market(symbol)
        instrument_id = self.safe_integer(market, "id")
        if instrument_id is None:
            raise BadResponse("bifu market instrument id must be an integer")
        request = {"instrument_id": instrument_id, "order_id": id}
        if amount is not None:
            amount_input = self._positive_decimal_string(amount)
            if amount_input is None:
                raise InvalidOrder("bifu edit_order amount must be a positive finite value")
            quantity = self.amount_to_precision(symbol, amount_input)
            amount_limits = market["limits"]["amount"]
            if amount_limits["min"] is not None and Precise.string_lt(
                quantity, str(amount_limits["min"])
            ):
                raise InvalidOrder("bifu edit_order amount is below the market minimum amount")
            if amount_limits["max"] is not None and Precise.string_gt(
                quantity, str(amount_limits["max"])
            ):
                raise InvalidOrder("bifu edit_order amount is above the market maximum amount")
            request["qty"] = quantity
        if price is not None:
            price_input = self._positive_decimal_string(price)
            if price_input is None:
                raise InvalidOrder("bifu edit_order price must be a positive finite value")
            order_price = self.price_to_precision(symbol, price_input)
            self._check_edit_limit("price", order_price, market["limits"]["price"])
            request["price"] = order_price
        for field in trigger_fields:
            if field not in params:
                continue
            value = self._nonnegative_decimal_string(params[field])
            if value is None:
                raise InvalidOrder(f"bifu edit_order {field} must be a non-negative value")
            request[field] = (
                "0" if Precise.string_eq(value, "0") else self.price_to_precision(symbol, value)
            )
            if request[field] != "0":
                self._check_edit_limit(field, request[field], market["limits"]["price"])

        if "qty" in request and "price" in request:
            cost = Precise.string_mul(request["qty"], request["price"])
            self._check_edit_limit("cost", cost, market["limits"]["cost"])

        return request, market

    def _check_edit_limit(self, label, value, limits):
        if limits["min"] is not None and Precise.string_lt(value, str(limits["min"])):
            raise InvalidOrder(f"bifu edit_order {label} is below the market minimum {label}")
        if limits["max"] is not None and Precise.string_gt(value, str(limits["max"])):
            raise InvalidOrder(f"bifu edit_order {label} is above the market maximum {label}")

    def _parse_edit_ack(self, response, request, market, type, side, include_request_values):
        order = self.parse_order(
            {
                "order_id": request["order_id"],
                "instrument_id": market["id"],
                "side": side.upper(),
                "type": type.upper(),
                "price": self.safe_string(request, "price") if include_request_values else None,
                "orig_qty": self.safe_string(request, "qty") if include_request_values else None,
            },
            market,
        )
        order["info"] = response
        return order

    async def cancel_order(self, id, symbol=None, params=None):
        params = dict(params or {})
        self._check_supported_params("cancel_order", params, set())
        if symbol is None:
            raise ArgumentsRequired("bifu cancel_order requires a symbol")
        await self.load_markets()
        market = self.market(symbol)
        instrument_id = self.safe_integer(market, "id")
        if instrument_id is None:
            raise BadResponse("bifu market instrument id must be an integer")
        response = await self.private_post_spot_v1_order_cancel(
            {"instrument_id": instrument_id, "order_id": id}
        )
        if not isinstance(response, dict) or self.safe_bool(response, "accepted") is not True:
            raise BadResponse("bifu cancel order response was not accepted")
        return self.safe_order(
            {
                "id": id,
                "symbol": market["symbol"],
                "status": None,
                "info": response,
            },
            market,
        )

    async def cancel_orders(self, ids, symbol=None, params=None):
        params = dict(params or {})
        self._check_supported_params("cancel_orders", params, set())
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
            raise ArgumentsRequired("bifu cancel_orders requires between 1 and 100 order ids")
        if symbol is None:
            raise ArgumentsRequired("bifu cancel_orders requires a symbol")
        if any(not isinstance(order_id, str) or not order_id for order_id in ids):
            raise InvalidOrder("bifu cancel_orders requires non-empty string order ids")
        await self.load_markets()
        market = self.market(symbol)
        instrument_id = self.safe_integer(market, "id")
        if instrument_id is None:
            raise BadResponse("bifu market instrument id must be an integer")
        response = await self.private_post_spot_v1_orders_cancel(
            {"instrument_id": instrument_id, "order_ids": ids}
        )
        accepted = self.safe_bool(response, "accepted") if isinstance(response, dict) else None
        count = self.safe_integer(response, "count") if isinstance(response, dict) else None
        if accepted is not True or count != len(ids):
            raise BadResponse("bifu cancel orders response was not fully accepted")
        return [
            self.safe_order(
                {
                    "id": order_id,
                    "symbol": market["symbol"],
                    "status": None,
                    "info": response,
                },
                market,
            )
            for order_id in ids
        ]

    async def cancel_all_orders(self, symbol=None, params=None):
        params = dict(params or {})
        self._check_supported_params("cancel_all_orders", params, set())
        if symbol is None:
            raise ArgumentsRequired("bifu cancel_all_orders requires a symbol")
        await self.load_markets()
        market = self.market(symbol)
        instrument_id = self.safe_integer(market, "id")
        if instrument_id is None:
            raise BadResponse("bifu market instrument id must be an integer")
        response = await self.private_post_spot_v1_open_orders_cancel(
            {"instrument_id": instrument_id}
        )
        if not isinstance(response, dict):
            raise BadResponse("bifu cancel all orders response must be a JSON object")
        canceled = self.safe_integer(response, "canceled")
        if canceled is None or canceled < 0:
            raise BadResponse("bifu cancel all orders response has an invalid canceled count")
        return [
            self.safe_order(
                {
                    "symbol": market["symbol"],
                    "status": None,
                    "info": response,
                },
                market,
            )
        ]

    async def fetch_order(self, id, symbol=None, params=None):
        params = dict(params or {})
        self._check_supported_params("fetch_order", params, set())
        await self.load_markets()
        response = await self.private_get_spot_v1_order_query({"order_id": id})
        if not isinstance(response, dict):
            raise BadResponse("bifu order response must be a JSON object")
        if self.safe_string(response, "order_id") != id:
            raise BadResponse("bifu order id does not match requested order")
        if symbol is None:
            market = self._market_from_response(response, "order")
        else:
            market = self.market(symbol)
            self._check_instrument(response, market, "order")
        return self.parse_order(response, market)

    async def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None):
        params = dict(params or {})
        self._check_supported_params(
            "fetch_open_orders", params, {"origin", "side", "status", "type"}
        )
        await self.load_markets()
        market = self.market(symbol) if symbol is not None else None
        request = {"instrument_id": market["id"]} if market else {}
        response = await self.private_get_spot_v1_open_orders(self.extend(request, params))
        return self._parse_order_list(response, market, "open orders", since, limit)

    async def fetch_closed_orders(self, symbol=None, since=None, limit=None, params=None):
        params = dict(params or {})
        self._check_supported_params(
            "fetch_closed_orders",
            params,
            {"cursor", "end_ts_ms", "origin", "side", "status", "type"},
        )
        await self.load_markets()
        market = self.market(symbol) if symbol is not None else None
        request = {"instrument_id": market["id"]} if market else {}
        if since is not None:
            request["start_ts_ms"] = since
        if limit is not None:
            request["limit"] = limit
        response = await self.private_get_spot_v1_history_orders(self.extend(request, params))
        return self._parse_order_list(response, market, "history orders", since, limit)

    def _parse_order_list(self, response, market, label, since=None, limit=None):
        if response == {}:
            return []
        orders = self.safe_list(response, "orders")
        if orders is None:
            raise BadResponse(f"bifu {label} response is missing orders")
        parsed = []
        for order in orders:
            if not isinstance(order, dict):
                raise BadResponse(f"bifu {label} response has an invalid order entry")
            order_market = market or self._market_from_response(order, "order")
            if market:
                self._check_instrument(order, market, "order")
            parsed.append(self.parse_order(order, order_market))
        return self.filter_by_since_limit(self.sort_by(parsed, "timestamp"), since, limit)

    async def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None):
        params = dict(params or {})
        self._check_supported_params("fetch_my_trades", params, {"cursor", "end_ts_ms", "order_id"})
        await self.load_markets()
        market = self.market(symbol) if symbol is not None else None
        request = {"instrument_id": market["id"]} if market else {}
        if since is not None:
            request["start_ts_ms"] = since
        if limit is not None:
            request["limit"] = limit
        response = await self.private_get_spot_v1_my_trades(self.extend(request, params))
        if response == {}:
            return []
        trades = self.safe_list(response, "trades")
        if trades is None:
            raise BadResponse("bifu private trades response is missing trades")
        parsed = []
        for trade in trades:
            if not isinstance(trade, dict):
                raise BadResponse("bifu private trades response has an invalid trade entry")
            trade_market = market or self._market_from_response(trade, "trade")
            if market:
                self._check_instrument(trade, market, "trade")
            parsed.append(self.parse_trade(trade, trade_market))
        return self.filter_by_since_limit(self.sort_by(parsed, "timestamp"), since, limit)

    def parse_order(self, order, market=None):
        timestamp = self.safe_integer(order, "created_ts")
        raw_type = self.safe_string(order, "type")
        side = self.safe_string_lower(order, "side")
        time_in_force = self.safe_string(order, "time_in_force")
        amount = self.safe_string(order, "orig_qty")
        quote_amount = self.safe_number(order, "quote_qty")
        quote_amount_market_buy = (
            raw_type in ("MARKET", "SANDBOX_MARKET")
            and side == "buy"
            and self.safe_number(order, "orig_qty") == 0
            and quote_amount is not None
            and quote_amount > 0
        )
        if quote_amount_market_buy:
            amount = None
        parsed = self.safe_order(
            {
                "info": order,
                "id": self.safe_string(order, "order_id"),
                "clientOrderId": self.safe_string(order, "client_order_id"),
                "timestamp": timestamp,
                "datetime": self.iso8601(timestamp),
                "lastTradeTimestamp": None,
                "lastUpdateTimestamp": self.safe_integer(order, "updated_ts"),
                "status": self.parse_order_status(self.safe_string(order, "status")),
                "symbol": market["symbol"] if market else None,
                "type": self.parse_order_type(raw_type),
                "timeInForce": "PO" if time_in_force == "POST_ONLY" else time_in_force,
                "postOnly": time_in_force == "POST_ONLY",
                "side": side,
                "price": self.safe_string(order, "price"),
                "triggerPrice": None,
                "cost": self.safe_string(order, "cum_quote"),
                "average": None,
                "amount": amount,
                "filled": self.safe_string(order, "filled_qty"),
                "remaining": None,
                "trades": None,
                "fee": self._parse_fee(order, "cum_fee"),
                "reduceOnly": None,
            },
            market,
        )
        # CCXT's safe_order derives amount from filled for closed orders. Bifu's
        # quote-amount market buys do not expose the requested base amount, so keep
        # that value unknown instead of turning the filled quantity into the request.
        if quote_amount_market_buy:
            parsed["amount"] = None
            parsed["remaining"] = None
        return parsed

    def parse_order_status(self, status):
        statuses = {
            "NEW": "open",
            "PENDING": "open",
            "OPEN": "open",
            "PARTIALLY_FILLED": "open",
            "CANCELING": "open",
            "FILLED": "closed",
            "CANCELED": "canceled",
            "REJECTED": "rejected",
            "EXPIRED": "expired",
        }
        return self.safe_string(statuses, status)

    def parse_order_type(self, order_type):
        types = {
            "LIMIT": "limit",
            "ICE_LIMIT": "limit",
            "MARKET": "market",
            "SANDBOX_MARKET": "market",
        }
        return self.safe_string(types, order_type, order_type.lower() if order_type else None)

    def _check_supported_params(self, method_name, params, allowed):
        unsupported = sorted(set(params) - allowed)
        if unsupported:
            names = ", ".join(unsupported)
            raise NotSupported(f"bifu {method_name} does not accept params: {names}")

    @staticmethod
    def _positive_decimal_string(value):
        try:
            text = str(value)
            return text if Precise.string_gt(text, "0") else None
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _nonnegative_decimal_string(value):
        try:
            text = str(value)
            return text if Precise.string_ge(text, "0") else None
        except (TypeError, ValueError, OverflowError):
            return None

    def _parse_fee(self, response, cost_key):
        fee_cost = self.safe_number(response, cost_key)
        if fee_cost is None:
            return None
        fee_asset_id = self.safe_string(response, "fee_asset_id")
        assets = getattr(self, "_bifu_assets_by_id", {})
        fee_asset = assets.get(fee_asset_id)
        fee_currency = (
            self.safe_string(fee_asset, "code")
            if fee_asset
            else self.safe_currency_code(fee_asset_id)
        )
        return {"cost": fee_cost, "currency": fee_currency}

    async def fetch_ticker(self, symbol, params=None):
        if params:
            raise NotSupported("bifu fetch_ticker does not accept params")
        await self.load_markets()
        market = self.market(symbol)
        response = await self.public_get_market_v1_ticker({"instrument_id": market["id"]})
        if not isinstance(response, dict):
            raise BadResponse("bifu ticker must be a JSON object")
        if self.safe_string(response, "instrument_id") != market["id"]:
            raise BadResponse("bifu ticker instrument id does not match requested market")
        return self.parse_ticker(response, market)

    async def fetch_bids_asks(self, symbols=None, params=None):
        params = dict(params or {})
        self._check_supported_params("fetch_bids_asks", params, set())
        await self.load_markets()
        requested = list(self.markets) if symbols is None else self.market_symbols(symbols)
        requested = list(dict.fromkeys(requested))
        result = {}
        for symbol in requested:
            market = self.market(symbol)
            response = await self.public_get_market_v1_book_ticker({"instrument_id": market["id"]})
            if not isinstance(response, dict):
                raise BadResponse("bifu book ticker must be a JSON object")
            self._check_instrument(response, market, "book ticker")
            result[market["symbol"]] = self.parse_book_ticker(response, market)
        return result

    def parse_book_ticker(self, response, market=None):
        timestamp = self.safe_integer(response, "book_time")
        if timestamp is None:
            raise BadResponse("bifu book ticker timestamp is invalid")
        ticker = self.safe_ticker(
            {
                "symbol": market["symbol"] if market else None,
                "timestamp": timestamp,
                "datetime": self.iso8601(timestamp),
                "bid": self.safe_number(response, "bid_price"),
                "bidVolume": self.safe_number(response, "bid_qty"),
                "ask": self.safe_number(response, "ask_price"),
                "askVolume": self.safe_number(response, "ask_qty"),
                "info": response,
            },
            market,
        )
        ticker["average"] = None
        return ticker

    async def fetch_tickers(self, symbols=None, params=None):
        params = dict(params or {})
        response_type = params.pop("type", "FULL")
        if response_type != "FULL":
            raise NotSupported("bifu unified fetch_tickers only supports FULL responses")
        await self.load_markets()
        symbols = self.market_symbols(symbols)
        response = await self.public_get_market_v1_tickers(self.extend({"type": "FULL"}, params))
        tickers = self.safe_list(response, "tickers")
        if tickers is None:
            raise BadResponse("bifu tickers response is missing tickers")
        requested_ids = (
            {self.market(symbol)["id"] for symbol in symbols} if symbols is not None else None
        )
        parsed = []
        for raw_ticker in tickers:
            if (
                requested_ids is not None
                and self.safe_string(raw_ticker, "instrument_id") not in requested_ids
            ):
                continue
            market = self._market_from_response(raw_ticker, "ticker")
            parsed.append(self.parse_ticker(raw_ticker, market))
        return self.filter_by_array_tickers(parsed, "symbol", symbols)

    def parse_ticker(self, response, market=None):
        timestamp = self.safe_integer(response, "ts")
        if timestamp is None:
            raise BadResponse("bifu ticker timestamp is invalid")
        ticker = self.safe_ticker(
            {
                "symbol": market["symbol"] if market else None,
                "timestamp": timestamp,
                "datetime": self.iso8601(timestamp),
                "open": self.safe_number(response, "open_price"),
                "high": self.safe_number(response, "high_price"),
                "low": self.safe_number(response, "low_price"),
                "last": self.safe_number(response, "last_price"),
                "change": self.safe_number(response, "price_change"),
                "percentage": self.safe_number(response, "price_change_percent"),
                "baseVolume": self.safe_number(response, "volume"),
                "quoteVolume": self.safe_number(response, "quote_volume"),
                "info": response,
            },
            market,
        )
        # Bifu explicitly uses open=0 with an empty percentage when no percentage can
        # be computed. CCXT's generic safe_ticker omits zero open and derives values,
        # so restore exchange-provided fields and remove the unreported average.
        ticker["open"] = self.safe_number(response, "open_price")
        ticker["percentage"] = self.safe_number(response, "price_change_percent")
        ticker["average"] = None
        return ticker

    async def fetch_order_book(self, symbol, limit=None, params=None):
        await self.load_markets()
        market = self.market(symbol)
        request = {"instrument_id": market["id"]}
        if limit is not None:
            request["limit"] = limit
        response = await self.public_get_market_v1_depth(self.extend(request, params or {}))
        if not isinstance(response, dict):
            raise BadResponse("bifu order book must be a JSON object")
        self._check_instrument(response, market, "order book")
        timestamp = self.safe_integer(response, "book_time")
        order_book = self.parse_order_book(
            response,
            market["symbol"],
            timestamp,
            "bids",
            "asks",
            "price",
            "qty",
        )
        order_book["nonce"] = self.safe_integer(response, "last_id")
        return order_book

    async def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None):
        await self.load_markets()
        market = self.market(symbol)
        if timeframe not in self.timeframes:
            raise NotSupported(f"bifu does not support timeframe {timeframe!r}")
        request = {"instrument_id": market["id"], "period": self.timeframes[timeframe]}
        if limit is not None:
            request["limit"] = limit
        response = await self.public_get_market_v1_klines(self.extend(request, params or {}))
        candles = self.safe_list(response, "klines")
        if candles is None:
            raise BadResponse("bifu klines response is missing klines")
        for candle in candles:
            self._check_instrument(candle, market, "kline")
        return self.parse_ohlcvs(candles, market, timeframe, since, limit)

    def parse_ohlcv(self, ohlcv, market=None):
        return [
            self.safe_integer(ohlcv, "open_time"),
            self.safe_number(ohlcv, "open"),
            self.safe_number(ohlcv, "high"),
            self.safe_number(ohlcv, "low"),
            self.safe_number(ohlcv, "close"),
            self.safe_number(ohlcv, "volume"),
        ]

    async def fetch_trades(self, symbol, since=None, limit=None, params=None):
        await self.load_markets()
        market = self.market(symbol)
        request = {"instrument_id": market["id"]}
        if limit is not None:
            request["limit"] = limit
        response = await self.public_get_market_v1_trades(self.extend(request, params or {}))
        trades = self.safe_list(response, "trades")
        if trades is None:
            raise BadResponse("bifu trades response is missing trades")
        for trade in trades:
            self._check_instrument(trade, market, "trade")
        return self.parse_trades(trades, market, since, limit)

    def parse_trade(self, trade, market=None):
        if "trade_id" in trade:
            timestamp = self.safe_integer(trade, "ts_ms")
            is_maker = self.safe_bool(trade, "is_maker")
            return self.safe_trade(
                {
                    "id": self.safe_string(trade, "trade_id"),
                    "order": self.safe_string(trade, "order_id"),
                    "timestamp": timestamp,
                    "datetime": self.iso8601(timestamp),
                    "symbol": market["symbol"] if market else None,
                    "type": None,
                    "side": self.safe_string_lower(trade, "side"),
                    "takerOrMaker": (
                        "maker" if is_maker else "taker" if is_maker is not None else None
                    ),
                    "price": self.safe_number(trade, "price"),
                    "amount": self.safe_number(trade, "qty"),
                    "cost": None,
                    "fee": self._parse_fee(trade, "fee"),
                    "info": trade,
                },
                market,
            )
        timestamp = self.safe_integer(trade, "ts")
        return self.safe_trade(
            {
                "id": None,
                "order": None,
                "timestamp": timestamp,
                "datetime": self.iso8601(timestamp),
                "symbol": market["symbol"] if market else None,
                "type": None,
                "side": self.safe_string_lower(trade, "taker_side"),
                "takerOrMaker": "taker",
                "price": self.safe_number(trade, "price"),
                "amount": self.safe_number(trade, "qty"),
                "cost": None,
                "fee": None,
                "info": trade,
            },
            market,
        )

    def _market_from_response(self, response, label):
        market_id = self.safe_string(response, "instrument_id")
        markets = self.markets_by_id.get(market_id) if self.markets_by_id else None
        if not markets:
            raise BadResponse(f"bifu {label} references an unknown market")
        return markets[0]

    def _check_instrument(self, response, market, label):
        if self.safe_string(response, "instrument_id") != market["id"]:
            raise BadResponse(f"bifu {label} instrument id does not match requested market")

    def parse_market(self, market, assets_by_id):
        base_id = str(market.get("base_asset_id"))
        quote_id = str(market.get("quote_asset_id"))
        base = assets_by_id.get(base_id)
        quote = assets_by_id.get(quote_id)
        if base is None or quote is None:
            raise BadResponse("bifu market references an unknown asset")
        base_code = self.safe_string(base, "code")
        quote_code = self.safe_string(quote, "code")
        if not base_code or not quote_code:
            raise BadResponse("bifu market asset code is missing")
        instrument_id = self.safe_string(market, "instrument_id")
        if not instrument_id:
            raise BadResponse("bifu market instrument id is missing")
        rules = self.safe_dict(market, "rules", {})
        status = self.safe_string(market, "status")
        active = None if status is None else status == "TRADING"
        return {
            "id": instrument_id,
            "symbol": base_code + "/" + quote_code,
            "base": base_code,
            "quote": quote_code,
            "baseId": base_id,
            "quoteId": quote_id,
            "settle": None,
            "settleId": None,
            "type": "spot",
            "spot": True,
            # Raw metadata includes a margin object for some spots, but that alone does
            # not prove a usable margin-trading contract or implemented account methods.
            "margin": False,
            "swap": False,
            "future": False,
            "option": False,
            "contract": False,
            "contractSize": None,
            "linear": None,
            "inverse": None,
            "active": active,
            "taker": self.safe_number(market, "taker_fee_rate"),
            "maker": self.safe_number(market, "maker_fee_rate"),
            "percentage": True,
            "precision": {
                "amount": self.safe_number(rules, "qty_step"),
                "price": self.safe_number(rules, "price_tick"),
            },
            "limits": {
                "amount": {
                    "min": self.safe_number(rules, "min_qty"),
                    "max": self.safe_number(rules, "max_qty"),
                },
                "price": {"min": None, "max": None},
                "cost": {
                    "min": self.safe_number(rules, "min_notional"),
                    "max": self.safe_number(rules, "max_notional"),
                },
            },
            "info": market,
        }


class BifuPro(BifuREST):
    """Bifu WebSocket adapter using the exchange's URL-based subscriptions."""

    def describe(self):
        return self.deep_extend(
            super().describe(),
            {
                "has": {
                    "ws": True,
                    "watchBidsAsks": True,
                    "watchBalance": True,
                    "watchOHLCV": True,
                    "watchOrderBook": True,
                    "watchMyTrades": True,
                    "watchOrders": True,
                    "watchTicker": True,
                    "watchTickers": True,
                    "watchTrades": True,
                },
                "options": {
                    "depthBufferLimit": 1000,
                    "OHLCVLimit": 1000,
                    "ordersLimit": 1000,
                    "tradesLimit": 1000,
                },
                "streaming": {"keepAlive": 30000},
            },
        )

    async def watch_ticker(self, symbol, params=None):
        if params:
            raise NotSupported("bifu watch_ticker does not accept params")
        return await self._watch_market("ticker", symbol)

    async def watch_tickers(self, symbols=None, params=None):
        if params:
            raise NotSupported("bifu watch_tickers does not accept params")
        await self.load_markets()
        requested = self.market_symbols(symbols)
        tickers = await self._watch_url(
            self._stream_url("/market/v1/stream/tickers"),
            "tickers",
            {"channel": "tickers"},
        )
        if requested is None:
            return dict(tickers)
        return {symbol: tickers[symbol] for symbol in requested if symbol in tickers}

    async def watch_trades(self, symbol, since=None, limit=None, params=None):
        if params:
            raise NotSupported("bifu watch_trades does not accept params")
        trades = await self._watch_market("trade", symbol)
        if self.newUpdates:
            limit = trades.getLimit(symbol, limit)
        return self.filter_by_symbol_since_limit(trades, symbol, since, limit, True)

    async def watch_order_book(self, symbol, limit=None, params=None):
        if params:
            raise NotSupported("bifu watch_order_book does not accept params")
        if limit is not None and limit <= 0:
            raise BadRequest("bifu watch_order_book limit must be positive")
        book = await self._watch_depth(symbol)
        result = dict(book)
        result["bids"] = list(book["bids"])[:limit]
        result["asks"] = list(book["asks"])[:limit]
        return result

    async def watch_bids_asks(self, symbols=None, params=None):
        if params:
            raise NotSupported("bifu watch_bids_asks does not accept params")
        await self.load_markets()
        requested = list(self.markets) if symbols is None else self.market_symbols(symbols)
        requested = list(dict.fromkeys(requested))
        results = await asyncio.gather(
            *(self._watch_market("book_ticker", symbol) for symbol in requested)
        )
        return {ticker["symbol"]: ticker for ticker in results}

    async def watch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None):
        if params:
            raise NotSupported("bifu watch_ohlcv does not accept params")
        if timeframe != "1m":
            raise NotSupported("bifu WebSocket only documents the 1m kline stream")
        candles = await self._watch_market("kline", symbol, timeframe)
        if self.newUpdates:
            limit = candles.getLimit(symbol, limit)
        return self.filter_by_since_limit(candles, since, limit, 0, True)

    async def watch_orders(self, symbol=None, since=None, limit=None, params=None):
        if params:
            raise NotSupported("bifu watch_orders does not accept params")
        if symbol is not None:
            await self.load_markets()
            symbol = self.market(symbol)["symbol"]
        orders = await self._watch_private("orders")
        if self.newUpdates:
            limit = orders.getLimit(symbol, limit)
        return self.filter_by_symbol_since_limit(orders, symbol, since, limit, True)

    async def watch_balance(self, params=None):
        if params:
            raise NotSupported("bifu watch_balance does not accept params")
        return await self._watch_private("balance")

    async def watch_my_trades(self, symbol=None, since=None, limit=None, params=None):
        if params:
            raise NotSupported("bifu watch_my_trades does not accept params")
        if symbol is not None:
            await self.load_markets()
            symbol = self.market(symbol)["symbol"]
        trades = await self._watch_private("myTrades")
        if self.newUpdates:
            limit = trades.getLimit(symbol, limit)
        return self.filter_by_symbol_since_limit(trades, symbol, since, limit, True)

    async def _watch_private(self, message_hash):
        await self.load_markets()
        path = "/spot/v1/userDataStream"
        url = self._stream_url(path)
        headers = self._private_ws_headers(path)
        ws_options = self.options.setdefault("ws", {})
        had_connection_options = "options" in ws_options
        previous_connection_options = ws_options.get("options")
        ws_options["options"] = self.extend(previous_connection_options or {}, {"headers": headers})
        try:
            future = self.watch(
                url,
                message_hash,
                None,
                "private",
                {"channel": "private"},
            )
        finally:
            if had_connection_options:
                ws_options["options"] = previous_connection_options
            else:
                ws_options.pop("options", None)
        try:
            return await future
        except NetworkError as error:
            message = str(error)
            if "401" in message:
                raise AuthenticationError("bifu WebSocket authentication failed") from error
            if "403" in message:
                raise PermissionDenied("bifu WebSocket permission denied") from error
            raise

    def _private_ws_headers(self, path):
        self.check_required_credentials()
        timestamp = str(self.milliseconds())
        payload = "\n".join((timestamp, "GET", path, ""))
        signature = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return {"X-API-KEY": self.apiKey, "X-TS": timestamp, "X-SIGN": signature}

    async def _watch_market(self, channel, symbol, timeframe=None):
        await self.load_markets()
        market = self.market(symbol)
        message_hash = channel + ":" + market["id"]
        if timeframe:
            message_hash += ":" + timeframe
        url = self._stream_url(
            "/market/v1/stream",
            {"instrument": market["id"], "channels": channel},
        )
        return await self._watch_url(
            url,
            message_hash,
            {"channel": channel, "symbol": market["symbol"], "timeframe": timeframe},
        )

    async def _watch_depth(self, symbol):
        await self.load_markets()
        market = self.market(symbol)
        symbol = market["symbol"]
        message_hash = "depth:" + market["id"]
        url = self._stream_url(
            "/market/v1/stream",
            {"instrument": market["id"], "channels": "depth"},
        )
        future = self.watch(
            url,
            message_hash,
            None,
            message_hash,
            {"channel": "depth", "symbol": symbol, "timeframe": None},
        )
        client = self.clients[url]
        states = getattr(self, "_bifu_ws_depth_states", {})
        state = states.get(symbol)
        if symbol not in self.orderbooks and (state is None or state["client"] is not client):
            state = {"buffer": [], "client": client, "error": None, "task": None}
            states[symbol] = state
            self._bifu_ws_depth_states = states
            state["task"] = asyncio.create_task(self._load_depth_snapshot(symbol, client, state))
        task = state["task"] if state is not None else None
        if task is not None:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                error = state.get("error")
                if error is not None:
                    if future.done():
                        future.exception()
                    raise error from None
                raise
            except Exception as error:
                if self.clients.get(url) is client:
                    client.subscriptions.pop(message_hash, None)
                    client.reject(error, message_hash)
                    asyncio.create_task(self._close_for_resync(client))
                if future.done():
                    future.exception()
                raise
        return await future

    async def _load_depth_snapshot(self, symbol, client, state):
        try:
            await client.connected
            states = getattr(self, "_bifu_ws_depth_states", {})
            if states.get(symbol) is not state or not client.isConnected:
                raise ExchangeNotAvailable(
                    "bifu WebSocket disconnected before the REST order book snapshot"
                )
            snapshot = await self.fetch_order_book(symbol)
            states = getattr(self, "_bifu_ws_depth_states", {})
            if states.get(symbol) is not state or not client.isConnected:
                raise ExchangeNotAvailable(
                    "bifu WebSocket disconnected during the REST order book snapshot"
                )
            if snapshot.get("nonce") is None:
                raise BadResponse("bifu REST order book snapshot is missing last_id")
            self.orderbooks[symbol] = self.order_book(snapshot)
            for data, market in state["buffer"]:
                self._apply_depth_update(client, data, market)
        finally:
            states = getattr(self, "_bifu_ws_depth_states", {})
            if states.get(symbol) is state:
                states.pop(symbol, None)

    async def _watch_url(self, url, message_hash, subscription):
        return await self.watch(url, message_hash, None, message_hash, subscription)

    def _stream_url(self, path, params=None):
        base_url = self.urls["api"].get("ws")
        if not base_url:
            raise BadRequest("bifu WebSocket URL is not configured")
        url = base_url.rstrip("/") + "/" + path.lstrip("/")
        if params:
            url += "?" + urlencode(params)
        return url

    def handle_message(self, client, message):
        try:
            if not isinstance(message, dict):
                raise BadResponse("bifu WebSocket frame must be a JSON object")
            message_type = self.safe_string(message, "type")
            data = message.get("data")
            if message_type == "tickers":
                if not isinstance(data, list):
                    raise BadResponse("bifu WebSocket tickers data must be a list")
                tickers = {}
                owners = getattr(self, "_bifu_ws_ticker_owners", {})
                for raw_ticker in data:
                    if not isinstance(raw_ticker, dict):
                        raise BadResponse("bifu WebSocket tickers data has an invalid entry")
                    market_id = self.safe_string(raw_ticker, "instrument_id")
                    markets = self.markets_by_id.get(market_id) if self.markets_by_id else None
                    if not markets:
                        continue
                    market = markets[0]
                    symbol = market["symbol"]
                    ticker = self.parse_ticker(raw_ticker, market)
                    tickers[symbol] = ticker
                    self.tickers[symbol] = ticker
                    owners[symbol] = client.url
                self._bifu_ws_ticker_owners = owners
                client.resolve(tickers, "tickers")
                return
            if not isinstance(data, dict):
                raise BadResponse("bifu WebSocket data must be a JSON object")
            if message_type == "balance_update":
                self._handle_balance_update(client, data)
                return
            if message_type == "margin_balance_update":
                return
            market = self._market_from_response(data, f"WebSocket {message_type}")
            if message_type == "ticker":
                ticker = self.parse_ticker(data, market)
                symbol = market["symbol"]
                self.tickers[symbol] = ticker
                owners = getattr(self, "_bifu_ws_ticker_owners", {})
                owners[symbol] = client.url
                self._bifu_ws_ticker_owners = owners
                client.resolve(ticker, "ticker:" + market["id"])
            elif message_type == "trade":
                symbol = market["symbol"]
                if symbol not in self.trades:
                    self.trades[symbol] = ArrayCache(self.options["tradesLimit"])
                self.trades[symbol].append(self.parse_trade(data, market))
                client.resolve(self.trades[symbol], "trade:" + market["id"])
            elif message_type == "depth":
                self._handle_depth(client, data, market)
            elif message_type == "book_ticker":
                ticker = self.parse_book_ticker(data, market)
                self.bidsasks[market["symbol"]] = ticker
                client.resolve(ticker, "book_ticker:" + market["id"])
            elif message_type == "kline":
                timeframe = self.safe_string(data, "period")
                if timeframe != "1m":
                    raise BadResponse("bifu WebSocket returned an unsupported kline period")
                symbol = market["symbol"]
                self.ohlcvs.setdefault(symbol, {})
                if timeframe not in self.ohlcvs[symbol]:
                    self.ohlcvs[symbol][timeframe] = ArrayCacheByTimestamp(
                        self.options["OHLCVLimit"]
                    )
                candles = self.ohlcvs[symbol][timeframe]
                candles.append(self.parse_ohlcv(data, market))
                client.resolve(candles, "kline:" + market["id"] + ":" + timeframe)
            elif message_type == "order_update":
                if self.orders is None:
                    self.orders = ArrayCacheBySymbolById(self.options["ordersLimit"])
                order_id = self.safe_string(data, "order_id")
                if not order_id:
                    raise BadResponse("bifu WebSocket order update is missing order_id")
                existing = self.orders.hashmap.get(market["symbol"], {}).get(order_id)
                raw_order = data
                if existing is not None:
                    raw_order = self.extend(self.omit(existing["info"], "fill"), data)
                self.orders.append(self.parse_order(raw_order, market))
                client.resolve(self.orders, "orders")
                fill = self.safe_dict(data, "fill")
                if fill and self.safe_string(fill, "trade_id"):
                    if self.myTrades is None:
                        self.myTrades = ArrayCacheBySymbolById(self.options["tradesLimit"])
                    raw_trade = self.extend(
                        fill,
                        {
                            "order_id": self.safe_string(raw_order, "order_id"),
                            "instrument_id": self.safe_integer(raw_order, "instrument_id"),
                            "side": self.safe_string(raw_order, "side"),
                            "ts_ms": self.safe_string(raw_order, "updated_ts"),
                        },
                    )
                    self.myTrades.append(self.parse_trade(raw_trade, market))
                    client.resolve(self.myTrades, "myTrades")
            else:
                raise BadResponse("bifu WebSocket frame has an unknown type")
        except Exception as error:
            if not isinstance(error, BaseError):
                error = BadResponse("Malformed bifu WebSocket payload")
            client.reject(error)

    def _handle_depth(self, client, data, market):
        symbol = market["symbol"]
        state = getattr(self, "_bifu_ws_depth_states", {}).get(symbol)
        if state is not None and state["client"] is client and symbol not in self.orderbooks:
            state["buffer"].append((data, market))
            limit = self.safe_integer(self.options, "depthBufferLimit", 1000)
            if len(state["buffer"]) > limit:
                error = ExchangeNotAvailable(
                    "bifu WebSocket depth buffer exceeded before the REST snapshot"
                )
                state["error"] = error
                task = state.get("task")
                if task is not None and not task.done():
                    task.cancel()
                client.reject(error, "depth:" + market["id"])
                asyncio.create_task(self._close_for_resync(client))
            return
        self._apply_depth_update(client, data, market)

    def _apply_depth_update(self, client, data, market):
        symbol = market["symbol"]
        message_hash = "depth:" + market["id"]
        current = self.orderbooks.get(symbol)
        last_id = self.safe_integer(data, "last_id")
        previous_id = self.safe_integer(data, "prev_id")
        if last_id is None or previous_id is None:
            self.orderbooks.pop(symbol, None)
            client.subscriptions.pop(message_hash, None)
            error = BadResponse("bifu WebSocket depth sequence is invalid")
            client.reject(error, message_hash)
            asyncio.create_task(self._close_for_resync(client))
            raise error
        if current is None:
            raise BadResponse("bifu WebSocket depth update arrived without a REST snapshot")
        if last_id <= current["nonce"]:
            return
        if previous_id != current["nonce"]:
            self.orderbooks.pop(symbol, None)
            client.subscriptions.pop(message_hash, None)
            error = InvalidNonce("bifu WebSocket order book gap; reconnect for a new snapshot")
            client.reject(error, message_hash)
            asyncio.create_task(self._close_for_resync(client))
            raise error
        for side in ("bids", "asks"):
            updates = self.safe_list(data, side, [])
            for update in updates:
                self.handle_delta(
                    current[side],
                    [self.safe_number(update, "price"), self.safe_number(update, "qty")],
                )
        timestamp = self.safe_integer(data, "book_time")
        current["nonce"] = last_id
        current["timestamp"] = timestamp
        current["datetime"] = self.iso8601(timestamp)
        client.resolve(self.orderbooks[symbol], message_hash)

    def handle_delta(self, bookside, delta):
        bookside.store(delta[0], delta[1])

    def _handle_balance_update(self, client, data):
        updates = self.safe_list(data, "balances")
        if updates is None:
            updates = [data]
        stored = getattr(self, "_bifu_ws_balances", {})
        for update in updates:
            asset_id = self.safe_string(update, "asset_id")
            if not asset_id:
                raise BadResponse("bifu WebSocket balance asset id is missing")
            stored[asset_id] = update
        self._bifu_ws_balances = stored
        assets = getattr(self, "_bifu_assets_by_id", {})
        result = {"info": {"balances": list(stored.values())}}
        for asset_id, item in stored.items():
            asset = assets.get(asset_id)
            code = self.safe_string(asset, "code") if asset else self.safe_currency_code(asset_id)
            if not code:
                raise BadResponse("bifu WebSocket balance asset id is unknown")
            result[code] = {
                "free": self.safe_string(item, "available"),
                "used": self.safe_string(item, "frozen"),
            }
        self.balance = self.safe_balance(result)
        client.resolve(self.balance, "balance")

    async def _close_for_resync(self, client):
        await client.close()
        self.clients.pop(client.url, None)

    def _reset_client_state(self, client):
        subscriptions = list(client.subscriptions.values())
        for subscription in subscriptions:
            if not isinstance(subscription, dict):
                continue
            channel = self.safe_string(subscription, "channel")
            symbol = self.safe_string(subscription, "symbol")
            if channel in ("ticker", "tickers"):
                owners = getattr(self, "_bifu_ws_ticker_owners", {})
                for ticker_symbol, owner in list(owners.items()):
                    if owner == client.url:
                        self.tickers.pop(ticker_symbol, None)
                        owners.pop(ticker_symbol, None)
                self._bifu_ws_ticker_owners = owners
            elif channel == "depth" and symbol:
                self.orderbooks.pop(symbol, None)
                states = getattr(self, "_bifu_ws_depth_states", {})
                state = states.get(symbol)
                if state is not None and state["client"] is client:
                    state["error"] = ExchangeNotAvailable(
                        "bifu WebSocket disconnected before the order book snapshot was ready"
                    )
                    task = state.get("task")
                    if task is not None and not task.done():
                        task.cancel()
                    states.pop(symbol, None)
            elif channel == "trade" and symbol:
                self.trades.pop(symbol, None)
            elif channel == "book_ticker" and symbol:
                self.bidsasks.pop(symbol, None)
            elif channel == "kline" and symbol:
                timeframe = self.safe_string(subscription, "timeframe")
                if symbol in self.ohlcvs and timeframe:
                    self.ohlcvs[symbol].pop(timeframe, None)
                    if not self.ohlcvs[symbol]:
                        self.ohlcvs.pop(symbol, None)
            elif channel == "private":
                self.orders = None
                self.myTrades = None
                self.balance = {}
                self._bifu_ws_balances = {}

    def on_close(self, client, error):
        self._reset_client_state(client)
        super().on_close(client, error)

    def on_error(self, client, error):
        self._reset_client_state(client)
        super().on_error(client, error)


BIFU_EXTENSION = Extension("bifu", rest=BifuREST, pro=BifuPro)

__all__ = ["BIFU_EXTENSION", "BifuPro", "BifuREST"]
