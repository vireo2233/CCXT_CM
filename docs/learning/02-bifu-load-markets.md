# 第 2 课：用 `load_markets` 认识 Bifu 的交易对

这一课只做公开只读操作：从 Bifu 测试环境读取现货交易对，再转换成 CCXT 的统一格式。不读取
账户、不使用 Key，也不会下单。

## 1. `load_markets` 的核心作用

Bifu 原始接口返回自己的字段，例如：

- 原始标的编号：`90000001`
- 原始展示符号：`BTC-USDT`
- 基础币资产编号：`1`
- 计价币资产编号：`2`
- 价格步长：`0.01`
- 数量步长：`0.00001`
- 最小名义金额：`5`

适配器把它转换成所有 CCXT 调用都认识的格式：

- `market.id`：`90000001`，后续 Bifu 接口用它准确定位标的
- 统一交易对 `market.symbol`：`BTC/USDT`
- `base`：`BTC`
- `quote`：`USDT`
- `precision.price`：`0.01`
- `precision.amount`：`0.00001`
- `limits.cost.min`：`5 USDT`

之后的行情、余额和订单功能都依赖这份市场字典。没有先加载市场，程序就不知道 `BTC/USDT`
对应 Bifu 的哪个标的，也不知道价格和数量应该保留到什么步长。

## 2. `load_markets` 和 `fetch_markets` 的区别

- `fetch_markets()`：直接向交易所请求原始市场资料并返回标准化列表。
- `load_markets()`：调用 `fetch_markets()`，再把结果缓存为以 symbol 为键的字典。

实际业务通常使用 `load_markets()`。后续再次调用时会复用缓存；只有明确要求刷新时才使用
`load_markets(True)`。

## 3. 精度和最小限额不是一回事

`precision` 表示“每一步可以变化多少”：

- 数量步长 `0.00001`：数量要按这个步长处理。
- 价格步长 `0.01`：价格要按这个步长处理。

`limits` 表示“允许的范围”：

- `amount.min=0.00001`：单次数量不能低于这个值。
- `cost.min=5`：价格乘数量后的名义金额不能低于 `5 USDT`。

数量符合步长，不代表订单金额一定达标。真正下单前必须同时检查精度和限额。

## 4. Jacky 手工验收

在仓库根目录运行：

```bash
uv run python -m examples.inspect_bifu_markets BTC/USDT
```

这条命令只调用测试环境的公开市场接口。你应该重点查看：

- `environment` 是 `test`；
- `market_count` 大于 `0`；
- `id` 是 Bifu 原始标的编号 `90000001`；
- `exchange_symbol` 是 Bifu 展示格式 `BTC-USDT`；
- `symbol` 是 CCXT 格式 `BTC/USDT`；
- `amount_1.23456789` 按数量步长处理成 `1.23456`；
- `price_100.129` 按价格步长处理成 `100.13`。

Bifu 测试环境偶尔会暂时响应较慢，所以这条**只读验收命令**最长会等待 30 秒；第一次超时时会
在终端提示并自动重试一次。重试只放在这个人工验收工具里，Bifu 适配器本身不会偷偷重试，避免
以后下单时因自动重试产生重复订单。如果两次都超时，说明当时测试环境或网络链路不可用，稍后
重新运行即可，不能把超时当成功。

## 5. 你要能回答的四个问题

1. `90000001`、`BTC-USDT` 和 `BTC/USDT` 分别是什么，为什么不能混用？
2. `load_markets()` 和 `fetch_markets()` 的区别是什么？
3. `precision.amount` 与 `limits.amount.min` 分别代表什么？
4. 为什么数量精度正确，订单仍可能因为 `limits.cost.min` 被拒绝？

你亲自运行命令并能用自己的话回答后，阶段 2 才能标记为“Jacky 已验收”。
