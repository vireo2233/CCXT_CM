"""Venue differences for managed spot; ordinary CCXT calls remain ordinary calls."""

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlsplit

import ccxt

from . import account_guards
from .account_guards import (
    _require_bybit_testnet_spot,
    fetch_bybit_testnet_spot_price_band,
    fetch_bybit_testnet_spot_public_window,
    preserve_bybit_wallet_time,
    verify_bybit_testnet_account,
)
from .capabilities import require_capabilities
from .order_lookup import (
    ClientOrderLookupUnconfirmed,
    OriginalLookup,
    fetch_bybit_testnet_spot_order_by_client_id,
    fetch_open_orders_complete,
    order_error_diagnostic,
)
from .registry import exchange_class, list_exchanges


def _decimal(value):
    if value is None:
        return None
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite decimal")
    return result


@dataclass(frozen=True)
class SpotMarket:
    exchange: str
    exchange_label: str
    environment: str
    environment_label: str
    uid_pattern: str
    market_type: str = "spot"
    legacy_default: bool = False
    public_trade_window: bool = False
    limit_pair: bool = False
    manual_limit: bool = False
    base_market_order: bool = False
    same_run_handoff: bool = False

    def valid_uid(self, uid):
        return isinstance(uid, str) and re.fullmatch(self.uid_pattern, uid) is not None


class SpotExecution:
    """Default only uses unified methods; no completeness promises by inference."""

    market = None
    normal_mm_orders = False
    private_stream = True
    paged_private_reads = frozenset()
    supports_dispatch_fence = False
    requires_wallet_time = False
    native_cash_proof = False
    allows_open_order_without_client_id = False

    def configure(self, client, environment):
        pass

    def bind(self, client, environment):
        pass

    async def verify_account(self, client, uid, call):
        raise ccxt.NotSupported("Spot account identity verification is not qualified")

    def order_diagnostic(self, exc):
        return {"error_type": type(exc).__name__, "reason": "response_unconfirmed"}

    async def fetch_original(self, client, call, symbol, oid, cid, slots, timeout, *, qualified):
        if not oid:
            raise ccxt.NotSupported("Exact client-order lookup is not qualified")
        return await call("fetch_order", oid, symbol, private=True), False

    async def cancel_original(self, client, call, symbol, oid):
        return await call("cancel_order", oid, symbol, private=True)

    async def open_directory(self, client, call, symbol):
        return None  # No proof that a unified fetch_open_orders response is complete.

    async def private_window(self, client, call, symbol, start, end, limit, native=None):
        return None

    async def account_fee(self, client, call, market, slots):
        return await call("fetch_trading_fee", market["symbol"], private=True)

    def order_fields(self, raw, intent, *, bound):
        return raw

    def cancel_ack(self, raw, symbol, order_id, *, bound):
        return None

    def cancellation_evidence(self, client, raw, symbol, *, bound):
        return {}

    async def original_evidence(self, client, market, read_pages, **scope):
        return {"outcome": OriginalLookup.UNCONFIRMED}

    async def native_read(self, client, slots, method, params):
        raise ccxt.NotSupported("Native spot audit is not qualified")

    async def price_band(self, client, market):
        raise ccxt.NotSupported("Spot price band is not qualified")

    async def public_window(self, client, symbol, start, end):
        return None

    def stream_status(self, client):
        return None

    def wallet_stream_complete(self, raw):
        return True

    def wallet_fields(self, client, raw, *, environment, bound, identity, logger):
        return raw

    def trade_fields(self, trade):
        return trade


