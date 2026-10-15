# 第 9 课：Bifu 单笔改单与批量改单

这一课使用 CCXT 标准 `edit_order` 和 `edit_orders`。它们属于正常交易接口，不是 Bifu 的特殊
`mock`。

## 1. 改单与“撤掉再重下”有什么区别

`edit_order` 调用 Bifu 原生 `POST /spot/v1/order/amend`，订单 ID 不变；`edit_orders` 调用原生
`POST /spot/v1/orders/amend`，一次修改同一交易对的 1–100 张订单。适配器没有使用 CCXT 基类中
“先撤单、再下新单”的模拟逻辑，因此不会产生新的订单 ID，也不会把两个写操作伪装成一个原生
改单。

```python
ack = await exchange.edit_order(
    order_id,
    "BTC/USDT",
    "limit",
    "buy",
    amount=0.00008,  # 新的订单总数量，不是增加 0.00008
    price=69000,
)
```

Bifu 的成功响应只返回订单 ID，表示改单命令已经受理。标准 Order 的 `status` 因此保持 `None`；
最终是否改成功，要继续调用 `fetch_open_orders` 或 `fetch_order`，确认同一订单 ID 的价格或数量
已经变化。

## 2. 批量改单怎样处理逐项结果

```python
acks = await exchange.edit_orders(
    [
        {
            "id": first_order_id,
            "symbol": "BTC/USDT",
            "type": "limit",
            "side": "buy",
            "amount": 0.00008,
            "price": 68000,
        },
        {
            "id": second_order_id,
            "symbol": "BTC/USDT",
            "type": "limit",
            "side": "buy",
            "amount": 0.00008,
            "price": 67000,
        },
    ]
)
```

Bifu 的批量 ACK 与请求同序，每项都有 `order_id`、`accepted` 和可能的 `reject_code`。适配器核对
数量、顺序和订单 ID，并在 `info` 中保留每项原始结果。`accepted=true` 仍然只是受理，不等于
最终状态；某项被拒绝时也不会掩盖其他项的结果。

## 3. 可以修改什么，不能修改什么

- 限价单可改总数量和价格；传入的 `amount` 是修改后的总数量，不是增量。
- 触发单可通过 Bifu 原始字段修改上下触发价和委托价；值 `0` 表示清除相应条件。
- 市价单通常已经立即成交，不能改单；适配器在联网前拒绝 `type="market"`。
- 数量仍须满足市场最小值、最大值和精度；价格与触发价按市场精度处理。
- 批量改单只能包含同一交易对，因为 Bifu 请求外层只有一个 `instrument_id`。

## 4. 安全验收程序做什么

`examples.accept_bifu_amend_orders` 会：

1. 强制启用 Bifu 测试环境并要求固定确认词。
2. 读取当前行情，拒绝可能越过最新成交价的测试价格。
3. 确认当前挂单为 0，再原生批量创建两张本轮专属 POST_ONLY 限价单。
4. 原生单改第一张，查询确认订单 ID 不变且新价格已可见。
5. 原生批改两张，逐项确认 ACK，并查询确认两张新价格都已可见。
6. 批量撤销两张订单，确认本轮订单消失且最终挂单为 0。
7. 中途失败时，只按本轮订单号或 clientOrderId 逐笔清理并复查。

创建、单改、批改和撤单的写请求都不会盲目重试。网络超时时服务端可能已经执行，所以程序先按
本轮标识查找并清理，不能再次发送同一个改单请求。

## 5. 你亲自运行

在仓库根目录逐行执行：

```bash
set -a
source ../.env.bifu.local
set +a
.venv/bin/python -m examples.accept_bifu_amend_orders \
  BTC/USDT buy 0.00008 70000 69000 68000 67000 \
  --confirm BIFU_TEST_AMEND_WRITE
```

上面的四个买价都必须低于运行时 BTC 最新成交价，每张订单在每次修改后也必须继续满足 5 USDT
最小名义金额。如果行情变化导致条件不成立，程序会在创建订单前停止；应重新选择安全价格和数量，
不能删除保护检查。

## 6. 怎样看结果

- `single_amend_ack_status=null`：单改回执没有提供最终状态，适配器没有编造状态。
- `single_amend_verified=true`：同一订单 ID 的新价格已经在当前挂单中可见。
- `batch_amend_ack_count=2` 与 `batch_amend_all_accepted=true`：两项批改回执都已取得并受理。
- `batch_amend_verified=true`：两张订单的新价格都已通过当前挂单复查。
- `both_absent_after_cancel=true` 与 `remaining_open_order_count=0`：本轮订单已清理且没有挂单残留。

## 7. 问题与标准答案

1. **为什么不用“撤单后重新下单”实现改单？** 那会产生新的订单 ID，并把两个独立写操作伪装成
   一个原生操作；Bifu 已提供改单接口，所以应直接调用它。
2. **`amount=0.00008` 表示增加还是改单后的总数量？** 表示新的订单总数量，不是增加量。
3. **为什么单改回执的 `status` 是 `None`？** Bifu 回执只证明命令受理，没有返回最终订单状态；
   必须查询订单或当前挂单验证。
4. **批改中一项失败时，能否把整批都写成成功？** 不能。必须保留每项 `accepted` 和
   `reject_code`，并按原顺序返回。
5. **改单超时为什么不能自动再发一次？** 第一次请求可能已在服务端成功；重发可能造成重复变更
   或把后续状态覆盖。正确做法是查询同一订单 ID，确认状态并清理测试单。
