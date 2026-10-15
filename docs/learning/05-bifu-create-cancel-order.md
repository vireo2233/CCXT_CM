# 第 5 课：限价下单与单笔撤单

这一课会真的操作 **Bifu 测试环境**，但使用的是测试资金，不连接生产。正常下单在 CCXT 中叫
`create_order`，正常撤单叫 `cancel_order`；它们都不是 Bifu 的特殊刷量 `mock` 接口。

## 1. 这次会发生什么

验收工具按顺序做四件事：

1. 创建一张 `POST_ONLY` 限价单；
2. 用 `fetch_order` 确认它能被查到；
3. 用 `cancel_order` 发出撤单命令；
4. 再查询订单，并确认它不在 `fetch_open_orders` 当前挂单里。

`POST_ONLY` 的意思是只允许订单挂在盘口成为 maker；如果价格会立刻成交，交易所应撤掉它而不是
让它成为 taker。这适合做接口验收，但仍要使用测试环境和小金额。

## 2. 为什么创建和撤销的返回状态是空

Bifu 的下单响应只有 `order_id`，表示“命令已受理”，不表示订单已经挂好或成交。撤单响应里的
`accepted=true` 也只表示撤单命令已受理，不等于最终状态已经是 `canceled`。因此两个 ACK 返回的
CCXT `status` 都保持 `None`，最终状态必须再查询。

当前测试环境还有一个实际边界：订单结束后，单笔查询可能返回 `OrderNotFound`。这不能被程序伪造
成 `canceled`。验收工具会继续检查当前挂单；只有订单确实不在当前挂单中，才报告
`order_absent_from_open_orders=true`。

## 3. 你亲自运行

在仓库根目录逐行执行：

```bash
set -a
source ../.env.bifu.local
set +a
.venv/bin/python -m examples.accept_bifu_order_lifecycle \
  BTC/USDT buy 0.00008 70000 --confirm BIFU_TEST_WRITE
```

这组参数的名义金额是 `0.00008 × 70000 = 5.6 USDT`，高于市场要求的 5 USDT。确认词必须完整输入；
少写或写错时，工具不会发出任何写请求。工具不会重试下单，创建成功后会尽力执行一次撤单清理，
输出中也不会显示订单号或 clientOrderId。

下单前，工具会先读取市场元数据。这个步骤是不会改动账户的公共 GET，因此遇到瞬时网络错误时
最多尝试 3 次（失败后重试 2 次）；只有预检成功后才会发送下单请求。真正的下单 POST 仍然只发送
一次，不会自动重试。

如果执行前 BTC/USDT 已经跌到 70000 附近，请先停下告诉我，我们重新选择明显低于当前买一价的
测试价格。生产环境禁止运行这条命令。

## 4. 怎样看结果

- `create_ack_standard_fields_present=true`：创建回执已整理为 CCXT 标准字段。
- `create_status=null`：受理回执不是最终订单状态，这是正确结果。
- `query_after_create_status="open"`：新订单创建后能通过单笔接口查到。
- `cancel_command_accepted=true`：撤单命令已被受理。
- `query_after_cancel_found=false`：结束后单笔接口不再返回该订单，这是当前测试环境的实际行为。
- `order_absent_from_open_orders=true`：订单已经不在当前挂单里，没有遗留挂单。

## 5. 你要能回答的五个问题

1. 正常下单为什么叫 `create_order`，而不是 `trade` 或 `mock`？
2. 为什么 `create_status=null` 不代表下单失败？
3. `POST_ONLY` 在这次验收里起什么保护作用？
4. 为什么撤单返回受理后还要再次查询当前挂单？
5. 为什么撤单后的 `OrderNotFound` 不能直接改写成 `canceled`？
