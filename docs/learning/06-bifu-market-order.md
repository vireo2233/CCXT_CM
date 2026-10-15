# 第 6 课：Bifu 市价买单和市价卖单

这一课会真的操作 **Bifu 测试环境**，使用测试资金立即成交。市价单仍是 CCXT 标准
`create_order`，不是 Bifu 的特殊 `mock` 接口。

## 1. 市价单和限价单有什么不同

- 限价单：你指定价格，订单可能先挂在盘口，之后还能撤单。
- 市价单：你要交易所按当前盘口尽快成交。Bifu 使用 `IOC`，能立即成交的部分成交，不能立即成交
  的剩余部分取消。因此市价单通常来不及再手工撤单。

这也是为什么本轮不能沿用上一课“下单后再撤单”的验收方式。市价单要通过成交记录和当前挂单
一起确认。

## 2. 买入和卖出的数量单位不同

| 操作 | 你表达的意思 | Bifu 原始请求 | CCXT 调用 |
|---|---|---|---|
| 市价买入 | 准备花多少计价币，例如 5.5 USDT | `quote_qty` | `create_market_buy_order_with_cost("BTC/USDT", 5.5)` |
| 市价卖出 | 准备卖多少基础币，例如 0.00001 BTC | `qty` | `create_order("BTC/USDT", "market", "sell", 0.00001)` |

也可以使用标准 `create_order` 的 `amount + price` 形式创建市价买单。适配器按 CCXT 常见规则用
`amount × price` 计算计价币预算，再按市场精度生成 `quote_qty`。这里的 `price` 用于计算预算，
不伪装成最终成交价。

## 3. 为什么创建回执里的状态仍是空

Bifu 创建订单的响应只有订单号。因此刚创建时，标准 Order 中的 `status`、`filled` 和 `cost`
都可能是 `None`；这只表示回执没有这些信息，不表示下单失败。

测试环境中的终态订单还可能很快从单笔查询接口消失并返回 `OrderNotFound`。本轮真实验收同时
确认三件事：

1. 创建回执包含 CCXT 标准字段；
2. `fetch_my_trades` 能取到这笔订单的标准成交；
3. `fetch_open_orders` 中没有残留挂单。

只有单笔查询找不到，不能单独证明成交；成交记录才是这次真实成交的关键证据。

## 4. 你亲自运行

在仓库根目录逐行执行。先做 5.5 USDT 的测试市价买入：

```bash
set -a
source ../.env.bifu.local
set +a
.venv/bin/python -m examples.accept_bifu_market_order \
  BTC/USDT buy 5.5 --confirm BIFU_TEST_MARKET_WRITE
```

如果要验证市价卖出，第三个值是 BTC 数量，不是 USDT：

```bash
.venv/bin/python -m examples.accept_bifu_market_order \
  BTC/USDT sell 0.00001 --confirm BIFU_TEST_MARKET_WRITE
```

确认词必须完全一致。工具只会重试不会改动账户的市场元数据预检，不会重试创建订单；输出不会
显示订单号、clientOrderId、成交号、账户 ID、Key 或 Secret。生产环境禁止运行这两条命令。

## 5. 怎样看结果

- `requested_value_unit="quote"`：买入值的单位是 USDT。
- `requested_value_unit="base"`：卖出值的单位是 BTC。
- `create_ack_standard_fields_present=true`：创建回执已整理为标准 Order 字段。
- `query_after_create_found=false`：测试环境的单笔接口已不再暴露终态订单，不等于失败。
- `trade_count=1`：账户成交接口找到了这笔订单的真实测试成交。
- `trade_standard_fields_present=true`：成交已经整理成 CCXT 标准 Trade。
- `order_absent_from_open_orders=true`：没有遗留挂单。

## 6. 你要能回答的五个问题

1. 为什么市价买入的 5.5 表示 USDT，而市价卖出的 0.00001 表示 BTC？
2. Bifu 的 `quote_qty` 和 `qty` 分别表示什么？
3. 为什么市价单不能像上一课的限价单那样依赖“创建后再撤单”验收？
4. 为什么 `fetch_order` 返回 `OrderNotFound` 仍不能直接说订单成交？
5. 哪两个结果一起说明市价订单已成交且没有残留挂单？
