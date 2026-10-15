# 第 12 课：Bifu 特殊 `mock` 刷量接口

## 先说结论

Bifu 的 `mock` 不是另一个 URL。官方协议仍使用私有现货下单地址
`POST /spot/v1/order`，但请求里的订单类型固定为 `SANDBOX_MARKET`。本项目把它单独封装为：

```python
order = await exchange.create_mock_order("BTC/USDT", "buy", 5.5)
```

它没有放进 CCXT 标准 `create_order`。这样看到 `create_order` 就知道是真实正常下单，看到
`create_mock_order` 才知道是 Bifu 专用模拟刷量，不会因为一个隐藏参数把两种语义混在一起。

协议来源：Bifu [开发环境现货接口文档](https://flame-api.bifu.dev/docs/spot#现货下单)，访问日期
2026-09-20。官方称它为“沙盒账户专用的模拟市价单”；2026-09-17 内部需求说明补充，它直接产生
交易所内部数据，不进入真实盘口，也不与盘口订单撮合。

## 参数怎样理解

| 调用 | `value` 的单位 | 发给 Bifu 的字段 |
|---|---|---|
| `create_mock_order("BTC/USDT", "buy", 5.5)` | USDT，也就是准备使用的计价币金额 | `quote_qty="5.5"` |
| `create_mock_order("BTC/USDT", "sell", 0.001)` | BTC，也就是准备卖出的基础币数量 | `qty="0.001"` |

适配器固定发送 `type=SANDBOX_MARKET` 和 `time_in_force=IOC`，按市场精度处理数量并检查最小、
最大数量或金额。可在 `params` 里传 `clientOrderId`；也可传 `price` 作为最差接受价保护。不需要
价格保护时不要传 `price`。该方法复用普通现货下单端点的限流成本 `1`；调用前必须先执行
`set_sandbox_mode(True)`，否则适配器会在联网前拒绝写入。

## 返回值和验收边界

Bifu 创建回执只有 `order_id`，表示请求已受理，不代表最终模拟成交已经可见。专有方法返回一个
CCXT Order 外壳，并把 Bifu 原始回执放在 `info`；`status` 保持 `None`，不编造最终状态。

真正的在线验收同时检查：

1. `fetch_my_trades` 能按本次订单号取得标准 Trade；
2. `fetch_open_orders` 中不存在这笔模拟订单，证明它没有变成真实盘口挂单；
3. 输出不显示 Key、账户 ID、订单 ID、clientOrderId 或成交 ID。

验收命令只允许使用 Bifu Sandbox 账户：

```bash
python -m examples.accept_bifu_mock_order \
  BTC/USDT buy 5.5 --confirm BIFU_TEST_MOCK_WRITE
```

验收工具为本轮写入生成一个 16 位 `clientOrderId`，用于失败时只识别和清理本轮订单。它不会输出
这个值，也不会自动重试写请求。若请求超时，结果可能未知：工具只按本轮订单号或客户端订单号检查
和清理当前挂单；仍需用私有订单/成交查询完成对账，确认没有已产生的模拟成交后才能决定是否再次
调用。Bifu 文档把 `client_order_id` 标为必填并说明重复值会触发重复订单号错误，本项目只把它作为
本轮关联键，不额外假定服务端提供比文档更强的幂等保证。

## 当前在线结果

2026-09-20 使用现有 Jacky 测试 Key 调用时，Bifu 返回 HTTP 403 和业务码 `4006`：
`SANDBOX_MARKET requires Sandbox account`。这证明 URL、签名和请求结构已经到达正确接口，但该 Key
属于普通测试账户，没有 Sandbox 账户权限；服务端没有生成模拟订单或成交。适配器把该错误映射为
`AccountNotEnabled`。

平台最初提供的内网 Key 在公网开发 URL 的私有请求上返回 HTTP 401、`4000 UNAUTHENTICATED`。
重新生成非内网公网 Key 后，同一适配器已通过 `https://flame-api.bifu.dev` 完成私有只读查询，证明
公网 Key、URL 和签名均有效；但 `SANDBOX_MARKET` 写入返回 HTTP 403、`4006 SANDBOX_MARKET
requires Sandbox account`。失败后的只读复核确认没有新增挂单、历史订单或成交。

平台随后创建了已开通权限的公网 Sandbox 账号。最终在线验收真实完成 5.5 USDT 的模拟买入和
0.001 BTC 的模拟卖出：两笔创建回执都有 CCXT Order 标准字段并保持 `status=None`，每笔都能查询到
1 条对应的 CCXT 标准 Trade，而且当前挂单中都不存在该订单。由此可以确认公网账号、签名、买入按
计价币金额、卖出按基础币数量、标准格式转换和“不进入盘口”的完整链路均已通过。

## 问题与答案

1. **`mock` 和正常市价单有什么区别？** 正常市价单用 `create_order(..., "market", ...)`，会进入
   正常交易流程；`mock` 用独立的 `create_mock_order`，只适用于 Bifu Sandbox 账户并产生模拟数据。
2. **为什么底层还是 `/spot/v1/order`？** Bifu 用请求中的 `type=SANDBOX_MARKET` 区分行为，官方
   没有提供另一个 mock URL。
3. **为什么不能把 `SANDBOX_MARKET` 直接传给 `create_order`？** 那会让 CCXT 标准下单方法同时承担
   两种完全不同的语义，调用方容易误用；本项目明确拒绝这种调用。
4. **买入的 5.5 和卖出的 0.001 分别是什么？** 买入是 5.5 USDT 预算，卖出是 0.001 BTC 数量。
5. **创建回执成功就能说模拟成交成功吗？** 不能。还要查询个人成交并确认当前挂单中没有这笔单。
