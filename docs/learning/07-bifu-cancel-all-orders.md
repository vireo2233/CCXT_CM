# 第 7 课：Bifu 撤销一个交易对的全部挂单

这一课会在 **Bifu 测试环境**创建两张属于本次验收的限价挂单，再用 CCXT 标准
`cancel_all_orders` 一次撤销。它是正常交易接口，不是 Bifu 的特殊 `mock`。

## 1. 它与单笔撤单有什么区别

- `cancel_order(order_id, symbol)`：明确撤销一张订单。
- `cancel_all_orders(symbol)`：撤销这个交易对下的全部当前挂单，不需要逐个提供订单号。

Bifu 原始接口是 `POST /spot/v1/openOrders/cancel`。请求中的 `instrument_id` 为空或 0 时会撤销
现货账户全部交易对的挂单，影响范围太大。本适配器当前要求必须传 CCXT 交易对，例如
`BTC/USDT`，再转换成对应的 `instrument_id`，避免误撤其他交易对。

## 2. 为什么返回一项而不是两项订单

Bifu 回执只给 `canceled`，含义是“已受理的撤单笔数”，不返回每一张订单的最终状态。CCXT 的
`cancel_all_orders` 返回 Order 列表，因此适配器返回一项标准 Order 外壳，在 `info.canceled` 保留
原始计数；`status` 保持 `None`，不会编造两张已经最终 `canceled` 的订单。

真正验收时还要再次调用 `fetch_open_orders(symbol)`，确认本次创建的两张订单都不再出现。

## 3. 为什么真实验收前必须要求当前挂单为 0

撤销全部挂单不可恢复。如果这个交易对原本已有别人的测试挂单，继续执行会一起撤掉。验收工具
先读取当前挂单：只要不是 0 就停止，不创建订单，也不调用撤销全部。

通过预检后，工具创建两张同价的 `POST_ONLY` 远价单。`POST_ONLY` 保证订单只做 maker；如果价格
会立即成交，交易所应拒绝而不是吃单。工具只清理本轮拿到的订单号，不会用全账户撤单做失败
清理。

## 4. 你亲自运行

在仓库根目录逐行执行：

```bash
set -a
source ../.env.bifu.local
set +a
.venv/bin/python -m examples.accept_bifu_cancel_all_orders \
  BTC/USDT buy 0.0001 60000 --confirm BIFU_TEST_CANCEL_ALL_WRITE
```

参数表示：在测试环境创建两张价格为 60000 USDT、数量为 0.0001 BTC 的远价买单，每张名义金额
6 USDT，达到当前市场最小 5 USDT 要求。运行前仍要确认 60000 明显低于当时 BTC 卖价；若市场价格
已经接近或低于该值，应先停下并重新选择更低且仍满足 5 USDT 最小金额的数量/价格组合。

## 5. 怎样看结果

- `initial_open_order_count=0`：调用前没有其他挂单，允许继续。
- `created_order_count=2`：本轮确实创建了两张测试单。
- `both_open_before_cancel=true`：两张单在撤销前都能从当前挂单取到。
- `cancel_ack_count=2`：Bifu 回执表示受理两笔撤单命令，不等于最终状态已经确定。
- `both_absent_after_cancel=true`：两张本轮测试单都已不在当前挂单中。
- `remaining_open_order_count=0`：这个交易对最终没有挂单残留。

## 6. 你要能回答的五个问题

1. `cancel_order` 与 `cancel_all_orders` 的影响范围有什么不同？
2. 为什么适配器要求必须传 `BTC/USDT`，不允许省略交易对？
3. 为什么 `cancel_ack_count=2` 还不能直接说两张订单最终状态都是 `canceled`？
4. 为什么验收开始前只要发现一张现有挂单就必须停止？
5. 哪两个结果一起证明本轮两张订单已批量撤掉且没有挂单残留？