class BybitSpot(SpotExecution):
    market = SpotMarket(
        "bybit",
        "Bybit",
        "sandbox",
        "测试环境（Testnet）",
        r"[0-9]{1,32}",
        legacy_default=True,
        public_trade_window=True,
        limit_pair=True,
        manual_limit=True,
        base_market_order=True,
        same_run_handoff=True,
    )
    normal_mm_orders = supports_dispatch_fence = requires_wallet_time = True
    allows_open_order_without_client_id = True
    native_cash_proof = True
    paged_private_reads = frozenset({"get_open_orders", "get_transaction_history"})

    def configure(self, client, environment):
        preserve_bybit_wallet_time(client)

    async def verify_account(self, client, uid, call):
        return await verify_bybit_testnet_account(client, uid)

    def matching_identity(self, identity):
        return (
            isinstance(identity, dict)
            and identity.get("environment") == self.market.environment
            and identity.get("market_type") == self.market.market_type
            and type(identity.get("unified_margin_status")) is int
            and identity["unified_margin_status"] in (3, 4, 5, 6)
            and identity.get("margin_mode")
            in ("ISOLATED_MARGIN", "REGULAR_MARGIN", "PORTFOLIO_MARGIN")
            and identity.get("spot_price_auto_adjustment") is False
            and identity.get("spot_mnt_fee_deduction") is False
            and self.market.valid_uid(identity.get("uid"))
        )

    async def verify_matching_mode(self, client, identity):
        mode = await account_guards.fetch_bybit_testnet_spot_account_mode(client)
        if (
            type(mode.get("unified_margin_status")) is not int
            or mode["unified_margin_status"] != identity["unified_margin_status"]
            or mode.get("margin_mode") != identity["margin_mode"]
            or mode.get("margin_mode")
            not in ("ISOLATED_MARGIN", "REGULAR_MARGIN", "PORTFOLIO_MARGIN")
        ):
            raise ValueError("Spot matching mode changed")

    async def read_matching_settings(self, client):
        settings = await account_guards.fetch_bybit_testnet_spot_smp_settings(client)
        if (
            type(settings.get("smp_type")) is not int
            or settings["smp_type"] != 0
            or type(settings.get("smp_group")) is not int
            or settings["smp_group"] < 0
            or type(settings.get("source_ms")) is not int
            or not 0 <= client.milliseconds() - settings["source_ms"] <= 10000
        ):
            raise ValueError("Spot matching settings unconfirmed")
        return settings

    order_diagnostic = staticmethod(order_error_diagnostic)

    async def fetch_original(self, client, call, symbol, oid, cid, slots, timeout, *, qualified):
        exact = bool(cid and qualified and getattr(client, "isSandboxModeEnabled", False) is True)
        deadline = asyncio.get_running_loop().time() + timeout if exact else None
        if oid:
            try:
                async with asyncio.timeout_at(deadline):
                    raw = await call(
                        "fetch_order", oid, symbol, private=True, params={"acknowledged": True}
                    )
                return raw, False
            except ccxt.OrderNotFound:
                if not exact:
                    raise
        if not exact:
            raise ccxt.NotSupported("Exact client-order lookup is not qualified")
        async with asyncio.timeout_at(deadline), slots:
            client.check_required_credentials()
            raw = await fetch_bybit_testnet_spot_order_by_client_id(
                client,
                symbol,
                cid,
                expected_order_id=str(oid) if oid else None,
            )
        return raw, True

    async def open_directory(self, client, call, symbol):
        return await fetch_open_orders_complete(client, symbol)

    def order_fields(self, raw, intent, *, bound):
        info = raw.get("info") or {}
        tif, native = raw.get("timeInForce"), info.get("timeInForce")
        tif = "PO" if tif == "PostOnly" else tif
        native = "PO" if native == "PostOnly" else native
        if tif is not None and native is not None and tif != native:
            raise ValueError("订单有效期的原生与标准字段冲突")
        updates = {
            "timeInForce": tif if tif is not None else native,
            "lastUpdateTimestamp": raw.get("lastUpdateTimestamp") or info.get("updatedTime"),
        }
        kind = raw.get("type") or intent.get("type")
        cid = raw.get("clientOrderId") or (intent.get("params") or {}).get("clientOrderId")
        if bound and kind == "market" and isinstance(cid, str) and cid.startswith("amm-b-"):
            if not intent or info.get("orderType") is not None:
                if (
                    info.get("orderType") != "Market"
                    or info.get("marketUnit") != "baseCoin"
                    or not isinstance(info.get("qty"), str)
                    or _decimal(info["qty"]) <= 0
                ):
                    raise ValueError("市价原单缺少可确认的币数量口径")
                updates["amount"] = info["qty"]
            else:
                updates["amount"] = intent["amount"]
            updates["price"] = None
        return dict(raw, **updates)

    def cancel_ack(self, raw, symbol, order_id, *, bound):
        if not bound:
            return None
        info = raw.get("info") if isinstance(raw, dict) else None
        if (
            not isinstance(info, dict)
            or raw.get("id") != str(order_id)
            or info.get("orderId") != str(order_id)
            or not isinstance(info.get("orderLinkId"), str)
            or raw.get("clientOrderId") not in (None, info["orderLinkId"])
            or raw.get("symbol") not in (None, symbol)
        ):
            raise ValueError("撤单回执原订单身份不符")
        return info["orderLinkId"]

    @staticmethod
    def cancel_ack_evidence(oid, cid):
        return {"bybit_cancel_ack": {"order_id": str(oid), "client_order_id": cid}}

    def cancellation_evidence(self, client, raw, symbol, *, bound):
        info = raw.get("info")
        if (
            not bound
            or not isinstance(info, dict)
            or info.get("cancelType")
            not in (
                "CancelByUser",
                "CancelBySmp",
            )
        ):
            return {}
        oid, cid, status = raw.get("id"), raw.get("clientOrderId"), raw.get("status")
        try:
            fields = self.order_fields(raw, {}, bound=bound)
            if (
                not isinstance(cid, str)
                or not cid
                or raw.get("type") != "limit"
                or info.get("orderId") != oid
                or info.get("orderLinkId") != cid
                or info.get("symbol") != client.market(symbol)["id"]
                or (info.get("orderStatus"), status)
                not in (
                    ("Cancelled", "canceled"),
                    ("PartiallyFilledCanceled", "closed"),
                )
                or info.get("side") != {"buy": "Buy", "sell": "Sell"}.get(raw.get("side"))
                or info.get("orderType") != "Limit"
                or {"PostOnly": "PO"}.get(info.get("timeInForce"), info.get("timeInForce"))
                != fields["timeInForce"]
                or fields["timeInForce"] not in ("GTC", "IOC", "FOK", "PO")
                or any(not isinstance(info.get(k), str) for k in ("qty", "price", "cumExecQty"))
            ):
                return {}
            qty, price, filled = (_decimal(info[k]) for k in ("qty", "price", "cumExecQty"))
            if (
                qty != _decimal(raw.get("amount"))
                or price != _decimal(raw.get("price"))
                or filled != _decimal(raw.get("filled"))
                or not (qty > 0 and price > 0 and 0 <= filled <= qty)
            ):
                return {}
            times = [info.get(k) for k in ("createdTime", "updatedTime")]
            if any(not isinstance(t, str) or not re.fullmatch(r"[0-9]+", t) for t in times):
                return {}
            created, updated = map(int, times)
            now = client.milliseconds()
            if (
                type(now) is not int
                or not 0 < created <= updated <= now
                or type(raw.get("timestamp")) is not int
                or raw["timestamp"] != created
                or type(raw.get("lastUpdateTimestamp")) is not int
                or raw["lastUpdateTimestamp"] != updated
                or raw.get("lastTradeTimestamp") is not None
                and (
                    type(raw["lastTradeTimestamp"]) is not int
                    or raw["lastTradeTimestamp"] != updated
                )
            ):
                return {}
        except (ArithmeticError, ValueError, TypeError):
            return {}
        return {
            "bybit_order_cancellation": {
                "symbol": symbol,
                "order_id": oid,
                "client_order_id": cid,
                "status": status,
                "native_status": info["orderStatus"],
                "cancel_type": info["cancelType"],
                "updated_at_ms": updated,
            }
        }

    def market_order_params(self, cid):
        return {
            "clientOrderId": cid,
            "marketUnit": "baseCoin",
            "isLeverage": 0,
            "timeInForce": "IOC",
        }

    def market_amount_limit(self, market):
        lot = (market.get("info") or {}).get("lotSizeFilter") or {}
        amount = _decimal(lot.get("maxMarketOrderQty"))
        if amount is None or amount <= 0:
            raise ValueError("Spot market quantity limit missing")
        return amount

    async def price_band(self, client, market):
        data, raw = await fetch_bybit_testnet_spot_price_band(client, market["symbol"])
        return dict(data, max_market_amount=self.market_amount_limit(market)), raw

    async def public_window(self, client, symbol, start, end):
        proof = await fetch_bybit_testnet_spot_public_window(
            client, symbol, start_ms=start, end_ms=end
        )
        rows = [
            {
                "id": r["execId"],
                "symbol": symbol,
                "timestamp": int(r["time"]),
                "price": r["price"],
                "amount": r["size"],
                "side": r["side"].lower(),
            }
            for r in proof["native_response"]["result"]["list"]
        ]
        return rows, proof["complete"], {"bybit_public_window": proof}

    def wallet_fields(self, client, raw, *, environment, bound, identity, logger):
        # UNIFIED 的 equity 包含衍生品/借贷，不能代替现货 walletBalance。
        info = raw.get("info") if isinstance(raw, dict) else None
        if isinstance(info, dict):
            accounts = (info.get("result") or {}).get("list")
            timestamp = info.get("time")
        else:
            accounts = info
            # The SDK preserves wallet creationTime in the standard timestamp.
            # A missing source time remains missing; never substitute receive time.
            timestamp = raw.get("timestamp") if isinstance(raw, dict) else None
        if not isinstance(accounts, list) or not accounts:
            raise ccxt.ExchangeError("现货账户余额缺少原始账户明细")
        payload = {"free": {}, "used": {}, "total": {}, "timestamp": timestamp}
        not_applicable_interest = 0
        not_applicable_margin = 0
        for account in accounts:
            if not isinstance(account, dict) or account.get("accountType") != "UNIFIED":
                raise ccxt.NotSupported("NotSupported: 仅接入独立 UNIFIED 现货账户")
            coins = account.get("coin")
            if not isinstance(coins, list):
                raise ccxt.ExchangeError("现货账户余额缺少币种明细")
            for coin in coins:
                if not isinstance(coin, dict) or not isinstance(coin.get("coin"), str):
                    raise ccxt.ExchangeError("现货余额币种错误")
                currency = client.safe_currency_code(coin["coin"])
                if not currency or currency in payload["total"]:
                    raise ccxt.ExchangeError("现货余额币种缺失或重复")
                for field in (
                    "spotBorrow",
                    "borrowAmount",
                    "totalOrderIM",
                    "totalPositionIM",
                    "unrealisedPnl",
                    "bonus",
                    "spotHedgingQty",
                ):
                    if _decimal(coin.get(field)) != 0:
                        raise ccxt.NotSupported(
                            "NotSupported: 现货账户含借贷、保证金、衍生品或未核清资产"
                        )
                total = _decimal(coin.get("walletBalance"))
                locked = _decimal(coin.get("locked"))
                equity = _decimal(coin.get("equity"))
                if total is None or locked is None or equity != total or not 0 <= locked <= total:
                    raise ccxt.ExchangeError("现货余额口径不完整或权益与钱包不一致")
                if coin.get("accruedInterest") == "" and (
                    environment == self.market.environment and bound
                ):
                    # Only the observed Testnet debt-free case; missing/empty
                    # debt or margin evidence is still unknown, never zero.
                    if (
                        any(
                            not isinstance(other, dict)
                            or _decimal(other.get("totalPositionMM")) != 0
                            for other in coins
                        )
                        or _decimal(account.get("totalPerpUPL")) != 0
                    ):
                        raise ccxt.NotSupported("NotSupported: 空利息缺少独立无保证金占用证据")
                    for field in ("totalInitialMargin", "totalMaintenanceMargin"):
                        if account.get(field) == "" and (
                            (identity or {}).get("margin_mode") == "ISOLATED_MARGIN"
                        ):
                            # Official wallet docs: account-wide fields are not
                            # applicable to isolated margin. Do not turn them to zero.
                            not_applicable_margin += 1
                        elif _decimal(account.get(field)) != 0:
                            raise ccxt.NotSupported(
                                "NotSupported: 空利息缺少已核验的账户保证金证据"
                            )
                    not_applicable_interest += 1
                elif _decimal(coin.get("accruedInterest")) != 0:
                    raise ccxt.NotSupported("NotSupported: 现货利息非零或尚未核清")
                payload["total"][currency] = total
                payload["used"][currency] = locked
                payload["free"][currency] = total - locked
        if not_applicable_interest and logger is not None:
            # Fixed audit text plus a local count, never raw coins, amounts or secrets.
            logger.info(
                f"Bybit Testnet spot balance audit: {not_applicable_interest} empty "
                "accruedInterest interpreted as not_applicable; spotBorrow, borrowAmount, "
                "totalOrderIM, totalPositionIM, totalPositionMM, unrealisedPnl, bonus, "
                "spotHedgingQty, totalPerpUPL explicitly zero; walletBalance=equity; "
                "0<=locked<=walletBalance; account totalInitialMargin/totalMaintenanceMargin "
                "explicitly zero except "
                f"{not_applicable_margin} not_applicable fields under verified ISOLATED_MARGIN"
            )
        return payload

    async def private_window(self, client, call, symbol, start, end, limit, native=None):
        # Physical windows respect the venue's seven-day bound; logical history
        # is complete only after every cursor chain explicitly terminates.
        end = end if end is not None else client.milliseconds() + 1
        if start is not None and start >= end:
            raise ValueError("私有成交核账窗口为空或在未来")
        window = 7 * 24 * 60 * 60 * 1000
        if native is not None:
            # Unified CCXT drops an empty final cursor on a nonempty page.
            # Inspect the actual terminal cursor for verified private accounts.
            market = client.market(symbol)
            rows, identities = [], set()
            for lo in range(start if start is not None else end - 1, end, window):
                query = {
                    "category": "spot",
                    "symbol": market["id"],
                    "execType": "Trade",
                    "endTime": min(end, lo + window) - 1,
                    "limit": min(limit, 100),
                }
                if start is not None:
                    query["startTime"] = lo
                page, _ = await self.read_native_pages(
                    native,
                    "private_get_v5_execution_list",
                    query,
                    timeout=client.timeout / 1000,
                )
                for row in page:
                    trade = client.parse_trade(row, market)
                    key = (trade.get("order"), trade.get("id"))
                    at = trade.get("timestamp")
                    if (
                        not all(isinstance(v, str) and v for v in key)
                        or key in identities
                        or row.get("symbol") != market["id"]
                        or trade.get("symbol") != symbol
                        or type(at) is not int
                        or at > query["endTime"]
                        or (start is not None and at < lo)
                        or len(rows) >= 8192
                    ):
                        raise ccxt.ExchangeError(
                            "Private execution page identity or range unconfirmed"
                        )
                    identities.add(key)
                    rows.append(trade)
            return rows, start is not None, end
        since, rows, identified = start, [], {}
        while since is None or since < end:
            until = end if since is None else min(end, since + window)
            params, cursors = {"endTime": until - 1}, set()
            while True:
                async with asyncio.timeout(client.timeout / 1000):
                    page = await call(
                        "fetch_my_trades",
                        symbol,
                        since=since,
                        limit=min(limit, 100),
                        params=dict(params),
                        private=True,
                    )
                if not isinstance(page, list) or len(page) > min(limit, 100):
                    raise ccxt.ExchangeError("私有成交页格式或容量错误")
                cursor = None
                for row in page:
                    if not isinstance(row, dict) or not row.get("id") or not row.get("order"):
                        raise ccxt.ExchangeError("私有成交缺身份")
                    key = (row["order"], row["id"])
                    if key in identified:
                        # CCXT's embedded nextPageCursor is transport metadata.
                        old = dict(
                            identified[key],
                            info={
                                k: v
                                for k, v in identified[key].get("info", {}).items()
                                if k != "nextPageCursor"
                            },
                        )
                        new = dict(
                            row,
                            info={
                                k: v
                                for k, v in row.get("info", {}).items()
                                if k != "nextPageCursor"
                            },
                        )
                        if old != new:
                            raise ccxt.ExchangeError("私有成交重复身份冲突")
                    else:
                        if len(rows) >= 8192:
                            raise ccxt.ExchangeError("近期成交超出当前批次容量，完整性未确认")
                        identified[key] = row
                        rows.append(row)
                    info = row.get("info")
                    if isinstance(info, dict) and "nextPageCursor" in info:
                        value = info["nextPageCursor"]
                        if cursor is not None and cursor != value:
                            raise ccxt.ExchangeError("私有成交续页凭证冲突")
                        cursor = value
                if not page or cursor == "":
                    break
                if not isinstance(cursor, str) or cursor in cursors:
                    raise ccxt.ExchangeError("私有成交页缺少有效续页凭证")
                cursors.add(cursor)
                params["cursor"] = cursor
            if since is None:
                break
            since = until
        return rows, start is not None, end

    def stream_status(self, client):
        status = {"connected": False, "session": None}
        if client is None:
            return status
        for ws in client.clients.values():
            if not str(getattr(ws, "url", "")).endswith("/v5/private"):
                continue
            try:
                status["session"] = (id(ws), ws.connectionEstablished)
                auth = ws.futures.get("authenticated")
                topics = ws.subscriptions
                recent = max(ws.lastPong or 0, ws.last_message_at or 0)
                age = client.milliseconds() - recent
                status["connected"] = bool(
                    ws.isConnected is True
                    and ws.error is None
                    and ws.connection is not None
                    and not ws.connection.closed
                    and auth is not None
                    and auth.done()
                    and not auth.cancelled()
                    and auth.result() is True
                    and not ws.rejections
                    and "balances:unified" in topics
                    and any(key.startswith("orders:") for key in topics)
                    and any(key.startswith("myTrades:") for key in topics)
                    and 0 <= age <= ws.keepAlive * ws.maxPingPongMisses
                )
            except Exception:
                status["connected"] = False
            return status
        return status

    def wallet_stream_complete(self, raw):
        info = raw.get("info") if isinstance(raw, dict) else None
        result = info.get("result") if isinstance(info, dict) else None
        coins = result.get("list") if isinstance(result, dict) else info
        if (
            not isinstance(coins, list)
            or any("locked" not in coin for account in coins for coin in account.get("coin", []))
            or (isinstance(info, list) and raw.get("timestamp") is None)
        ):
            return False
        return True

    async def account_fee(self, client, call, market, slots):
        raw = await super().account_fee(client, call, market, slots)
        if client.isSandboxModeEnabled is True:
            # Native V5 fee-rate fields are current percentage rates. Pinned
            # CCXT preserves them in info but leaves percentage/tierBased None.
            # This establishes no all-fee or lifetime reservation capability.
            info = raw.get("info")
            if (
                (raw.get("percentage") is not None and raw.get("percentage") is not True)
                or (raw.get("tierBased") is not None and raw.get("tierBased") is not False)
                or not isinstance(info, dict)
                or info.get("symbol") != market["id"]
                or any(
                    not isinstance(info.get(field), str) or not info[field].strip()
                    for field in ("makerFeeRate", "takerFeeRate")
                )
            ):
                raise ccxt.ExchangeError("Bybit 当前百分比费率缺少原生字段或身份凭证")
            maker, taker = (_decimal(info[field]) for field in ("makerFeeRate", "takerFeeRate"))
            raw = dict(raw, percentage=True, tierBased=False, maker=maker, taker=taker)
        return raw

    def trade_fields(self, trade):
        # 不沿用 SDK 按买卖方向猜测的费用币种，也不忽略附加费用。
        info = trade.get("info") or {}
        currency = info.get("feeCurrency") or info.get("feeCoin")
        trade = dict(trade, clientOrderId=info.get("orderLinkId") or None)
        trade["fees"] = (
            [{"currency": currency, "cost": info["execFee"]}]
            if isinstance(currency, str)
            and currency.strip()
            and info.get("execFee") is not None
            and not info.get("extraFees")
            else []
        )
        trade["fee"] = None
        return trade

    async def native_read(self, client, slots, method, params):
        if client is None or method not in {
            "private_get_v5_execution_list",
            "private_get_v5_order_history",
            "private_get_v5_order_realtime",
            "private_get_v5_account_transaction_log",
        }:
            raise ValueError("bybit_settlement:native_scope_unconfirmed")
        _require_bybit_testnet_spot(client)
        client.check_required_credentials()
        endpoint = getattr(client, method, None)
        if not callable(endpoint):
            raise ccxt.NotSupported("Native audit endpoint not qualified")
        async with slots:
            return await endpoint(dict(params))

    async def read_native_pages(self, fetch, method, query, *, timeout):
        """Original native rows, without first-row parsing or truncated success."""
        rows, pages, seen, cursor = [], [], set(), None
        while True:
            params = dict(query)
            if cursor is not None:
                params["cursor"] = cursor
            try:
                async with asyncio.timeout(timeout):
                    response = await fetch(method, params)
            except Exception:
                raise ValueError("bybit_settlement:native_read_failed") from None
            result = response.get("result") if isinstance(response, dict) else None
            if (
                not isinstance(response, dict)
                or type(response.get("retCode")) is not int
                or response["retCode"] != 0
                or not isinstance(result, dict)
                or result.get("category") != "spot"
                or not isinstance(result.get("list"), list)
                or len(result["list"]) > query["limit"]
                or any(not isinstance(row, dict) for row in result["list"])
                or not isinstance(result.get("nextPageCursor"), str)
            ):
                raise ValueError("bybit_settlement:native_page_unconfirmed")
            identities = [
                (row.get("orderId"), row.get("execId"))
                if method.endswith("execution_list")
                else row.get("orderId")
                for row in result["list"]
            ]
            if len(set(identities)) != len(identities):
                raise ValueError("bybit_settlement:native_page_unconfirmed")
            pages.append({"cursor": cursor, "response": response})
            rows.extend(result["list"])
            following = result["nextPageCursor"]
            if following == "":
                return rows, {"query": dict(query), "pages": pages}
            if not result["list"] or following in seen:
                raise ValueError("bybit_settlement:native_cursor_loop")
            seen.add(following)
            cursor = following

    async def original_evidence(
        self, client, market, read_pages, *, client_order_id, start_ms, end_ms
    ):
        """One uncached exact audit: open orders, historical orders and private executions."""
        day = 86400000
        if (
            not isinstance(client_order_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", client_order_id)
            or type(start_ms) is not int
            or type(end_ms) is not int
            or not 0 < start_ms < end_ms
            or end_ms - start_ms > 730 * day
        ):
            raise ValueError("topup evidence scope unconfirmed")
        _require_bybit_testnet_spot(client)
        scope = {
            "category": "spot",
            "symbol": market["id"],
            "orderLinkId": client_order_id,
            "limit": 50,
        }
        orders, executions, times = {}, {}, []
        complete = True

        async def read(method, query, target, time_field, id_field):
            nonlocal complete
            try:
                rows, proof = await read_pages(method, query)
                stamps = [p["response"].get("time") for p in proof["pages"]]
                received_ms = time.time_ns() // 1000000
                if not stamps or any(
                    type(t) is not int or not end_ms <= t <= received_ms + 5000 for t in stamps
                ):
                    raise ValueError("stale native evidence")
                begin, end = query.get("startTime", start_ms), query.get("endTime", max(stamps))
                for row in rows:
                    when = row.get(time_field)
                    if (
                        row.get("symbol") != market["id"]
                        or row.get("orderLinkId") != client_order_id
                        or any(
                            not isinstance(row.get(k), str) or not row[k]
                            for k in ("orderId", id_field)
                        )
                        or not isinstance(when, str)
                        or not when.isascii()
                        or not when.isdigit()
                        or not begin <= int(when) <= end
                    ):
                        raise ValueError("native evidence identity or range changed")
                    if id_field == "execId" and target.get(row[id_field], row) != row:
                        raise ccxt.ExchangeError("native execution evidence conflicts")
                    target.setdefault(row[id_field], row)
                times.extend(stamps)
            except ValueError:
                complete = False  # A failed read can never prove absence.

        order_scope = dict(scope, orderFilter="Order")
        await read("private_get_v5_order_realtime", order_scope, orders, "createdTime", "orderId")
        for start in range(start_ms, end_ms, 7 * day):
            bounds = {"startTime": start, "endTime": min(end_ms, start + 7 * day)}
            await read(
                "private_get_v5_order_history",
                dict(order_scope, **bounds),
                orders,
                "createdTime",
                "orderId",
            )
            await read(
                "private_get_v5_execution_list",
                dict(scope, **bounds),
                executions,
                "execTime",
                "execId",
            )
        if len(orders.keys() | {r["orderId"] for r in executions.values()}) > 1:
            raise ValueError("topup original identity is not unique")
        if orders or executions:
            return {
                "order": client.parse_order(next(iter(orders.values())), market)
                if orders
                else None,
                "trades": [client.parse_trade(row, market) for row in executions.values()],
                "complete": complete,
                "outcome": OriginalLookup.FOUND,
            }
        if complete:
            return {
                "absence": dict(
                    start_ms=start_ms, end_ms=end_ms, first_ms=min(times), last_ms=max(times)
                ),
                "outcome": OriginalLookup.ABSENT,
            }
        return {"outcome": OriginalLookup.UNCONFIRMED}

    async def run_order_history(self, client, market, read_pages, *, prefix, start_ms, end_ms):
        try:
            rows, segments, seen, start = [], [], {}, start_ms
            while start < end_ms:
                until = min(end_ms, start + 7 * 86400000)
                query = {
                    "category": "spot",
                    "symbol": market["id"],
                    "startTime": start,
                    "endTime": until,
                    "limit": 50,
                }
                values, proof = await read_pages("private_get_v5_order_history", query)
                segments.append(proof)
                for row in values:
                    oid = row.get("orderId")
                    if not isinstance(oid, str) or not oid:
                        raise ValueError
                    if oid in seen:
                        if seen[oid] != row:
                            raise ValueError
                        continue
                    seen[oid] = row
                    rows.append(row)
                start = until
            owned = {}
            for row in rows:
                if row.get("symbol") != market["id"]:
                    raise ValueError
                cid = row.get("orderLinkId")
                if not isinstance(cid, str) or not cid.startswith(prefix):
                    continue
                if cid in owned:
                    raise ValueError
                parsed = client.parse_order(row, market)
                if (
                    parsed.get("id") != row.get("orderId")
                    or parsed.get("clientOrderId") != cid
                    or type(parsed.get("timestamp")) is not int
                    or not start_ms <= parsed["timestamp"] <= end_ms
                ):
                    raise ValueError
                owned[cid] = parsed
            pages = segments[0] if len(segments) == 1 else {"segments": segments}
            return {"orders": owned, "native_pages": pages}
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ValueError("bybit_settlement:history_unconfirmed") from None

    async def cash_windows(self, market, read_pages, fetch, *, start_ms, end, timeout):
        from .bybit_cash import collect_bybit_transaction_pages

        native, execution_segments, seen = [], [], {}
        start = start_ms
        while start < end:
            until = min(end, start + 7 * 24 * 60 * 60 * 1000)
            query = {
                "category": "spot",
                "symbol": market["id"],
                "startTime": start,
                "endTime": until,
                "limit": 100,
            }
            values, proof = await read_pages("private_get_v5_execution_list", query)
            execution_segments.append(proof)
            for row in values:
                # A checkpoint's boundary has already been accounted for.
                if (
                    isinstance(row.get("execTime"), str)
                    and row["execTime"].isdigit()
                    and int(row["execTime"]) == start_ms
                ):
                    continue
                key = (row.get("orderId"), row.get("execId"))
                if key in seen:
                    if seen[key] != row:
                        raise ValueError("bybit_settlement:execution_facts_changed")
                    continue
                seen[key] = row
                native.append(row)
            start = until
        execution_evidence = (
            execution_segments[0]
            if len(execution_segments) == 1
            else {"segments": execution_segments}
        )

        async def cash_page(params):
            return await fetch("private_get_v5_account_transaction_log", params)

        cash_pages = await collect_bybit_transaction_pages(
            cash_page,
            start_ms=start_ms,
            end_ms=end,
            timeout=timeout,
        )
        return native, execution_evidence, cash_pages


class BinanceSpot(SpotExecution):
    """Qualified offline for the core spot route; no public-volume or market-buy claim."""

    market = SpotMarket(
        "binance",
        "Binance",
        "sandbox",
        "现货测试环境（Spot Testnet）",
        r"[0-9]{1,32}",
    )
    normal_mm_orders = supports_dispatch_fence = True
    requires_wallet_time = True
    private_stream = False  # REST gap repair; the private stream session is not qualified.
    paged_private_reads = frozenset({"get_transaction_history"})

    def configure(self, client, environment):
        if environment == self.market.environment:
            # Spot Testnet has /api, not the SAPI currency or derivatives endpoints.
            client.options.update(fetchCurrencies=False, fetchMarkets={"types": ["spot"]})

    def bind(self, client, environment):
        endpoints = client.urls.get("api", {})
        if environment != self.market.environment or client.isSandboxModeEnabled is not True:
            raise ccxt.NotSupported("Verified Spot Testnet binding required")
        for name in ("public", "private"):
            route = urlsplit(endpoints.get(name, ""))
            if (route.scheme, route.netloc, route.path) != (
                "https",
                "testnet.binance.vision",
                "/api/v3",
            ):
                raise ccxt.NotSupported("Verified Spot Testnet binding required")

    async def verify_account(self, client, uid, call):
        self.bind(client, self.market.environment)
        raw = await call("fetch_balance", private=True)
        info = raw.get("info")
        if (
            not isinstance(info, dict)
            or info.get("accountType") != "SPOT"
            or info.get("canTrade") is not True
            or str(info.get("uid")) != uid
            or not self.market.valid_uid(uid)
        ):
            raise ccxt.PermissionDenied("Spot account identity or trading permission unconfirmed")
        return {"uid": uid, "environment": self.market.environment, "market_type": "spot"}

    def wallet_fields(self, client, raw, *, environment, bound, identity, logger):
        info = raw.get("info") if isinstance(raw, dict) else None
        if not isinstance(info, dict) or info.get("accountType") != "SPOT":
            raise ccxt.ExchangeError("Spot wallet scope unconfirmed")
        if identity is not None and str(info.get("uid")) != identity.get("uid"):
            raise ccxt.PermissionDenied("Spot wallet identity changed")
        # Never substitute receipt time for the exchange's account watermark.
        at = info.get("updateTime")
        balances = info.get("balances")
        if not isinstance(balances, list):
            raise ccxt.ExchangeError("Spot wallet balance directory unconfirmed")
        free, used = {}, {}
        for row in balances:
            currency = client.safe_currency_code(row["asset"])
            if not currency or currency in free:
                raise ccxt.ExchangeError("Spot wallet asset identity conflict")
            free[currency], used[currency] = _decimal(row["free"]), _decimal(row["locked"])
        return dict(
            raw,
            free=free,
            used=used,
            total={c: free[c] + used[c] for c in free},
            timestamp=at if type(at) is int and at > 0 else None,
        )

    def trade_fields(self, trade):
        raw = trade.get("info") or {}
        # Exact native text avoids passing a binary-float approximation to the ledger.
        if all(isinstance(raw.get(k), str) for k in ("price", "qty", "quoteQty")):
            trade = dict(trade, price=raw["price"], amount=raw["qty"], cost=raw["quoteQty"])
        fee = trade.get("fee") or {}
        if isinstance(raw.get("commission"), str) and fee.get("currency"):
            trade = dict(trade, fees=[dict(fee, cost=raw["commission"])])
        return trade

    async def fetch_original(self, client, call, symbol, oid, cid, slots, timeout, *, qualified):
        if not oid and not cid:
            raise ValueError("Missing original identity")
        self.bind(client, self.market.environment)
        try:
            if oid:
                raw = await call("fetch_order", oid, symbol, private=True)
                if (
                    cid
                    and raw.get("id") == str(oid)
                    and raw.get("symbol") == symbol
                    and raw.get("status") == "canceled"
                    and raw.get("clientOrderId") == self.cancel_id(symbol, oid, cid)
                ):
                    raw = dict(raw, clientOrderId=cid)
                return raw, False
            return await call(
                "fetch_order", oid, symbol, private=True, params={"origClientOrderId": cid}
            ), True
        except ccxt.OrderNotFound as exc:
            # This is an exact GET's answer, never evidence that create was rejected.
            try:
                raw, _ = json.JSONDecoder().raw_decode(str(exc)[str(exc).index("{") :])
            except ValueError:
                raw = {}
            outcome = (
                OriginalLookup.ABSENT
                if isinstance(raw, dict) and raw.get("code") == -2013
                else OriginalLookup.UNCONFIRMED
            )
            raise ClientOrderLookupUnconfirmed(
                "Exact original lookup has no order", outcome=outcome
            ) from None

    @staticmethod
    def cancel_id(symbol, oid, cid):
        # The venue replaces clientOrderId on cancellation. A reproducible
        # cancel identity preserves the original association after a lost ACK
        # or process restart, without trusting arbitrary replacements.
        digest = hashlib.sha256(f"{symbol}\0{oid}\0{cid}".encode()).hexdigest()
        return "cmc-" + digest[:32]

    async def cancel_original(self, client, call, symbol, oid):
        self.bind(client, self.market.environment)
        original = await call("fetch_order", oid, symbol, private=True)
        cid = original.get("clientOrderId")
        if (
            original.get("id") != str(oid)
            or original.get("symbol") != symbol
            or not isinstance(cid, str)
            or not cid
        ):
            raise ccxt.ExchangeError("Cancel original identity unconfirmed")
        if original.get("status") in ("closed", "canceled"):
            return original  # A signed terminal read needs no further write.
        if original.get("status") != "open":
            raise ccxt.ExchangeError("Cancel original status unconfirmed")
        cancel_id = self.cancel_id(symbol, oid, cid)
        raw = await call(
            "cancel_order",
            oid,
            symbol,
            private=True,
            params={
                "orderId": oid,
                "origClientOrderId": cid,
                "newClientOrderId": cancel_id,
            },
        )
        info = raw.get("info") or {}
        if (
            raw.get("id") != str(oid)
            or raw.get("symbol") != symbol
            or raw.get("clientOrderId") not in (cid, cancel_id)
            or info.get("origClientOrderId") != cid
            or info.get("clientOrderId") != cancel_id
        ):
            raise ccxt.ExchangeError("Cancel acknowledgement identity unconfirmed")
        return dict(raw, clientOrderId=cid)

    async def open_directory(self, client, call, symbol):
        self.bind(client, self.market.environment)
        # Unified spot openOrders has no transport limit; don't pass a client-side cap.
        return await call("fetch_open_orders", symbol, private=True)

    async def private_window(self, client, call, symbol, start, end, limit, native=None):
        self.bind(client, self.market.environment)
        cap = min(1000, limit)
        if start is None:
            rows = await call("fetch_my_trades", symbol, limit=cap, private=True)
            return rows, False, end
        # Time-bounded queries cannot be combined with fromId. Split saturated
        # windows without skipping fills sharing the same millisecond.
        rows, seen = [], set()
        end = end if end is not None else client.milliseconds() + 1

        async def page(lo, hi):
            # Pass native bounds through the unified call without asking CCXT
            # to filter or cap the returned rows before we check completeness.
            batch = await call(
                "fetch_my_trades",
                symbol,
                private=True,
                params={
                    "startTime": lo,
                    "endTime": hi - 1,
                    "limit": cap,
                },
            )
            if not isinstance(batch, list) or len(batch) > cap:
                raise ccxt.ExchangeError("Private execution page shape unconfirmed")
            if len(batch) == cap:
                mid = (lo + hi) // 2
                if mid == lo:
                    raise ccxt.ExchangeError(
                        "Private execution window saturated at one millisecond"
                    )
                await page(lo, mid)
                await page(mid, hi)
                return
            for row in batch:
                if not isinstance(row, dict):
                    raise ccxt.ExchangeError("Private execution row shape unconfirmed")
                key = (row.get("order"), row.get("id"))
                at = row.get("timestamp")
                if (
                    not all(isinstance(v, str) and v for v in key)
                    or key in seen
                    or row.get("symbol") != symbol
                    or type(at) is not int
                    or not lo <= at < hi
                    or len(rows) >= 8192
                ):
                    raise ccxt.ExchangeError("Private execution identity or range unconfirmed")
                seen.add(key)
                rows.append(row)

        day = 86_400_000
        if not 0 < start < end or end - start > 7 * day:
            raise ccxt.ExchangeError("Private execution range outside supported audit bounds")
        async with asyncio.timeout(client.timeout / 1000):
            for lo in range(start, end, day):
                await page(lo, min(end, lo + day))
        return rows, True, end

    async def account_fee(self, client, call, market, slots):
        # CCXT's spot fetchTradingFee uses SAPI, absent on Spot Testnet.
        self.bind(client, self.market.environment)
        async with asyncio.timeout(client.timeout / 1000), slots:
            client.check_required_credentials()
            raw = await client.private_get_account_commission({"symbol": market["id"]})
        if raw.get("symbol") != market["id"]:
            raise ccxt.ExchangeError("Spot fee scope unconfirmed")

        def rate(kind):
            result = Decimal(0)
            for name in ("standardCommission", "specialCommission", "taxCommission"):
                group = raw[name]
                amounts = [Decimal(group[k]) for k in (kind, "buyer", "seller")]
                if not all(v.is_finite() and v >= 0 for v in amounts):
                    raise ValueError("Spot fee evidence invalid")
                result += amounts[0] + max(amounts[1:])
            return result  # Do not assume any discount or fee asset.

        return {
            "symbol": market["symbol"],
            "percentage": True,
            "tierBased": False,
            "maker": rate("maker"),
            "taker": rate("taker"),
        }


_VENUES = {
    (venue.market.exchange, venue.market.environment): venue
    for venue in (BybitSpot(), BinanceSpot())
}
_DEFAULT = SpotExecution()
_LEGACY = next(v for v in _VENUES.values() if v.market.legacy_default)


def spot_execution(exchange, environment):
    if (exchange, environment) in _VENUES:
        return _VENUES[exchange, environment]
    legacy = _LEGACY if exchange == _LEGACY.market.exchange else _DEFAULT
    try:
        cls = exchange_class(exchange)
    except ccxt.BaseError:
        return legacy
    return getattr(cls, "cm_spot_execution", {}).get(environment, legacy)


def managed_spot_market(exchange, environment, market_type="spot"):
    if environment not in ("sandbox", "demo"):
        return None  # Keep the existing managed experiment environment boundary.
    profile = spot_execution(exchange, environment)
    market = profile.market
    return (
        market
        if market
        and (market.exchange, market.environment, market.market_type)
        == (
            exchange,
            environment,
            market_type,
        )
        else None
    )


def managed_spot_markets():
    markets = {v.market for v in _VENUES.values()}
    for name in list_exchanges():
        for environment, profile in getattr(exchange_class(name), "cm_spot_execution", {}).items():
            if profile.market and managed_spot_market(name, environment) == profile.market:
                markets.add(profile.market)
    return sorted(markets, key=lambda row: (not row.legacy_default, row.exchange, row.environment))


def legacy_spot_market():
    return _LEGACY.market


def require_managed_spot(client):
    require_capabilities(
        client,
        "createOrder",
        "cancelOrder",
        "fetchOrder",
        "fetchOpenOrders",
        "fetchBalance",
        "fetchMyTrades",
        "fetchOrderBook",
    )
