# 第 13 课：Bifu WebSocket 实时推送

## 先理解它解决什么问题

REST 是“我现在去问一次”，WebSocket 是“连接保持着，有新数据时交易所主动推给我”。做市程序会用
REST 做初始化和对账，用 WebSocket 接收持续变化的行情、订单、成交和余额。WebSocket 不能代替
REST：连接中断期间可能漏事件，所以恢复后仍要重新取得可信基线并做对账。

创建方式和其他 CCXT Pro 交易所一致：

```python
from ccxt_cm import create_exchange

exchange = create_exchange("bifu", config, mode="pro")
exchange.set_sandbox_mode(True)
book = await exchange.watch_order_book("BTC/USDT")
```

测试和生产共用同一个 `BifuPro` 适配器。测试环境由 `set_sandbox_mode(True)` 选择；生产环境以后由
调用方配置已确认的生产 REST/WS URL 和生产 Key。不要在同一实例中途切换环境。

## 已接入的方法

| CCXT 方法 | Bifu 数据 | 说明 |
|---|---|---|
| `watch_ticker` | `ticker` | 一个交易对的 24 小时行情 |
| `watch_tickers` | `stream/tickers` | 全市场行情，未知 instrument 会跳过，不拖垮整批数据 |
| `watch_trades` | `trade` | 公共逐笔成交，不是自己的账户成交 |
| `watch_order_book` | REST `depth` + WS `depth` | WS 建连后缓冲增量，再取 REST 快照并按 `prev_id/last_id` 衔接 |
| `watch_bids_asks` | `book_ticker` | 买一、卖一；Bifu 要求每个交易对一条连接 |
| `watch_ohlcv` | `kline` | 当前协议只确认 `1m`，不能假装支持其他周期 |
| `watch_orders` | `order_update` | 当前账户订单变化 |
| `watch_my_trades` | `order_update.fill` | 从订单事件里的真实成交明细提取 |
| `watch_balance` | `balance_update` | 现货余额绝对值更新，不把未出现币种当成零 |

Bifu 的订阅写在 WebSocket URL 中，连接建立后不能在同一连接动态增加交易对或频道，因此单市场
公共流按交易对和频道分连接。私有流连接 `/spot/v1/userDataStream` 时使用 `X-API-KEY`、`X-TS`
和 `X-SIGN` 请求头鉴权；URL 本身不携带 Key、Secret、签名或 token。每次重新连接都会生成新的
时间戳和签名，鉴权请求头只应用到该私有连接，不会泄露给公共行情连接。

官方协议也提供约 5 分钟有效的 `listenKey` 方式，适合浏览器等不方便设置 WebSocket 请求头的
客户端。本 Python 适配器能直接设置签名请求头，因此不采用把凭证放进 URL 的方式。

## 你自己怎么验收

公共流不需要 Key，在仓库根目录运行：

```bash
.venv/bin/python -m examples.inspect_bifu_ws BTC/USDT
```

每一项的 `received=true` 表示真实收到消息；`standard_fields_present=true` 表示已经转成 CCXT 字段。
它不打印实时价格，避免把一次快照误当成长期固定结果。

私有流会创建一笔 5.5 USDT 的 Sandbox 模拟买入来触发事件。先加载本机凭证，再显式确认写入：

```bash
set -a
source ../.env.bifu.mock.local
set +a
.venv/bin/python -m examples.accept_bifu_ws BTC/USDT 5.5 \
  --confirm BIFU_TEST_WS_WRITE
```

这个工具只输出“是否收到”和“标准字段是否齐全”，不输出 Key、Secret、账户 ID、订单 ID、成交
ID 或精确余额。它只允许 Sandbox `create_mock_order`，不执行生产写入。

## 常见问题和答案

1. **为什么 `watch_trades` 和 `watch_my_trades` 都有 trades？** 前者是整个市场任何人的公开成交，
   后者只表示当前账户自己的成交。
2. **为什么订单簿要先取 REST 快照？** 官方把 WS `depth` 定义为增量，没有保证首帧是完整盘口。
   适配器会先建立 WS 并缓冲消息，同时用 REST 取得带 `last_id` 的基线，再按序号衔接增量。中间
   少一段后本地盘口已经不完整，会抛 `InvalidNonce`、清缓存并重连取得新快照。
3. **为什么只支持 1 分钟 WebSocket K 线？** REST 支持多个周期，但当前 WebSocket 协议只确认了
   `1m` 且订阅 URL 没有周期参数。没有协议证据就不能虚报能力。
4. **为什么创建模拟订单能同时验收三条私有流？** 创建和成交会触发订单更新，嵌套 `fill` 是自己的
   成交，资产变化会触发余额更新；三者分别解析成 CCXT Order、Trade 和 Balance。
5. **WebSocket 收到订单就能不用 REST 了吗？** 不能。私有流是变化事件，不保证它本身就是完整
   账户快照；启动和重连后仍需用 REST 对账。
6. **断线会自动保留旧缓存吗？** 不会把依赖连续性的旧缓存当成仍可信的数据；后续调用重新连接并
   获取新基线。调用方仍要决定重试节奏和数据时效要求。

## 当前明确边界

- 适配器只发布现货能力，`margin_balance_update` 不会伪装成现货余额。
- 服务端心跳、Pong 和底层重连由 CCXT Pro WebSocket 客户端处理；订单簿断档由 Bifu 适配器主动
  拒绝并重建连接。
- `watch_tickers` 会忽略市场元数据中不存在的 instrument；已知市场仍正常返回。
- WebSocket 实时更新不能证明生产环境可用。生产 URL 与生产 Key 到位后，仍先做生产只读验收。

协议依据：Bifu 开发环境 [WebSocket 文档](https://flame-api.bifu.dev/docs/websocket)，最后修改
2026-08-27，本项目于 2026-09-20 复核。
