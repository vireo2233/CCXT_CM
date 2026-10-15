# 第 1 课：用同一个 Bifu 适配器选择测试或生产环境

这一课只解决一个问题：**同一个 Bifu 适配器怎样连接到调用方指定的环境？**

测试和生产不是两套代码。它们使用同一个 `create_exchange("bifu", ...)` 入口；区别只是 URL、
Key 和调用参数。实际运行时仍应为不同环境创建不同实例，避免共享凭证和连接状态。

## 1. 统一创建入口

```python
from ccxt_cm import create_exchange

exchange = create_exchange("bifu", {"enableRateLimit": True}, mode="async")
```

Bifu 已作为本库内置扩展注册，不需要调用方再导入 `BIFU_EXTENSION` 或手工创建 `Registry`。
返回的是 `BifuREST` 这个 CCXT 异步交易所对象，不是 CoinMaker 的第二套包装器。

## 2. 测试环境

```python
exchange = create_exchange(
    "bifu",
    {"apiKey": "测试 Key", "secret": "测试 Secret"},
    mode="async",
)
exchange.set_sandbox_mode(True)
```

`set_sandbox_mode(True)` 必须在任何网络调用之前执行。当前测试地址为：

- REST：`https://flame-api.bifu.dev`
- WebSocket：`wss://flame-api.bifu.dev`

测试账号使用测试资金。Key、Secret、签名、订单号和精确余额不能写入仓库或验收记录。

## 3. 生产环境

Bifu 公开文档目前没有给出生产 REST/WS 地址，所以适配器默认不会猜地址；新实例的生产 URL 为
`None`。平台提供生产地址和生产 Key 后，由调用方显式传入：

```python
exchange = create_exchange(
    "bifu",
    {
        "apiKey": "生产 Key",
        "secret": "生产 Secret",
        "urls": {
            "api": {
                "public": "平台确认的生产 REST 地址",
                "private": "平台确认的生产 REST 地址",
                "ws": "平台确认的生产 WebSocket 地址",
            }
        },
    },
    mode="async",
)
```

生产实例不要调用 `set_sandbox_mode(True)`。适配器允许显式生产配置，但在平台提供正式地址前，
项目不会猜 URL，也不会把测试 Key 当作生产 Key。生产首次验收只做只读调用。

## 4. 已完成的 REST 能力

最终交付已完成并测试以下统一方法：

- 公共：`load_markets()` / `fetch_markets()`、`fetch_ticker()` / `fetch_tickers()`、
  `fetch_bids_asks()`、`fetch_order_book()`、`fetch_ohlcv()` 和 `fetch_trades()`。
- 私有只读：`fetch_balance()`、`fetch_ledger()`、`fetch_open_orders()`、
  `fetch_closed_orders()`、`fetch_order()` 和 `fetch_my_trades()`。
- 私有写入：`create_order()` / `create_orders()`、`cancel_order()` / `cancel_orders()` /
  `cancel_all_orders()`，以及 `edit_order()` / `edit_orders()`。
- Bifu 专有私有方法：`create_mock_order()`；它不冒充 CCXT 标准 `create_order()`。

私有请求已实现 `X-API-KEY`、毫秒 `X-TS` 和 HMAC-SHA256 小写 hex `X-SIGN`，完整异常映射、
自动化测试和测试环境在线验收也已完成。尚未实现的方法不会提前声明为 `has=True`。生产环境与
测试环境共用同一适配器，但本轮没有生产 URL、生产 Key 或生产在线验收；上线前先做经授权的
生产只读验收，不直接执行生产写操作。

## 5. 手动检查环境

在仓库根目录运行：

```bash
uv run python -m examples.inspect_bifu_environment test
uv run python -m examples.inspect_bifu_environment production
```

测试环境应显示 `sandbox=true`、`configured=true`；默认生产实例应显示 `sandbox=false`、
`configured=false`。后者只表示调用方还没有传入生产 URL，不代表适配器另有一套生产实现。

## 6. 你要能回答的三个问题

1. 为什么测试和生产共用同一适配器，但运行时仍应使用不同实例？
2. `set_sandbox_mode(True)` 应该在什么时候调用？
3. 为什么默认生产实例是 `configured=false`，而显式传入平台确认的生产 URL 后可以连接？
