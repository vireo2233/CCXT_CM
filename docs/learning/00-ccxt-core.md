# 第 0 课：先看懂 CCXT 在做什么

这一课不接 Bifu、不使用 Key、不下单。目标是先用一个官方支持的交易所理解 CCXT，避免以后
把 CCXT、Bifu Adapter 和 CoinMaker 的职责混在一起。

## 1. 一句话理解

不同交易所像使用不同方言的人。CCXT 是翻译层：CoinMaker 用同一组方法说话，CCXT 或
CCXT_CM 里的 Adapter 再把它翻译成各交易所真正接受的 URL、参数、签名和数据格式。

例如 CoinMaker 统一调用：

- `fetch_ticker("BTC/USDT")`：读最新行情。
- `fetch_balance()`：读账户余额。
- `create_order(...)`：下单。
- `watch_orders()`：持续接收订单更新。

接 Binance 时由官方 CCXT 翻译；接官方尚未支持的 Bifu 时，由本项目的 Bifu Adapter 翻译。

## 2. CCXT 负责和不负责的事情

CCXT 负责：

- 统一方法名、常用参数、返回结构和异常类型。
- 把统一交易对 `BTC/USDT` 转成交易所需要的市场 ID。
- 处理签名、限流、精度以及 REST / WebSocket 连接细节。
- 用 `has` 声明一个实例支持哪些能力。

CCXT 不负责：

- 决定什么时候买卖、报什么价格、下多少数量。
- 管理 CoinMaker 的策略、库存、风控、调度和业务账本。
- 保证 `has=True` 的接口一定能在你的账户上成功；权限、市场状态和参数仍需在线验收。

## 3. Bifu 的普通接口和特殊接口

Vireo 已确认：Bifu **只有 `mock` 算特殊接口**。分类依据是 2026-09-17 18:40 的 Lark
聊天确认；mock 行为说明来自同日内部会议纪要 02:33 左右的讲解。公开仓库不保存内部链接、
截图或附件。具体请求路径、参数、鉴权、返回和错误语义仍须用 Bifu API 文档及测试环境脱敏
响应核对，在此之前标记为“待资料”。

- 正常下单用 `create_order`，撤单用 `cancel_order`；余额、订单查询、成交记录和 WebSocket
  更新也都走普通 CCXT 接口。
- `mock` 直接产生交易所内部数据，不生成真实订单，也不与盘口订单撮合，因此单独封装和验收。

这里不要把业务口语“交易”误写成一个叫 `trade` 的下单方法：

- `create_order`：创建订单，也就是正常下单。
- `fetch_trades`：查询市场公共成交记录。
- `fetch_my_trades`：查询当前账户自己的成交记录。
- `mock`：Bifu 专有的刷量接口，也是本项目唯一的特殊接口。

## 4. 第一次手动实验

先把仓库克隆到自己的电脑，进入仓库根目录，然后按锁文件安装依赖：

```bash
uv sync --frozen
```

先做完全离线的对象检查：

```bash
uv run python -m examples.inspect_exchange binance
```

重点看三件事：

1. `id` 是不是 `binance`。
2. 类来自官方 `ccxt` / `ccxt.pro`，说明 CCXT_CM 没有复制官方交易所实现。
3. `capabilities` 里的 `True` 只表示“声明支持”，不是在线调用已经成功。

再做公共只读行情实验，不需要 Key，也不会下单：

```bash
uv run python -m examples.public_market_data binance BTC/USDT --connect
```

这一步重点看统一结果中的 `symbol`、`last`、`bid`、`ask`、`timestamp` 和 `info`：前几个是
CCXT 统一字段，`info` 保留交易所原始响应，方便排查翻译是否正确。

截至 2026-09-17，这台电脑对 Binance 公共接口连接被拒绝，对 OKX 公共接口请求超时，所以
公共在线实验尚未通过。看到 `ExchangeNotAvailable` 或 `RequestTimeout` 时，应记录为网络/环境
待排查，不能据此判断 Adapter 正确，也不能把本课写成在线通过。离线对象实验不受影响。

如果要观察 WebSocket，再运行：

```bash
uv run python -m examples.public_market_data binance BTC/USDT --connect --watch
```

## 5. Jacky 的本课验收

完成后用自己的话回答：

1. CoinMaker 为什么不用分别学习 Binance、OKX 和 Bifu 的参数？
2. `has=True` 能证明什么，不能证明什么？
3. 统一字段和 `info` 原始字段分别用来做什么？
4. 为什么正常下单叫 `create_order`，而 `fetch_trades` 不是下单？
5. Bifu 为什么只有 `mock` 走特殊方法？

把实际命令、运行时间、结果和这五个答案记入能力矩阵。程序测试通过不等于 Jacky 已验收，
只有亲自运行并能解释，才把“人工验收”改为“通过”。
