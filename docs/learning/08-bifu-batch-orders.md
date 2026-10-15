# 第 8 课：Bifu 批量下单与批量撤单

这一课使用 CCXT 标准 `create_orders` 和 `cancel_orders`。两者都是正常交易接口，不是 Bifu 的
特殊 `mock`。

## 1. 批量接口解决什么问题

逐笔调用 `create_order` 两次，会发出两次 HTTP 请求；`create_orders` 把同一交易对的多张订单放进
一次请求。Bifu 的批量请求只有一个 `instrument_id`，所以适配器要求一批订单必须属于同一个
CCXT 交易对，且每批为 1–100 笔。

```python
orders = await exchange.create_orders(
    [
        {
            "symbol": "BTC/USDT",
            "type": "limit",
            "side": "buy",
            "amount": 0.00008,
            "price": 70000,
            "params": {"postOnly": True},
        },
        {
            "symbol": "BTC/USDT",
            "type": "limit",
            "side": "buy",
            "amount": 0.00008,
            "price": 70000,
            "params": {"postOnly": True},
        },
    ]
)
```

Bifu 原始接口是 `POST /spot/v1/orders`。返回的 `acks` 与输入订单一一对应且顺序相同。适配器也
保持这个顺序：`PENDING` 映射为 CCXT `open`，`REJECTED` 映射为 `rejected`，原始拒绝码保留在
对应订单的 `info.reject_code`，不会因为其中一笔失败就把整批伪装成成功。

## 2. 批量撤单的返回为什么没有最终状态

```python
acks = await exchange.cancel_orders(
    [orders[0]["id"], orders[1]["id"]],
    "BTC/USDT",
)
```

Bifu 原始接口是 `POST /spot/v1/orders/cancel`。它只返回整批是否受理和受理笔数，不返回每张订单
最终变成什么状态。因此适配器按输入订单号返回同序的 CCXT Order 回执，但 `status` 保持
`None`。要证明撤单真正完成，必须继续调用 `fetch_open_orders("BTC/USDT")`，确认这些订单号已经
消失。

## 3. 为什么验收工具仍然很谨慎

`examples.accept_bifu_batch_orders` 会：

1. 明确启用 Bifu 测试环境，且要求固定确认词。
2. 写入前确认该交易对当前挂单为 0。
3. 一次批量创建两张远离盘口的 `POST_ONLY` 限价单。
4. 确认两张订单都真实出现在当前挂单中。
5. 一次批量撤销这两张订单，并再次确认当前挂单为 0。
6. 中途失败时，只按本轮订单号逐笔清理，再复查是否仍有残留。

创建和撤单 POST 都不会盲目重试。批量创建超时时，工具会用本轮预先生成的 `clientOrderId` 从
当前挂单里识别自己的订单，只清理自己的测试单。

## 4. 你亲自运行

在仓库根目录逐行执行：

```bash
set -a
source ../.env.bifu.local
set +a
.venv/bin/python -m examples.accept_bifu_batch_orders \
  BTC/USDT buy 0.00008 70000 --confirm BIFU_TEST_BATCH_WRITE
```

每张订单名义金额是 `0.00008 × 70000 = 5.6 USDT`，高于当前最小 5 USDT。运行前仍需确认
70000 明显低于当时 BTC 卖价；否则应停下，重新选择不会立即成交、同时仍满足最小金额的参数。

## 5. 怎样看结果

- `batch_size=2`、`create_ack_count=2`：一次批量请求包含两张订单，两项回执都已取得。
- `both_open_before_cancel=true`：撤单前，两张订单都真实出现在当前挂单中。
- `cancel_ack_count=2`：批量撤单请求受理了两个订单号，但这不是最终状态证明。
- `both_absent_after_cancel=true`：两张本轮订单都已从当前挂单中消失。
- `remaining_open_order_count=0`：最终没有测试挂单残留。

## 6. 问题与标准答案

1. **为什么一批订单只能用一个交易对？** Bifu 批量请求外层只有一个 `instrument_id`，整批条目
   共用它；混合交易对无法被原始协议准确表达，所以适配器在联网前拒绝。
2. **`create_orders` 的第二项被拒绝时，第一项会怎样？** 两项是逐条 result-or-reject；适配器按
   原顺序返回第一项成功和第二项 `rejected`，不会把部分失败隐藏掉。
3. **为什么 `cancel_ack_count=2` 不能直接说两张订单最终都是 `canceled`？** 该回执只表示命令已
   受理。最终结果必须再查当前挂单，确认两个订单号都已经消失。
4. **如何证明这次确实调用了批量接口，而不是循环调用单笔接口？** 协议测试断言只有一个
   `POST /spot/v1/orders` 和一个 `POST /spot/v1/orders/cancel`；在线验收结果同时要求批次大小与
   回执数量均为 2。
5. **哪两个字段一起证明撤单完成且无残留？** `both_absent_after_cancel=true` 证明本轮两张单都
   消失，`remaining_open_order_count=0` 证明该交易对没有其他挂单残留。
