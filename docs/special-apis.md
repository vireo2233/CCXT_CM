# 特殊接口处理

目标是保留交易所能力，不把专有语义强行伪装成标准功能。

## 三层选择

| 层 | 何时使用 | 调用与返回 |
|---|---|---|
| 标准方法的 `params` | 同一操作的交易所参数，如 `clientOrderId`、受支持的 `postOnly`、`reduceOnly`、账户/子账户选择 | 调用 `create_order(..., params=...)` 等，返回 CCXT 标准结构 |
| 官方/自定义 implicit API | 交易所特有 endpoint；暂时没有对应统一方法 | 直接调用类上已声明的 endpoint，返回原始响应 |
| 专有显式方法 | 需要可发现的语义、参数检查或固定解析，但无合适标准方法 | 自定义命名方法，并用 `SpecialMethod` 标明文档、权限、写入性质和返回 |

优先顺序是标准接口 → 已有 implicit API → 必要的新专有方法。不增加一个任意 URL 的通用网关，不在工厂根据交易所硬编码路由。

## 专有方法示例

本机参考类声明了 `fetch_account_limits(params=None)`：需要认证、只读，返回 `{"requestsPerMinute": 60}` 这样的原始额度信息。对应 implicit API 是 `private_get_account_limits`，CCXT 的 `Entry` 描述符负责调用统一 `request`。

```python
from ccxt_cm import special_methods

for spec in special_methods(exchange):
    print(spec["method"], spec["private"], spec["mutating"], spec["documentation"])

# 确认当前类有这项专有声明后再直接调用：
# quota = await exchange.fetch_account_limits()
```

元数据不是新的交易返回类型，也不是权限控制。`special_methods` 只返回扩展作者显式声明的专有方法，不枚举官方所有 implicit endpoints。

## Bybit Testnet 的原客户端订单身份查询

`fetch_bybit_testnet_spot_order_by_client_id(exchange, symbol, client_order_id)` 是只读的窄适配，复用已创建的官方 CCXT 客户端，不另建连接或修改 `has`。调用前应加载市场、绑定 sandbox，并通过 `verify_bybit_testnet_account` 核对实际账户。

该方法仅接受 Bybit 官方 Testnet 路由、spot 市场及原始 client ID。使用已声明的 `GET /v5/order/realtime`，仅在空结果时查询 `GET /v5/order/history`，两次均发送原 `symbol + orderLinkId`。先检查原始响应恰好一条、客户端身份和交易对一致，再调用官方 `parse_order` 返回标准订单；多条、续页、外来身份及格式不完整均抛出 `ClientOrderLookupUnconfirmed`。

官方 `fetch_order(None, symbol, {"orderLinkId": id, "acknowledged": True})` 虽能透传原身份，但当前实现直接选择第一条 realtime 记录，丢失多条检测证据，因此这里使用原生 implicit API 做必要的边界补齐，不扩展通用下单接口。

两处都为空仍表示未知，不证明订单从未创建，也不允许据此重发或释放未核清的预算。helper 本身不增设重试；传输行为遵循既有客户端设置。调用方保留原意图，后续继续按同一身份核对。无创建、撤单或改价操作。

来源：[实时订单查询](https://bybit-exchange.github.io/docs/v5/order/open-order)、[订单历史](https://bybit-exchange.github.io/docs/v5/order/order-list)。历史数据可能延迟；服务器重启后近期已关闭订单可能只在历史接口可查。离线测试使用真实 CCXT 签名及请求构造，仅 mock HTTP 传输，不构成 Testnet 实际查询验收。

## Bifu `mock`

Bifu 的模拟刷量使用现货下单地址，但协议类型是 `SANDBOX_MARKET`，且只允许 Sandbox 账户。
它不是 CCXT 标准下单能力，因此由 `create_mock_order(symbol, side, value, params=None)` 单独暴露，
并声明为 private、mutating 的 `SpecialMethod`。标准 `create_order` 不接受 `mock` 或
`SANDBOX_MARKET`。完整参数、返回和验收边界见
[第 12 课](learning/12-bifu-mock.md)。

## 每个专有方法的文档至少包含

- 原始 endpoint、HTTP/WS 方法、协议版本与来源。
- 账户/市场范围、认证权限、是否产生状态变更。
- 参数名称、类型、必填项、默认值、单位和互斥关系。
- 原始返回示例，或说明归一化到哪一种 CCXT 结构。
- 限流成本、超时后如何核查、是否支持幂等键。
- 测试样本、特殊错误和未覆盖边界。

特别注意：快单、批量撤单、修改订单、子账户查询、资金划转、保证金模式等语义各异，不能只因名字近似就映射成同一个操作。先确认其语义再实现。

旧系统的专有业务操作不自动进入本库，也不作为所有交易所必须实现的接口。新增写入型专有方法需独立评审和验收；不通过虚假订单/成交响应掩盖差异。
