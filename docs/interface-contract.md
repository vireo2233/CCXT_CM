# 接口与数据契约

## REST 基线

自定义方法参数顺序必须与当前受支持的 CCXT 版本一致。Python 使用 snake_case；官方初始化产生的 camelCase 别名按上游规则保留。

| CCXT 方法 | 典型参数 | 标准返回 |
|---|---|---|
| `load_markets` / `fetch_markets` | `reload, params` / `params` | symbol → Market / Market 列表 |
| `fetch_ticker` | `symbol, params` | Ticker |
| `fetch_order_book` | `symbol, limit, params` | OrderBook |
| `fetch_trades` / `fetch_ohlcv` | `symbol, since, limit, params` / 加 `timeframe` | Trade 列表 / OHLCV 数组 |
| `create_order` | **`symbol, type, side, amount, price, params`** | Order |
| `cancel_order` / `fetch_order` | `id, symbol, params` | Order |
| `cancel_all_orders` | `symbol, params` | Order 列表；只有汇总 ACK 时不得伪造逐单终态 |
| `fetch_open_orders` / `fetch_orders` / `fetch_closed_orders` | `symbol, since, limit, params` | Order 列表 |
| `fetch_balance` | `params` | Balance |
| `create_orders` / `cancel_orders` | `orders, params` / `ids, symbol, params` | 按官方契约的结果列表 |
| `fetch_positions` / `set_leverage` | 按官方当前签名 | Position 列表 / 交易所响应 |

表内是接入时的检查清单，不表示每家交易所都必须实现。`fetch_ohlcv` 的完整顺序是 `symbol, timeframe='1m', since=None, limit=None, params={}`。实际签名用当前官方类核对，不以此缩写表生成调用代码。

## WebSocket 基线

| 方法 | 返回/说明 |
|---|---|
| `watch_ticker` / `watch_tickers` | Ticker / 按 symbol 索引的 Ticker |
| `watch_order_book` | 本地维护后的当前 OrderBook，不是原始增量 |
| `watch_trades` | Trade 缓存或新更新，取决于 `newUpdates` |
| `watch_orders` | 订单更新缓存，不是“全部未成交订单”的账户快照 |
| `watch_balance` | 余额更新，快照/增量按协议合并 |
| `watch_my_trades` / `watch_positions` | 有真实私有流时再实现 |
| `create_order_ws` / `cancel_order_ws` | WS 交易指令；与通过 WS 收到订单更新是不同能力 |
| `un_watch_*` | 仅在适配器真正支持且有测试时声明 |

Pro 类可用不代表上表全可用。不得用 REST 轮询伪装 `watch_*`；调用方可以自己明确选用 REST 降级，但应标记来源和时效。

## 能力四态

| `has` 值 | 含义 | `require_capabilities` 默认 |
|---|---|---|
| `True` | 声明有实现 | 放行 |
| `False` | 不支持/尚未实现 | 抛 `NotSupported` |
| `'emulated'` | 由其他接口组合模拟；要说明额外请求和语义限制 | 默认拒绝，显式 `allow_emulated=True` 放行 |
| `None` 或键缺失 | 未知/未声明 | 抛 `NotSupported` |

这只是能力声明检查，不进行账户鉴权、交易所探活或实盘可用性认证。不要使用 `bool(exchange.has.get(name))`，字符串 `'emulated'` 会被误认为原生支持。
`has` 是方法级别；更细的市场类型、订单类型、`postOnly`、`reduceOnly`、`timeInForce`、触发单、分页和客户端订单号约束，应使用官方 `features`（如有）以及交易所文档。未知参数不得被自定义代码悄悄丢弃或转换成别的订单。

## 返回值规则

1. symbol 使用 `BTC/USDT`、`BTC/USDT:USDT` 等 CCXT 标准。原始交易对 ID 从 `market['id']` 获取，禁止靠字符串替换猜测合约。
2. 时间戳是 Unix 毫秒；`datetime` 从该时间戳生成。交易所没给成交时间，就不填“当前时间”。
3. Ticker/Order/Trade/Balance 等支持 `info` 的结构保留原始业务数据。标准 OrderBook/OHLCV 按上游结构，不额外硬塞统一外壳。
4. 未知价格、成交量、余额、状态使用 `None`。只有确定的零才填 0。已知 `free + used` 可以推导 `total`，未知两项不得硬补。
5. 订单状态使用 CCXT 标准 `open/closed/canceled/expired/rejected`；未知状态不默认为 open 或 closed。
6. 成交量、成交均价、费用和 lastTradeTimestamp 必须来自可确认的数据或上游明确推导规则。请求接受/成功 HTTP 状态不证明成交。
7. 合约 amount 通常是张数，结合 `contractSize`；现货 amount 通常是 base 数量。币本位、U 本位、交割/永续、线性/反向不得混淆。
8. 使用 `precisionMode`、`amount_to_precision`、`price_to_precision` 和 limits；精度不等于最小金额。禁止默认 `contractSize=1` 掩盖未知元数据。
9. 过滤 `symbol/since/limit` 与排序遵循官方约定。分页若只返回一页，应注明，不得把截断列表宣传成全部订单。
10. 批量接口必须保留逐项成功、拒绝和未知结果，说明是否原生/模拟、是否原子；不得整批兜底“成功”。

## 异常

使用官方 `AuthenticationError`、`PermissionDenied`、`BadSymbol`、`InvalidOrder`、`InsufficientFunds`、`OrderNotFound`、`RateLimitExceeded`、`RequestTimeout`、`NetworkError`、`BadResponse`、`NotSupported` 等。
HTTP 层和业务层都要判断错误。不支持抛 `NotSupported`；禁止 `pass`、返回 `None`、空字典或空列表来表示失败。查询结果本来为空时才返回空列表。

未知写结果向调用方透传异常，不擅自再次下单，不修改客户端订单号，不伪造 `canceled`。重试及账户对账由业务层根据原订单号决定。
