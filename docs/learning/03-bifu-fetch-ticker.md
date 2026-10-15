# 第 3 课：读取 Bifu 24 小时行情和最优盘口

这一课只读取 Bifu 测试环境的公开行情，不使用 API Key，不访问账户，也不会下单。

协议依据：[Bifu 公共行情文档](https://flame-api.bifu.dev/docs/market)中的
`GET /market/v1/ticker`、`GET /market/v1/bookTicker` 和 `GET /market/v1/trends`；本轮核对时间为 2026-09-20
（Asia/Shanghai）。

## 1. Ticker 是什么

`fetch_ticker("BTC/USDT")` 返回一个交易对的最新成交价和 24 小时滚动统计。它适合回答：

- 最新一笔成交价是多少；
- 24 小时开盘、最高和最低价格是多少；
- 24 小时成交了多少 BTC、成交额是多少 USDT；
- 相比 24 小时开盘价，价格变化了多少。

Ticker 不是订单薄，也不是创建订单。它不会读取你的余额，更不会产生交易。

## 2. Bifu 和 CCXT 的调用过程

调用者只写：

```python
ticker = await exchange.fetch_ticker("BTC/USDT")
```

适配器在内部完成四步：

1. 用 `load_markets()` 确认 `BTC/USDT` 对应 Bifu 标的 ID `90000001`；
2. 请求测试环境 `GET /market/v1/ticker?instrument_id=90000001`；
3. 检查响应的标的 ID 和时间戳，避免把其他交易对或损坏数据当成 BTC/USDT；
4. 把 Bifu 字段转换成 CCXT 标准 Ticker。

调用方不应该把 `BTC/USDT` 手工替换成 `BTC-USDT`，也不应该在业务代码里写死
`90000001`。这些转换由市场资料和适配器负责。

## 3. 字段怎样对应

| Bifu 字段 | CCXT 字段 | 含义 |
|---|---|---|
| `last_price` | `last` | 最新成交价 |
| `open_price` | `open` | 24 小时滚动窗口开盘价 |
| `high_price` | `high` | 24 小时最高价 |
| `low_price` | `low` | 24 小时最低价 |
| `volume` | `baseVolume` | 基础币成交量；BTC/USDT 中是 BTC 数量 |
| `quote_volume` | `quoteVolume` | 计价币成交额；BTC/USDT 中是 USDT 金额 |
| `price_change` | `change` | 最新价减开盘价 |
| `price_change_percent` | `percentage` | 百分比变化；`0.18` 表示 `0.18%`，不是 `18%` |
| `ts` | `timestamp` / `datetime` | 交易所提供的行情时间，Unix 毫秒及其 UTC 文本 |

原始响应完整保存在 `ticker["info"]`，方便排查问题；业务代码优先读取 CCXT 标准字段。

如果交易对存在但 24 小时内没有任何成交，Bifu 会返回 HTTP 404 和业务码 `6005`。适配器把它
转换成 `ccxt.BadResponse`，表示当前没有可组成 Ticker 的成交统计。它不会误报成
`ExchangeNotAvailable`，因为这种情况不代表整个交易所不可用。

## 4. 为什么成交统计和买一卖一要分开读取

Bifu 的 `/market/v1/ticker` 只提供成交统计，不提供买一和卖一。买一、卖一来自独立的
`/market/v1/bookTicker`，完整盘口来自 `/market/v1/depth`。

所以 `fetch_ticker()` 自己的 `bid` 和 `ask` 保持 `None`。不能用最新成交价 `last` 假装买一或
卖一，因为最新成交可能发生在几秒或更早以前，而当前盘口已经变化。

只需要买一、卖一和对应数量时调用：

```python
best = (await exchange.fetch_bids_asks(["BTC/USDT"]))["BTC/USDT"]
```

这个 CCXT 方法返回 `bid`、`bidVolume`、`ask`、`askVolume` 和盘口时间。Bifu 原生接口一次只能查
一个交易对，因此多交易对调用会逐个请求，能力声明为 `"emulated"`；这仍是正常统一接口，不是
Bifu 特殊 `mock`。需要多档完整盘口时继续使用 `fetch_order_book()`。

## 5. 24 小时缩略走势为什么使用原生隐式接口

Bifu 还提供 `/market/v1/trends`，一次返回多个交易对最多 24 个、固定一小时粒度的收盘价，主要给
行情列表画缩略走势图。CCXT 没有“批量缩略走势”这一统一方法，而且 Bifu 响应只有收盘价，没有
开、高、低和成交量，不能伪装成标准 OHLCV。

因此适配器只注册 CCXT 原生隐式接口：

```python
raw = await exchange.public_get_market_v1_trends({"instrument_ids": exchange.market_id("BTC/USDT")})
```

它返回 Bifu 原始结构；需要标准 K 线时仍调用 `fetch_ohlcv()`。这里没有新增 `fetch_trends` 之类的
专有高层方法，也不是特殊 `mock`；只是让文档中已有的公共 REST 地址能通过同一个 CCXT 实例调用。

## 6. Jacky 手工验收

在 `CCXT_CM-Jacky` 仓库根目录运行：

```bash
uv run python -m examples.inspect_bifu_ticker BTC/USDT
```

这条命令只读取测试环境公开接口。你要检查：

- `environment` 是 `test`；
- `symbol` 是 CCXT 格式 `BTC/USDT`；
- `instrument_id` 是 Bifu 原始标的 ID；
- `timestamp` 是毫秒时间戳，`datetime` 是同一时刻的 UTC 表示；
- `last/open/high/low` 是价格；
- `base_volume` 是 BTC 成交量，`quote_volume` 是 USDT 成交额；
- `bid` / `bid_volume` 是真实买一价和数量；`ask` / `ask_volume` 是真实卖一价和数量；
- `book_timestamp` 是最优盘口的时间，它可能与 24 小时 Ticker 时间不同；
- `trend_point_count` 最多是 24；`trend_first_close` / `trend_last_close` 是这段缩略走势的首尾收盘价。

测试环境偶尔响应较慢。验收工具最长等待 30 秒，第一次超时时会提示并只重试一次。重试仅存在于
这个只读工具；适配器核心不会自动重试，写接口更不会因此重复提交。

## 7. 常见问题和答案

1. `fetch_ticker` 返回什么？答：返回最新成交价和 24 小时成交统计，不是订单簿，也不会创建订单。
2. 为什么代码用 `BTC/USDT`，请求用 `90000001`？答：前者是 CCXT 统一交易对，后者是 Bifu
   原始标的 ID，适配器通过 markets 自动转换。
3. BTC/USDT 的 `baseVolume` 和 `quoteVolume` 分别是什么？答：前者是 BTC 成交数量，后者是
   USDT 成交金额。
4. 为什么不能用 `last` 代替 `bid` 和 `ask`？答：`last` 是已经发生的成交，`bid/ask` 是当前
   等待成交的最优买卖价，时间和含义不同。
5. `percentage=0.18` 表示多少？答：表示上涨 `0.18%`，不是 `18%`。
6. 为什么 trends 不映射成 `fetch_ohlcv`？答：它只有固定一小时的收盘价点，缺少标准 OHLCV
   必须的开、高、低和成交量；强行映射会制造假数据。

你亲自运行命令，并能结合这些答案解释输出后，这组公开行情能力才能标记为“Jacky 已验收”。
