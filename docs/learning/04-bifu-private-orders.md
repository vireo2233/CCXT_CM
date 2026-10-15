# 第 4 课：看懂 Bifu 私有订单查询

这一课只读数据，不创建、修改或撤销订单。四个方法的区别是：

- `fetch_open_orders()`：当前还没有结束的订单。
- `fetch_closed_orders()`：已经成交、撤销、拒绝或过期的历史订单。
- `fetch_order()`：知道订单 ID 时查询一张订单。
- `fetch_my_trades()`：当前账户自己的成交明细；它不同于公共行情 `fetch_trades()`。

## 1. 为什么返回值要转换

Bifu 使用 `BTC-USDT`、`PARTIALLY_FILLED`、`POST_ONLY` 等字段；CoinMaker 通过 CCXT 调用时得到
`BTC/USDT`、`open`、`PO` 等统一字段。原始响应仍保留在每条结果的 `info` 中，排查问题时可以看，
业务逻辑优先使用 CCXT 标准字段。

订单状态对应关系：

| Bifu | CCXT |
|---|---|
| `NEW` / `PENDING` / `OPEN` / `PARTIALLY_FILLED` / `CANCELING` | `open` |
| `FILLED` | `closed` |
| `CANCELED` | `canceled` |
| `REJECTED` | `rejected` |
| `EXPIRED` | `expired` |

如果 Bifu 以后增加了新状态，适配器会先返回 `None`，不会把一个 CCXT 不认识的字符串假装成标准
状态。等协议确认后，再补映射和测试。

还有一个容易误解的边界：按 USDT 金额买入的市价单可能返回 `orig_qty="0"` 和非零
`quote_qty`。这个 0 不是“订单数量为 0”，而是请求时没有指定基础币数量，所以 CCXT 的
`amount`、`remaining` 保持 `None`；实际成交数量仍放在 `filled`，实际成交额放在 `cost`。
响应带有 `cum_fee` 和 `fee_asset_id` 时，适配器会转换为标准 `fee.cost` 和 `fee.currency`。

## 2. 测试与生产仍是同一个适配器

测试环境调用前执行 `exchange.set_sandbox_mode(True)`；生产环境由调用方传入正式 URL 和正式 Key，
不要调用这个开关。四个方法没有测试版、生产版两套实现。

## 3. 分页边界

`fetch_closed_orders()` 和 `fetch_my_trades()` 当前读取服务端一页。可以用 `since`、`limit` 和
`params={"cursor": "上一页返回的游标"}` 请求指定页面，但 CCXT 的列表返回值本身不携带下一页
游标。因此不能把单次结果宣传为账户全部历史记录；需要全量翻页时再增加明确的分页工具。

## 4. Jacky 手工验收

在仓库根目录逐行执行：

```bash
set -a
source ../.env.bifu.local
set +a
.venv/bin/python -m examples.inspect_bifu_orders BTC/USDT
```

工具不会打印 Key、Secret、账户 ID、订单 ID 或成交 ID，也不会执行写操作。当前测试账户的真实结果
可能是：挂单为 0、历史订单有样本、成交记录有样本。`single_order_query_verified=false` 表示拿当前
历史样本的 ID 查询时服务端返回了 `OrderNotFound`；具体是保留时间还是其他服务端规则，要等创建
新测试订单后再验证，不能提前下结论。程序没有把这个失败当成成功。

## 5. 你要能回答的五个问题

1. `fetch_trades()` 和 `fetch_my_trades()` 有什么区别？
2. 为什么 `fetch_open_orders()` 返回空列表不代表接口失败？
3. 为什么历史订单存在，但同一个 ID 的 `fetch_order()` 仍可能返回 `OrderNotFound`？
4. 为什么当前一页历史订单不能称为“账户全部历史订单”？
5. 为什么按 USDT 金额买入的市价单不能直接把 `orig_qty=0` 解释成 `amount=0`？
