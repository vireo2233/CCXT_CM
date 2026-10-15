# CCXT 统一 API 与 Bifu 粗粒度重排研究

> 研究日期：2026-09-18（Asia/Shanghai）
>
> 范围：只核对 CCXT 官方 Manual、CCXT Pro Manual、API Spec 与官方 GitHub；不代表 Bifu
> 已实现或已在线验收任何能力。

## 结论先行

当前项目的目标可以收敛为一句话：**调用方只通过 `create_exchange()` 得到官方 CCXT 或 Bifu
交易所实例，随后直接调用 CCXT 统一方法；Bifu 适配器负责把原始请求和响应转换成同一套方法、
数据结构与异常。**

原计划按单个方法拆阶段过细。经 2026-09-18 再次确认，项目改为 **3 个交付部分**。每个部分内部
仍逐接口运行真实调用、检查能力声明、验证 CCXT 标准结构，但不再把单个方法拆成项目阶段。

测试环境和生产环境不需要两套 Bifu 适配器，也不应成为两个独立开发阶段；它们是同一个适配器的
两套运行配置。不过，官方 CCXT 明确要求：交易所支持 sandbox 时，`set_sandbox_mode(True)` 必须在
实例创建后、任何其他调用之前执行，而且测试 Key 与生产 Key 不能互换。因此可以取消“环境隔离”
作为独立功能阶段，但不能取消运行时的 URL、Key、资金和环境标识校验。

## 官方资料核实

### 1. “统一 API”是共同调用方式，不是每家交易所全量支持的承诺

CCXT Manual 把统一 API 定义为各交易所常见能力的一个子集。官方概览列出的核心方法包括：

- 市场与公共数据：`loadMarkets`、`fetchMarkets`、`fetchCurrencies`、`fetchTicker(s)`、
  `fetchOrderBook`、`fetchOHLCV`、`fetchStatus`、`fetchTrades`。
- 账户与交易：`fetchBalance`、`createOrder`、`cancelOrder`、`fetchOrder(s)`、
  `fetchOpenOrders`、`fetchClosedOrders`、`fetchMyTrades`、`deposit`、`withdraw`。

这张图表达的是统一命名空间，不是“所有交易所必有这些方法”。官方文档同时说明，不同交易所的
方法集合不同；调用前要查 `exchange.has`，不支持的方法会抛 `NotSupported`。

`has` 不是简单布尔值，而是四态：

| 值 | 官方含义 | 项目验收含义 |
|---|---|---|
| `True` | 交易所有原生端点，CCXT 已统一实现 | 才能进入真实调用验收 |
| `False` | 交易所原生 API 没有该端点 | 明确记录不支持 |
| `'emulated'` | CCXT 用其他接口在客户端重建 | 单独记录语义和额外请求，不能当原生实现 |
| `None` / 缺失 | 尚未实现或尚未统一 | 不能放行 |

所以 `require_capabilities()` 适合做**调用前声明检查**，但它不证明网络、Key、账户权限、交易对或
具体订单类型一定可用。真正的验收仍要调用接口。

来源：

