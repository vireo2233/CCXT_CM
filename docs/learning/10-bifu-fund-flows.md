# 第 10 课：看懂 Bifu 资金流水

这一课使用 CCXT 标准方法 `fetch_ledger()` 查询账户余额为什么发生变化。它是**只读查询**，不会
划转、充值、提现或下单。Bifu 的原始接口是 `GET /spot/v1/fundFlows`。

## 1. 资金流水和余额有什么区别

- `fetch_balance()` 是现在还有多少钱，像账户余额快照。
- `fetch_ledger()` 是钱为什么增加或减少，像账户流水账。

Bifu 返回 `CREDIT`、`WITHDRAW`、`TRANSFER`、`BORROW`、`REPAY`、`INTEREST` 等原始类型；
适配器把它们转换成 CCXT Ledger Entry。CCXT 的 `amount` 必须是绝对值，资金方向单独放在
`direction`：`in` 表示流入，`out` 表示流出。原始正负号和全部原始字段仍保存在 `info` 中。

| Bifu `kind` | CCXT `type` |
|---|---|
| `CREDIT` | `deposit` |
| `WITHDRAW` | `withdrawal` |
| `TRANSFER` | `transfer` |
| `BORROW` | `borrow` |
| `REPAY` | `repay` |
| `INTEREST` | `interest` |

`account` 使用 Bifu 的产品和范围组成，例如 `SPOT:0`。只有内部划转能确认对方账户时，才填写
`referenceAccount`；没有证据的 `before`、`after`、`status`、`fee` 和 `referenceId` 保持
`None`。

## 2. 时间、数量和分页

调用方式：

```python
entries = await exchange.fetch_ledger(
    "USDT",
    since=开始时间毫秒,
    limit=100,
    params={"until": 结束时间毫秒},
)
```

Bifu 一页最多 1000 条，`since` 对应包含起点，`until` 对应不包含终点；同时传起止时间时，Bifu
要求区间不超过 30 天。Bifu 原接口没有按币种筛选参数，所以 `code="USDT"` 是在当前一页解析后
筛选；结果少于 `limit` 不代表服务器没有其他币种流水。

当前方法一次返回一页。下一页游标保留在 `exchange.last_json_response["next_cursor"]`，下一次可用
`params={"cursor": "上一页游标"}`。不能把一页结果说成账户全部历史流水。

## 3. 测试环境只读验收

在仓库根目录逐行执行：

```bash
set -a
source ../.env.bifu.local
set +a
.venv/bin/python -m examples.inspect_bifu_ledger --limit 5
```

工具只输出样本数量、方向、类型、币种、标准字段是否齐全和是否有下一页。它不会打印 Key、Secret、
账户 ID、流水 ticket、精确金额或精确时间。如果 `sample_count=0`，只能证明鉴权和空页响应正常，不能
证明每一种资金类型都有真实样本。

## 4. 本轮问题与答案

1. 为什么 `amount` 没有负数？因为 CCXT 把绝对金额和流入/流出方向拆成两个字段。
2. 为什么 `fetch_balance()` 不能替代 `fetch_ledger()`？余额只告诉你现在的结果，不告诉你变化原因。
3. 为什么一页只有 5 条不能称为全部流水？接口使用游标分页，下一页可能仍有数据。
4. 为什么测试工具不打印金额和 ticket？它们属于账户敏感信息，验收只需证明接口与结构有效。
5. 为什么没有依据的字段保持 `None`？统一格式不等于编造数据；未知比错误值更安全。