- [CCXT Manual：架构概览、Exchange Metadata、Unified API](https://docs.ccxt.com/docs/manual)
- [官方 Exchange Capabilities 示例](https://docs.ccxt.com/docs/examples/js/exchange-capabilities)
- [官方 API Spec by Method](https://docs.ccxt.com/docs/base-spec)

### 2. 统一返回格式必须按“结构”验收，不能只看 HTTP 200

Manual 明确区分两类结果：交易所原始/implicit API 返回未经统一的 JSON；统一方法返回跨交易所
一致的 CCXT 结构。Bifu 每个方法的验收应至少包含：

1. 方法可以被 `create_exchange("bifu", ...)` 返回的实例直接调用；
2. 请求参数采用 CCXT 标准格式，例如 `BTC/USDT`，不把 `BTC-USDT` 泄漏给调用方；
3. 返回对象符合相应标准结构；
4. 原始交易所响应保留在 `info`（该结构支持时）；
5. 缺失字段用 `None`，不得用猜测值填满；
6. 失败映射成 CCXT 异常，而不是空字典、空列表或伪成功。

重点结构示例：

- Market：`id`、`symbol`、`base`、`quote`、`type`、`spot`、`precision`、`limits` 等。
- Ticker：`symbol`、`timestamp`、`high/low`、`bid/ask`、`last`、成交量等；官方说明部分字段可为
  `None`。
- OrderBook：`bids`、`asks`、`symbol`、`timestamp`、`datetime`、`nonce`。
- Balance：按 `free`、`used`、`total` 聚合，并按币种提供对应余额。
- Order：`id`、`status`、`symbol`、`type`、`side`、`amount`、`filled`、`remaining`、`cost`、
  `fee`、`info` 等。`createOrder` 至少保证返回 `id` 与 `info`，其他字段取决于交易所响应。

来源：

- [CCXT Manual：Returned JSON Objects 与各统一结构](https://docs.ccxt.com/docs/manual)
- [CCXT API Spec：各方法返回结构与支持交易所](https://docs.ccxt.com/docs/base-spec)

### 3. 测试/生产共用同一适配器，但运行配置仍有明确边界

官方把 sandbox 描述为通常与生产 API 相同、主要切换服务器 URL 的测试环境。对于官方支持
sandbox 的交易所，标准操作是：

```python
exchange = ccxt.binance(config)
exchange.set_sandbox_mode(True)  # 必须是创建后的第一个调用
```

三个不能省略的事实：

- 只有底层交易所和 CCXT 类支持 sandbox 时才能这样切换；并非所有交易所都有测试网。
- `set_sandbox_mode(True)` 必须发生在任何 `load_markets`、行情或私有调用之前。
- sandbox Key 与 production Key 不能互换。

因此对 CCXT_CM 的合理表述是：**不开发两套适配器，不把环境单列为开发阶段；创建实例时选择测试
或生产配置，并把“实际命中的 URL、环境标志、凭据来源是否匹配”作为每轮验收的固定前置检查。**
对 Bifu，如果它不是 CCXT 官方交易所，则由 Bifu 适配器复用同样的 CCXT 语义来切换自身测试/
生产 URL；不能假设官方 `set_sandbox_mode` 会自动知道 Bifu 的地址。

来源：

- [CCXT Manual：Testnets And Sandbox Environments](https://docs.ccxt.com/docs/manual#testnets-and-sandbox-environments)

### 4. sync、async 与 pro 是执行模式，不是三套业务接口

Python 官方用法分为：

- `ccxt`：同步 REST；
- `ccxt.async_support`：`asyncio` 异步 REST，属性和方法与同步版相同，网络方法需要 `await`；
- `ccxt.pro`：异步 WebSocket/流式能力，并继承 CCXT REST 规则和结构。

CCXT Pro 官方说明，`fetch*` 是 REST 请求-响应，`watch*` 是 WebSocket 流；常见对应关系包括
`fetchOrderBook` → `watchOrderBook`、`fetchTicker` → `watchTicker`、`fetchBalance` →
`watchBalance`、`fetchOrders` → `watchOrders`。并非每家交易所都有每个 `watch*` 能力，仍需查
`has`。Pro 的连接、订阅和缓存语义也意味着，返回一次结果不等于已经验证重连、增量合并和私有
认证。

来源：

- [CCXT Manual：Synchronous vs Asynchronous Calls](https://docs.ccxt.com/docs/manual#synchronous-vs-asynchronous-calls)
- [CCXT Pro Manual](https://docs.ccxt.com/docs/pro-manual)

### 5. `deposit` / `withdraw` 不应因概览图出现就自动成为 Bifu 必做项

当前 API Spec 显示，统一 `deposit` 方法的支持范围非常窄，文档中的当前支持交易所仅列 Coinbase；
`withdraw` 的支持范围更广，但它是直接资金转出操作，返回 Transaction 结构并可能要求 2FA。
另外，查询充值/提现历史使用的是 `fetchDeposits`、`fetchWithdrawals` 等方法，与执行入金/出金不是
一回事。

因此 Bifu 是否实现 `deposit` / `withdraw`，应以 Bifu 原生协议和 CoinMaker 实际需求为准，不能
仅凭统一 API 概览图推定为必做。若业务确实需要，资金划转/提现应放在最高风险的可选验收项，且
生产环境不能用“试一笔”方式验收。

来源：

- [CCXT API Spec：deposit](https://docs.ccxt.com/docs/base-spec#deposit)
- [CCXT API Spec：withdraw](https://docs.ccxt.com/docs/base-spec#withdraw)
- [CCXT Manual：Deposit / Withdrawal](https://docs.ccxt.com/docs/manual#deposit)

## 最新执行计划：3 个部分

### 第一部分：所有公共及私有 REST 接口（支持异步）

一次完成 Bifu 的公共 REST、私有 REST、签名、解析、标准异常和能力声明。调用方通过
`create_exchange("bifu", ..., mode="async")` 或 Pro 实例继承的 REST 能力，直接使用 CCXT 统一
方法，不增加第二套业务接口。

Bifu 的特殊刷量接口属于本部分，但作为交易所私有专有方法单独实现；它不伪装成 `createOrder`
或其他 CCXT 统一方法。是否实现某个统一方法以 Bifu 协议和业务需求为准，`has` 必须如实声明。

### 第二部分：公共及私有 WebSocket

一次完成 Bifu 实际支持的公共和私有 WebSocket。公共流包括行情、盘口、成交等；私有流包括余额、
订单和个人成交等。调用方继续使用 CCXT Pro 的 `watch*` 方法。未获得原生推送协议证据的能力不
实现、不用 REST 轮询伪装。

### 第三部分：完整测试、修复、回归、复审和交付

封装完成后逐项测试第一、第二部分的全部功能。每项都要确认：能否真实取得数据、返回值是否符合
CCXT 标准结构、异常是否正确映射。发现问题后修复并重新执行全量回归，随后完成代码复审、重复和
无调用点代码清理、正式文档、构建产物及交付报告。

测试环境和生产环境不拆成两个开发阶段，共用同一适配器，通过不同 URL、Key 和参数选择环境。
每个部分完成后形成一份可直接向领导汇报的阶段报告，再进入下一部分。

## 每个部分内部的固定验收模板

部分变粗不等于少测接口。每个方法仍按同一套四步走，只是不再为它单独建立“阶段”：

1. **能力**：`has` 是 `True`、`False`、`'emulated'` 还是未声明。
2. **调用**：通过 `create_exchange("bifu", ...)` 的统一实例实际调用，而不是直接调内部私有函数。
3. **格式**：验证必需字段、类型、单位、symbol、毫秒时间戳、状态和 `info`。
4. **边界**：验证空数据、不支持、鉴权失败、业务错误、超时；写接口不做隐式重试。

一个部分内可用同一验收程序按清单逐项执行，最后输出一张汇总表：`method / has / 调用结果 /
结构校验 / 异常校验 / 人工解释`。这样既满足“挨个接口测”，又避免把项目管理拆成十多个阶段。

## 对当前工作树的处理建议

三部分计划确认后，不再把当前 `fetch_ticker` 工作作为单独阶段。后续有两种处理：

- 如果实现和测试本身正确，将它并入“第一部分：所有公共及私有 REST 接口”，删除单独的 `3a`
  阶段表达；
- 如果代码与新的统一验收程序冲突，再重写该部分。

不要只因阶段重排就先删除已经验证正确的协议映射；先用新验收标准复审，再决定保留或改写。
