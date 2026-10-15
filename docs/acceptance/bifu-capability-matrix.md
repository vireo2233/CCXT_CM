# Bifu 能力与验收矩阵

这是 Bifu 重写的唯一进度表。自 2026-09-18 起，项目只按下面三个部分执行和汇报；旧的单接口
阶段编号仅作为历史记录，不再作为后续项目阶段。每完成一个接口，仍要同时更新代码、测试、中文
解读和本表。昨天未进入 `Jacky` 分支的实现不算当前完成状态。

## 状态含义

- `未开始`：尚未写本轮失败测试。
- `红灯`：失败测试已证明缺少该能力。
- `离线通过`：协议转换和错误路径自动化测试通过，尚未连测试环境。
- `程序在线通过`：测试环境自动验收通过，尚未由 Jacky 亲自验收。
- `Jacky 已验收`：Jacky 亲自运行并能解释输入、输出和结果。
- `待资料`：缺少明确协议、URL、权限或测试数据，不能猜。
- `不支持`：有证据确认不支持，并在 `has` 中如实声明。

## 当前三部分计划

| 部分 | 范围 | 当前状态 | 完成后汇报内容 |
|---|---|---|---|
| 1 | 所有公共及私有 REST 接口，支持异步；Bifu 特殊刷量接口作为交易所私有专有方法单独处理 | 已完成；`d1cc299` 已推送到 `Jacky` | 方法清单、`has`、自动化结果、测试环境真实调用、CCXT 结构与异常映射、遗留问题 |
| 2 | 公共及私有 WebSocket | 已完成；以本记录所在 commit 为准 | `watch*` 清单、订阅与鉴权、真实更新、心跳/断线/重连、CCXT 结构、遗留问题 |
| 3 | 全功能测试、问题修复、全量回归、代码复审和正式交付 | 已完成；以本记录所在 commit 为准 | 全接口矩阵、修复记录、全库测试、复审与清理、构建产物、最终交付说明 |

测试环境和生产环境不是两个开发阶段；两者共用同一适配器，通过不同 URL、Key 和参数选择环境。
每个部分内部仍逐接口测试，重点确认真实数据、CCXT 标准格式和 CCXT 异常映射。每个部分只有在
形成领导汇报并完成确认后，才进入下一部分。

### 第三部分最终验收结果（2026-09-20）

第三部分已完成。完整交付证据见 [Bifu 最终交付说明](bifu-final-delivery.md)。本轮针对第一、二部分
全部能力执行全库回归，最终为 **391 passed**，核心包 statement/branch coverage **91.37%**；
144 条 warning 均来自 aiohttp 在 Python 3.14 下的依赖层弃用提示。Ruff 检查、格式检查、
`compileall`、diff 检查、wheel/sdist 构建和仓库外 wheel 导入均通过。

公网开发环境只读复验再次取得 24 个现货市场、行情/买一卖一/趋势数据及 6 类公共 WebSocket
真实更新；Sandbox 私有只读复验取得标准订单、个人成交、资金流水与余额结构。第二部分已完成的
私有 WebSocket 三类真实事件验收仍有效，本轮未为重复证明而再次触发写入。生产和测试继续共用
一个适配器，以 URL、Key 和 sandbox 参数选择环境；本轮未连接或写入生产账户。

代码复审发现并修复了写入验收工具的安全漂移：现在不论由工具创建还是由调用方传入 exchange，
都必须验证为 Bifu Sandbox；所有下单路径在写请求前生成 `clientOrderId`，未知结果可按本轮标识
对账和清理；失败清理会轮询确认本轮订单已离开当前挂单；`fetch_balance` 不再静默透传未经支持的
参数。重复的环境创建、标识生成和清理逻辑已集中到 `examples/_bifu_write.py`。引用扫描未发现
`src/` 或 `examples/` 中仅定义、无调用点的函数或类。

### 第二部分验收结果（2026-09-20）

| 范围 | 已完成能力 | 在线验收结论 |
|---|---|---|
| 公共 WebSocket | `watch_ticker`、`watch_tickers`、`watch_trades`、`watch_order_book`、`watch_bids_asks`、`watch_ohlcv` | 公网开发环境逐项收到真实推送，并转换为 CCXT Ticker、Trade、OrderBook、BidsAsks 和 OHLCV 结构 |
| 私有 WebSocket | `watch_orders`、`watch_my_trades`、`watch_balance` | 公网 Sandbox 用一次 `create_mock_order` 同时触发三类真实事件，三项均收到并通过标准字段校验；输出已脱敏 |
| 鉴权与安全 | `X-API-KEY`、`X-TS`、`X-SIGN` 请求头 | 私有 URL 不含凭证；每次重连重新签名；私有鉴权头不会带到公共连接 |
| 连续性 | 心跳 Pong、断线重连、REST 盘口快照与 WS 增量衔接、订单簿序号校验、局部订单合并 | 专项测试通过；先建立 WS 缓冲增量，再取 REST 快照作为基线；序号断档抛 `InvalidNonce` 并重新取快照，只清理断开的相关缓存，不影响其他连接 |

额外修复：全市场 ticker 流中出现市场元数据未知 instrument 时，仅跳过未知项，不让一条异常数据
拖垮整批行情；私有订单的局部更新会与已知订单合并，不会把事件未携带的字段覆盖为 `None`。

当前边界：Bifu WebSocket 文档只确认 `1m` K 线，因此不虚报其他周期；适配器只发布现货能力，
`margin_balance_update` 不伪装成现货余额。测试和生产仍共用同一适配器，但本轮没有连接或写入
生产环境。协议依据为 `https://flame-api.bifu.dev/docs/websocket`（最后修改 2026-08-27，
2026-09-20 复核）。订单簿快照修复后再次完成公网开发环境只读验收，6 个公共流全部收到真实
数据并通过标准结构检查；此前私有流验收结果保持有效，本轮没有重复下单。WebSocket 专项测试
**20 passed**；全仓库回归 **385 passed**，statement/branch coverage **91.37%**；144 条 warning
均来自 aiohttp 在 Python 3.14 下的依赖层弃用提示，不是适配器失败。

### 第一部分当前进度（2026-09-18）

| 接口 / 方法 | 自动化 | 测试环境在线 | 当前结论 |
|---|---|---|---|
| `load_markets` / `fetch_markets` | 通过 | 通过 | 24 个现货市场；Bifu `BTC-USDT` 映射为 CCXT `BTC/USDT` |
| `fetch_ticker` | 通过 | 通过 | 标准 Ticker；不伪造未提供的 bid/ask |
| `fetch_tickers` | 通过 | 通过 | FULL 形态按 symbol 筛选；不把 meta 外的 instrument 伪装成市场 |
| `fetch_bids_asks` | 通过 | 程序在线通过 | 原生 `bookTicker` 单标的查询；多标的逐一请求，故 `has="emulated"`；不以最新成交价伪造买一卖一 |
| `public_get_market_v1_trends` | 通过 | 程序在线通过 | CCXT 无批量缩略走势统一方法，保留为原生隐式公共接口；不伪造成 OHLCV，也不新增专有高层方法 |
| `fetch_order_book` | 通过 | 通过 | 实取买卖各 5 档；标准时间戳和 nonce；校验响应 instrument |
| `fetch_ohlcv` | 通过 | 通过 | 实取 2 行标准六字段 OHLCV；校验响应 instrument |
| `fetch_trades` | 通过 | 通过 | 实取 2 条标准 Trade；金额使用 CCXT 精确乘法；校验响应 instrument |
| `fetch_balance` | 通过 | 通过 | 真实测试 Key 鉴权成功；不记录精确余额；meta 未知资产以原始 ID 保留 |
| `fetch_open_orders` | 通过 | 通过 | 当前 `BTC/USDT` 挂单为 0；兼容测试环境用 `{}` 表示空结果 |
| `fetch_closed_orders` | 通过 | 通过 | 实取 1 条历史样本并标准化为 `canceled`；当前只返回一页 |
| `fetch_my_trades` | 通过 | 通过 | 实取 1 条个人成交，识别为 maker；标准 Trade 与手续费结构通过 |
| `fetch_order` | 通过 | 通过 | 新建测试订单处于挂单中时可查询为标准 `open`；订单结束后测试环境可能返回 `OrderNotFound`，此时不能伪造最终状态 |
| `create_order`（限价） | 通过 | 通过 | 标准参数、精度、5 USDT 精确边界、POST_ONLY、已知权限错误与畸形 ACK 已验；创建成功只表示受理，标准 Order 的状态保持 `None` |
| `create_order`（市价） | 通过 | Jacky 已验收 | 买入按计价币预算、卖出按基础币数量；仅 IOC；买卖真实测试成交均取得标准 Trade，且没有残留挂单 |
| `cancel_order` | 通过 | 通过 | 参数、订单不存在、未受理 ACK 已验；撤单成功只表示命令受理，随后用当前挂单确认订单已移除，不把受理回执伪装成 `canceled` |
| `cancel_all_orders` | 通过 | Jacky 已验收 | 必须传 symbol；真实创建 2 张 POST_ONLY 单后按交易对撤销，回执 `canceled=2`，最终挂单 0；不把汇总 ACK 伪造成逐单终态 |
| `create_orders` | 通过 | 程序在线通过 | 原生批量下单；同一 symbol、每批 1–100 笔；同序保留逐笔结果，映射 `PENDING` / `REJECTED`，未知状态保持未知，不掩盖部分失败 |
| `cancel_orders` | 通过 | 程序在线通过 | 原生批量撤单；必须传 symbol、每批 1–100 个订单号；汇总受理回执不伪造成逐单最终状态，须复查当前挂单 |
| `edit_order` | 通过 | 程序在线通过 | 原生单笔改单；订单 ID 不变；数量为新总量；受理回执不伪造最终状态，须查询确认修改生效 |
| `edit_orders` | 通过 | 程序在线通过 | 原生批量改单；同一 symbol、每批 1–100 笔；严格核对同序逐项结果并保留拒绝码 |
| `fetch_ledger` | 通过 | 程序在线通过 | 资金流水转换为 CCXT Ledger；金额使用绝对值并分离 `direction`；支持时间、币种和游标分页边界，验收输出脱敏 |
| `create_mock_order`（Bifu 专有） | 通过 | Jacky 已验收 | 独立专有方法固定发送 `SANDBOX_MARKET`；公网 Sandbox 账号真实完成 5.5 USDT 买入和 0.001 BTC 卖出，两笔均取得标准 Trade 且未进入当前挂单 |
| Bifu 业务错误映射 | 通过 | 已有在线样本 | 文档公开的 52 个业务码和在线发现的 `4006` 映射为 CCXT 异常；写入结果未知映射为 `RequestTimeout`，要求先查单再决定是否重试 |

统一入口、显式测试/生产配置和签名已完成。公共及私有异步 REST、普通订单、批量接口、资金流水、
异常矩阵和 Bifu 专有 `mock` 均已完成实现与在线验收，第一部分可以形成领导汇报。生产环境仍只做
配置边界验证，不使用生产凭证，不进行生产写入。

当前全库回归：**365 passed**；核心包 statement/branch coverage **91.77%**。127 条 warning 均来自
aiohttp 在 Python 3.14 下的依赖层弃用提示，不是适配器失败。

## 每轮验收记录模板

复制下面这段到本文件末尾，只保留**脱敏后的**原始结果。Key、Secret、签名、token、账户 ID、
订单 ID、clientOrderId、精确余额和其他账户/客户数据都必须替换为 `[REDACTED]`，不能进入仓库：

```text
日期时间（Asia/Shanghai）：
功能：
代码版本 / commit：
自动化命令与结果：
测试环境命令与结果：
Jacky 手动命令与结果：
我对这个功能的解释：
异常或限制：
复审与清理结果：
```

## 验收记录

> 下列“阶段 0/1/2/3a”是三部分计划确定前的历史验收快照，其中关于手工注册、禁止生产 URL、
> 能力仍为 False 等描述只反映当时版本，不能作为当前使用说明。当前行为以本文件顶部三部分计划、
> `docs/learning/01-bifu-environment.md` 和现行代码为准。

### 2026-09-17：阶段 0 仓库基线与中文学习入口

- 日期时间：2026-09-17 18:43–18:50 CST（Asia/Shanghai）。
- 代码版本：以本记录所在 commit 为准；Python 3.14.7、CCXT 4.5.78、uv 0.12.15。
- 锁定环境：`uv sync --frozen`，exit 0。
- 静态检查：`uv run ruff check .`，exit 0；`uv run ruff format --check .`，exit 0，
  31 files already formatted。
- 全量测试：`uv run pytest -q --cov=ccxt_cm --cov-report=term-missing`，exit 0；
  **69 passed**，核心包 statement/branch coverage **100%**，3 条为 aiohttp/CPython 依赖层
  `DeprecationWarning`。
- 构建：`uv build`，exit 0；生成 `ccxt_cm-0.1.0.tar.gz` 和
  `ccxt_cm-0.1.0-py3-none-any.whl`（构建产物由 `.gitignore` 排除）。
- 对象检查：官方 Binance 工厂对象为 `ccxt.pro.binance`，能力声明可读；这只证明工厂复用和
  能力声明，不证明在线接口可用。
- 公共在线检查：Binance 返回 `ExchangeNotAvailable`（连接被拒）；OKX 返回
  `RequestTimeout`。两项均未使用 Key、未下单，在线状态保持“未通过”。
- Jacky 手动验收：未开始；完成 `docs/learning/00-ccxt-core.md` 的五个问题后再更新。
- 双轴复审：规范审查与需求审查指出可复现命令、证据脱敏、协议来源和重复阶段编号问题，均已
  修正。变更只有文档与索引，没有新增运行时代码或调用点；敏感值扫描无命中。

### 2026-09-17：阶段 1 创建 Bifu 实例与环境隔离

- 日期时间：2026-09-17 22:34 CST（Asia/Shanghai）。
- 候选版本：working tree，基于 `origin/Jacky@c8f870b`；适配器 commit 在 Jacky 手动验收并同步
  分支后补记。Python 3.14.7、CCXT 4.5.78。
- 功能：显式注册 Bifu async REST 类；使用 CCXT `set_sandbox_mode(True)` 选择测试环境；生产
  URL 默认关闭且当前禁止注入；测试实例与关闭的生产占位实例不共享凭据和 URL。
- 自动化：`pytest -q tests/test_bifu_environment.py`，**9 passed**。
- 全库复测：`pytest -q --cov=ccxt_cm --cov-report=term-missing`，**78 passed**，核心包
  statement/branch coverage **100%**；3 条仍为 aiohttp/CPython 依赖层 `DeprecationWarning`。
- 静态与构建：ruff 检查、36 文件格式检查通过；wheel/sdist 构建通过，wheel 已核对包含
  `ccxt_cm/exchanges/bifu.py`。
- 安全保护：测试环境全部完成前，任何非空生产 URL 都直接拒绝。
- 测试环境：公开 `/market/v1/meta` 探活返回 HTTP 200；响应包含 `assets`、`spots`、`globals`，
  当时为 69 个资产、24 个现货标的。命令：
  `curl --max-time 30 https://flame-api.bifu.dev/market/v1/meta`。本次没有使用 Key，没有发出
  交易请求。
- 协议资料：公开文档未标版本号；私有接口为 API Key + 毫秒时间戳 + HMAC-SHA256 签名；具体
  请求频率限制待资料。本阶段不实现鉴权。
- 能力边界：本阶段所有 Bifu 业务 `has` 仍为 `False`；探活成功不表示 `fetch_markets` 已实现。
- Jacky 手动验收：2026-09-18 已完成。Jacky 亲自运行测试/生产两个离线检查命令，结果分别为
  `sandbox=true, configured=true` 和 `sandbox=false, configured=false`；能说明测试与生产资金及
  账户不能混用、测试开关要在创建交易所实例后且调用任何接口前打开，以及生产地址不配置可防止
  误连真实账户。

### 2026-09-18：阶段 2 `load_markets` / markets

- 代码版本：以本记录所在 commit 为准；基于远端
  `Jacky@b7ffa9b0c253a31c84dd706e7db4cc8395427334` 开发并完成 Jacky 人工验收。Python
  3.14.7、CCXT 4.5.78、uv 0.12.15。
- 功能：调用测试环境公开 `/market/v1/meta`，将 Bifu 现货标的转换为 CCXT 标准市场结构；开启
  `fetchMarkets`、`publicAPI` 和 `spot` 能力声明。
- 自动化：`pytest -q tests/test_bifu_markets.py`，**13 passed**。覆盖正常标准化、固定查询编码、
  未支持参数、生产关闭、暂停/未知状态、畸形元数据，以及只读验收工具首次超时后重试一次；
  网络测试只连接本机回环服务。
- 全库复测：2026-09-18 13:57 CST，`pytest -q --cov=ccxt_cm --cov-report=term-missing`，
  **91 passed**，核心包 statement/branch coverage **100%**；12 条仍为 aiohttp/CPython 依赖层
  `DeprecationWarning`。
- 静态与发布物：`ruff check .`、39 文件格式检查通过；wheel/sdist 构建通过，wheel 内的
  `ccxt_cm/exchanges/bifu.py` 与工作区一致。
- 测试环境：公开只读在线验收通过，共返回 **24** 个现货市场；`BTC-USDT` 转为 `BTC/USDT`，
  `market.id` 使用原始 `instrument_id=90000001`；数量步长 `0.00001`、价格步长 `0.01`、最小
  数量 `0.00001`、最小名义金额 `5 USDT`。
- 精度示例：数量 `1.23456789` 转为 `1.23456`，价格 `100.129` 转为 `100.13`。
- 稳定性：人工验收时曾出现 `GET /market/v1/meta` 超时；同一链路随后直接请求及程序复测均成功，
  判断为测试环境或 Cloudflare 链路的短暂波动。只读人工验收工具使用 30 秒超时，首次超时会明确
  提示并只重试一次；适配器本身仍尊重调用方传入的 CCXT `timeout`，不会自动重试，避免未来写
  接口出现重复下单。
- 安全边界：不读取 Key、不访问账户、不下单；生产 URL 仍关闭，其他业务 `has` 仍为 `False`；
  原始 `margin` 对象不足以证明保证金交易可用，市场 `margin` 保守保持 `False`。
- Jacky 手动验收：2026-09-18 14:06 CST 已完成。Jacky 亲自运行只读命令，得到测试环境、24 个
  现货市场及 `BTC/USDT` 的标准化结果；能说明 `BTC-USDT` 是 Bifu 原生格式、`BTC/USDT` 是
  CCXT 统一格式且二者不能在程序中混用，订单名义金额低于 `5 USDT` 不能下单，并确认 markets
  验收不代表余额或下单功能已经完成。

### 2026-09-18：阶段 3a `fetch_ticker`

- 候选版本：未提交工作区，基于远端
  `Jacky@321cf49ef17557098fde99d0f172421f8bc39948`。Python 3.14.7、CCXT 4.5.78、uv
  0.12.15。
- 协议依据：Bifu 公开文档的 `GET /market/v1/ticker`。请求参数为 `instrument_id`；成功响应是指定
  标的的 24h 滚动成交统计，字段包括最新、开盘、最高、最低价格，基础币成交量、计价币成交额、
  价格变化、百分比和交易所时间戳。接口不需要鉴权。
- 功能：`fetch_ticker("BTC/USDT")` 先通过 markets 找到原始 ID，再请求 Bifu 并返回 CCXT 标准
  Ticker；开启 `fetchTicker` 能力声明。响应标的 ID 不一致、时间戳非法或响应不是 JSON 对象时
  明确抛 `BadResponse`；业务码 `6005`（标的存在但无成交）映射为 `BadResponse`，不会误报整个
  交易所不可用；不接受未经确认的额外 `params`。
- 字段边界：`last/open/high/low/baseVolume/quoteVolume/change/percentage/timestamp` 来自 Bifu
  明确字段；`bid/ask` 保持 `None`，因为买一卖一属于独立 `bookTicker` 接口，不能用最新成交价
  伪造。
- 自动化：`pytest -q tests/test_bifu_ticker.py`，**9 passed**。覆盖标准映射、固定标的 ID、未知
  参数、错配标的、非法时间戳、非对象响应、空涨跌幅、业务码 `6005`、验收工具输出和只读工具
  首次超时后重试一次。
- 全库复测：2026-09-18 15:27 CST，`pytest -q --cov=ccxt_cm --cov-report=term-missing`，
  **100 passed**，核心包 statement/branch coverage **100%**；20 条仍为 aiohttp/CPython
  依赖层 `DeprecationWarning`。
- 静态与发布物：`ruff check .`、43 文件格式检查通过；wheel/sdist 构建通过。隔离构建首次因
  执行环境无法解析 PyPI 失败，安装 `pyproject.toml` 已声明的 Hatchling 后使用现有环境完成构建；
  代码与依赖约束未改变。
- 测试环境：2026-09-18 15:07 CST 公开只读在线验收通过。BTC/USDT 返回原始
  `instrument_id=90000001`、交易所毫秒时间戳、24h 价格和成交量；本次快照中 `last=538944.47`、
  `baseVolume=0.44998`、`quoteVolume=242966.07064`。行情会变化，这些数字只作为当次链路证据。
- 安全边界：没有使用 Key，没有访问账户，没有下单或撤单；生产 URL 仍关闭。人工验收工具首次
  超时只重试一次，核心适配器不自动重试。
- Jacky 手动验收：未开始。运行 `uv run python -m examples.inspect_bifu_ticker BTC/USDT`，完成
  `docs/learning/03-bifu-fetch-ticker.md` 的五个问题后再更新。
- 复审与清理：第一轮规范审查通过；需求审查发现业务码 `6005` 被 CCXT 默认误映射为
  `ExchangeNotAvailable`，已增加明确映射和回归测试。最终双轴复审待复测后补记。

### 2026-09-19：第一部分中间验收——私有只读订单查询

- 功能：`fetch_open_orders`、`fetch_closed_orders`、`fetch_order` 和 `fetch_my_trades`；本轮只读，
  没有创建、修改或撤销订单。
- 自动化：全库 **128 passed**，核心包 statement/branch coverage **94.61%**，exit 0；44 条
  warning 均来自 aiohttp 在 Python 3.14 下的依赖层弃用提示。Ruff 检查、格式检查和 diff 检查
  通过。
- 测试环境：当前挂单 0 条；取得 1 条 `canceled` 历史订单和 1 条 maker 成交；订单与成交的
  CCXT 标准字段检查通过。现有历史样本 ID 经单笔查询返回 `OrderNotFound`，因此
  `fetch_order` 在线状态仍为部分通过，待创建新测试订单后复核，未猜测服务端原因。
- 协议边界：未知订单状态保持 `None`；按计价币金额买入的市价单不把 `orig_qty=0` 误报为
  `amount=0`；已知累计手续费转换为 CCXT 标准 `fee`；历史订单和个人成交当前只读取一页。
- 安全：测试样本、验收工具和仓库敏感信息扫描无真实 Key、Secret、账户 ID、订单 ID 或成交 ID；
  验收工具输出同样不显示这些字段。
- 复审：规范和需求双轴复审均通过，无 blocker；复审发现的未知状态排序和教学标题问题已修复并
  补回归测试。
- Jacky 手动验收：2026-09-19 已完成。Jacky 在 `Jacky` 分支工作区亲自执行
  `python -m examples.inspect_bifu_orders BTC/USDT` 并得到上述脱敏结果；已确认理解公共成交与账户
  成交、空挂单、历史与单笔查询、单页历史以及按计价币金额买入市价单这五个边界。

### 2026-09-19：第一部分中间验收——限价下单与单笔撤单

- 功能：CCXT 标准 `create_order` 限价单和 `cancel_order` 单笔撤单；Bifu 专有 `mock` 不混入该方法。
- 协议：POST JSON 的 `instrument_id` 必须是整数；`client_order_id` 必填且由适配器在调用方未提供时
  生成；支持 GTC/IOC/FOK/PO 与 `postOnly`。数量和价格先按市场精度处理，并在请求前校验数量和
  名义金额上下限。
- 自动化：本机契约测试覆盖请求体、签名、精度、POST_ONLY、timeInForce、5 USDT 精确边界、
  不支持参数、已知业务错误、畸形/未受理 ACK 和显式写入确认词；写请求不会自动重试。全库最终
  回归 **152 passed**，覆盖率 **92.14%**。另有写超时测试确认下单 POST 只发送一次、不自动重试；
  公共市场元数据预检遇到瞬时网络错误最多尝试 3 次（失败后重试 2 次），且仅在预检成功后进入
  写请求。完整错误码矩阵仍随第一部分其余 REST 接口继续补齐。
- 测试环境：使用 BTC/USDT、`POST_ONLY`、名义金额 5.6 USDT 的远离盘口限价买单。创建回执通过
  标准字段检查，创建后单笔查询为 `open`，撤单命令返回受理，之后当前挂单中已不存在该订单。
- 服务端边界：撤单后的单笔查询返回 `OrderNotFound`，与旧历史样本现象一致。适配器没有伪造
  `canceled`；验收工具改用“单笔查询结果 + 是否仍在当前挂单”双重说明。
- 安全：只连接测试环境；输出不显示 Key、Secret、账户 ID、订单 ID 或 clientOrderId；没有生产
  请求；最终当前挂单数量为 0。
- Jacky 手动验收：2026-09-19 已完成。Jacky 在 `Jacky` 分支工作区亲自执行验收工具；结果确认
  创建 ACK 标准字段齐全、创建后查询状态为 `open`、撤单命令已受理、撤单后单笔查询不再返回该
  订单且当前挂单中不存在该订单，输出中的订单标识已脱敏。五个概念问题在本轮教学中继续确认。

### 2026-09-19：第一部分中间验收——市价买单与市价卖单

- 功能：CCXT 标准 `create_order` 支持 Bifu 市价买卖；另开启官方便捷方法
  `create_market_buy_order_with_cost`。Bifu 专有 `mock` 不混入该方法。
- 协议：市价单固定使用 IOC。市价买入向 Bifu 发送计价币预算 `quote_qty`；市价卖出发送基础币
  数量 `qty`。买入既支持 CCXT 常见的 `amount × price`，也支持直接给出计价币 `cost`；低于
  市场最小名义金额、`postOnly`、非 IOC、错误参数都在真正 POST 前拒绝。
- 自动化：市价买卖请求体、精度、标准预算便捷方法、ACK 标准化、错误参数、5 USDT 最小金额、
  明确写入确认词、只读预检重试和下单不重试均有专项测试。全库 **174 passed**，核心包覆盖率
  **92.60%**；Ruff 检查和格式检查通过。
- 测试环境：真实执行 5.5 USDT 的 BTC/USDT 市价买入和 0.00001 BTC 的市价卖出。两笔创建
  ACK 均具备标准字段；终态订单很快不再能被单笔接口查询，但各取得 1 条标准账户成交，且当前
  挂单中均无残留。验收没有把 `OrderNotFound` 单独当作成交证明。
- 安全：只连接测试环境并使用假币；两次创建各只发送一次；输出不显示 Key、Secret、账户、订单、
  clientOrderId 或成交 ID；没有生产请求。
- Jacky 手动验收：2026-09-19 已完成。Jacky 亲自运行 5.5 USDT 的 BTC/USDT 测试市价买入，
  得到 1 条标准成交且当前挂单无残留；能够说明市价买入值是计价币预算、市价卖出值是基础币数量，
  市价 IOC 通常会立即完成而不能依赖创建后手动撤单，创建 ACK 的 `status=None` 只表示最终状态
  未知，以及必须用 `trade_count=1` 和 `order_absent_from_open_orders=true` 一起证明成交且无残留。

### 2026-09-19：第一部分中间验收——按交易对撤销全部挂单

- 版本与范围：工作树基于 `Jacky@37057bc`，Python 3.14.7、CCXT 4.5.78；新增 CCXT 标准
  `cancel_all_orders(symbol)`，只连接 Bifu 测试环境的现货 `BTC/USDT`。
- 协议与返回：适配器调用 `POST /spot/v1/openOrders/cancel` 并发送整数 `instrument_id`。Bifu
  只返回已受理数量 `canceled`，所以标准 Order 列表中的 `status` 保持 `None`，不伪造每张订单
  已经最终 `canceled`。当前强制要求 symbol，不开放 `instrument_id=0` 的全现货域撤单。
- 自动化：成功请求体与签名、缺 symbol、未知参数、非法 ACK、权限错误、超时只发送一次 POST、
  精确确认词、中途创建失败清理、清理失败残留、并发外来挂单、ACK 数量错配和撤后残留均有测试。
  全库最终 **191 passed**，核心包覆盖率 **92.35%**，68 条 warning 均为 aiohttp/CPython 依赖层
  弃用提示；Ruff、格式检查、wheel/sdist 构建和 diff 检查通过。
- 测试环境证据：调用前当前挂单 0；创建两张 0.0001 BTC、60000 USDT、POST_ONLY 买单，每张
  名义金额 6 USDT；撤销前两张都在当前挂单中；汇总 ACK 为 `canceled=2`；撤销后两张均消失且
  `remaining_open_order_count=0`。本轮在线验收在安全修复后重新执行通过。
- 安全与局限：撤前当前挂单必须恰好等于本轮两张自有单；发现已有或并发出现的其他挂单立即停止，
  仅按订单号清理自有测试单。输出不显示 Key、Secret、账户 ID、订单 ID 或 clientOrderId；未连接
  生产环境。汇总 ACK 不能证明逐单最终状态，最终结果仍以当前挂单复查为准。
- 复审：首轮规范/需求复审发现失败清理吞异常、并发挂单误撤、残留非零仍成功、ACK 数量未核对
  及清理抛错时会话未关闭；均已修复并补回归测试。最终规范与需求双轴复审均通过，无剩余问题。
- Jacky 手动验收：2026-09-19 已完成。Jacky 亲自运行验收工具，结果为初始挂单 0、创建两张、
  撤销前两张均为当前挂单、汇总 ACK `canceled=2`、撤销后两张均消失且剩余挂单 0；输出中的订单
  标识已脱敏。教学改为一次性提供问题与标准答案，不再逐题等待回复。

### 2026-09-19：第一部分中间验收——批量下单与批量撤单（程序在线通过）

- 功能：新增 CCXT 标准 `create_orders` 与 `cancel_orders`，分别使用 Bifu 原生
  `POST /spot/v1/orders` 和 `POST /spot/v1/orders/cancel`，不是循环调用单笔接口，也不属于特殊
  `mock`。
- 协议：两种接口都限制 1–100 笔并要求同一 symbol。批量下单严格保持请求与回执同序，逐项保留
  `PENDING` / `REJECTED` 及拒绝码；批量撤单只返回整批受理与数量，所以标准 Order 的最终
  `status` 保持 `None`，不编造逐单已取消。
- 自动化：真实签名请求体、同序结果、部分拒绝、未知结果、空批/超限、ACK 数量错配、业务错误、
  非法批量撤单回执、固定写入确认词、未知创建结果清理、批量撤单失败降级清理和清理失败残留均
  已有测试。批量相关 87 项测试通过；全库 **223 passed**，核心包覆盖率 **92.08%**。85 条 warning
  均来自 aiohttp 在 Python 3.14 下的依赖层弃用提示，不是适配器失败。
- 在线状态：2026-09-19 首次验收在 `GET /market/v1/meta` 阶段超时，且发生在下单前；测试网关
  恢复后先完成只读复查，确认当前挂单为 0，再执行完整批量写验收。一次原生请求创建两张
  POST_ONLY 买单，两张都出现在当前挂单；一次原生批量撤销收到两项回执；最终两张均从当前挂单
  消失，剩余挂单为 0，命令退出码为 0。没有连接或写入生产环境。
- 安全：工具仅连接测试环境，写入前要求当前挂单为 0；两张订单使用显式 `clientOrderId` 和
  POST_ONLY，写请求不盲目重试；失败时只按本轮 ID 清理并复查。输出不显示任何凭据或订单标识。
- 复审：需求复审发现批量业务错误测试不足，规范复审发现逐单 `params` 可能被静默丢弃、未知
  结果和“服务端已创建但客户端超时”清理测试不足；均已修复并回归。最终需求与规范双轴复审
  均为 PASS，没有剩余可执行问题。

### 2026-09-20：第一部分中间验收——单笔改单与批量改单（程序在线通过）

- 功能：新增 CCXT 标准 `edit_order` 与 `edit_orders`，分别调用 Bifu 原生
  `POST /spot/v1/order/amend` 和 `POST /spot/v1/orders/amend`；没有使用“撤单后重下”的模拟逻辑，
  也不属于特殊 `mock`。
- 协议：单改保持原订单 ID；`amount` 是改单后的新总数量。批改限制同一 symbol、每批 1–100 笔，
  严格核对 ACK 数量、顺序和订单 ID，并逐项保留 `accepted` 与 `reject_code`。单改和批改 ACK 都
  只代表命令受理，标准 Order 的最终 `status` 保持 `None`。
- 自动化：覆盖原生请求体与签名、精度、数量/已知价格/可计算名义金额上下限、触发单字段与清零
  值、市价单拒绝、混合 symbol、批量边界、ACK 错配、逐项拒绝/未知结果、业务错误映射和写超时
  不重试；安全验收工具另
  覆盖固定确认词、穿价保护、单改失败清理和批改结果未知时不重试。全库 **256 passed**，核心包
  覆盖率 **91.65%**；Ruff、格式检查及 wheel/sdist 构建通过。
- 测试环境：先确认当前挂单为 0，再创建两张 0.00008 BTC、70000 USDT 的 POST_ONLY 买单。单改
  第一张至 69000 后查询确认；随后一次批改将两张分别改至 68000 和 67000，两项 ACK 均受理，
  当前挂单中两张新价格均可见。最后批量撤销并复查，`remaining_open_order_count=0`。
- 安全与局限：所有买价在运行时均低于最新成交价，且每次修改后的名义金额仍高于 5 USDT；写
  请求不自动重试，失败时只按本轮订单号/clientOrderId 清理。输出未包含 Key、Secret、账户、
  订单或 clientOrderId；未连接生产环境。触发单字段完成协议测试，但本轮没有创建真实触发单。
- 复审：需求轴通过；规范轴发现拒绝/未知 ACK 回显未确认值、价格与可计算名义金额边界不足、
  公共 `params` 测试缺失及安全清理重复。四项均已修复并补测试，复审修复后重新执行全量回归、
  构建和真实测试环境验收，结果仍通过且最终挂单为 0。
- Jacky 手动验收：未开始。按 `docs/learning/09-bifu-amend-orders.md` 执行同一验收命令并理解文末
  问题与标准答案后，再将本项更新为“Jacky 已验收”。

### 2026-09-20：第一部分中间验收——资金流水（程序在线通过）

- 功能：新增 CCXT 标准 `fetch_ledger(code, since, limit, params)`，调用 Bifu 私有只读
  `GET /spot/v1/fundFlows`。余额快照继续由 `fetch_balance` 负责，资金变化原因由本方法返回。
- 结构：Bifu 有符号 `amount` 转为 CCXT 绝对金额，并把正负方向分别映射成 `in` / `out`；
  `kind` 转为标准或保真流水类型，资产 ID 转为统一币种代码。缺少依据的余额前后值、费用和关联
  事件 ID、状态保持 `None`；原始记录完整保留在 `info`。
- 查询边界：支持 `since`、1–1000 的 `limit`、标准 `until`、Bifu `end_ts_ms` 以及 `cursor`。
  Bifu 端没有币种过滤参数，因此 `code` 在当前页解析后筛选；下一页游标可从
  `exchange.last_json_response["next_cursor"]` 读取，单次返回不能称为全部历史。
- 自动化与构建：资金流水专项 15 项、脱敏验收工具 2 项通过；全库 **273 passed**，核心包覆盖率
  **91.44%**，113 条 warning 均来自 aiohttp 在 Python 3.14 下的依赖层弃用提示。Ruff、格式
  检查及 wheel/sdist 构建通过。
- 测试环境：真实只读验收取得 2 条 USDT 样本，方向集合为 `in`、类型集合为 `deposit`；CCXT
  Ledger 标准字段检查通过，当前无下一页。工具未打印精确金额、时间、ticket、账户 ID 或凭据。
- 安全：本轮没有划转、充值、提现、下单或生产请求。`sample_count=2` 只证明当前真实样本及转换
  正常，不代表所有 Bifu 流水类型都已有线上样本。
- 复审：需求轴通过；规范轴发现原始响应没有状态却填入 `status="ok"`，已改为 `None` 并同步
  测试与文档。修复后重新执行全量回归、构建和真实测试环境验收，最终双轴复审均为 PASS。
- Jacky 手动验收：未开始。按 `docs/learning/10-bifu-fund-flows.md` 执行只读命令并理解文末问题与
  标准答案后，再更新为“Jacky 已验收”。

### 2026-09-20：第一部分中间验收——最优买卖盘口（程序在线通过）

- 功能：把 Bifu 公开 `GET /market/v1/bookTicker` 接入 CCXT 标准 `fetch_bids_asks`，返回买一价、
  买一数量、卖一价、卖一数量和盘口时间；24 小时成交统计继续由 `fetch_ticker` 单独返回，不用
  最新成交价伪造盘口。
- 协议边界：Bifu 原生接口一次只接受一个 `instrument_id`，所以多交易对查询由适配器逐个请求，
  `has["fetchBidsAsks"]` 如实声明为 `"emulated"`。这仍是 CCXT 统一方法，不是 Bifu 专有
  `mock`。响应不是对象、交易对 ID 不匹配或盘口时间非法时映射为 `BadResponse`；未确认的额外
  参数在联网前拒绝。
- 自动化：`pytest -q tests/test_bifu_ticker.py`，**18 passed**；覆盖标准字段、单标的与全部市场、
  响应结构、交易对 ID、时间戳、未知参数和只读验收工具。Ruff 检查和格式检查通过。
- 测试环境：`python -m examples.inspect_bifu_ticker BTC/USDT` 在线只读通过；当次公开快照返回
  `bid=81562.0`、`bidVolume=0.09683`、`ask=81562.02`、`askVolume=0.0001`，并返回独立盘口
  时间戳。行情会变化，这些值只作为当次真实链路证据。
- 安全：公共行情不使用 Key，不访问账户，不创建或撤销订单，也未连接生产环境。
- Jacky 手动验收：待 Jacky 亲自运行并确认能区分 `last`、`bid/ask` 和完整多档订单簿。
- 复审与回归：需求轴、规范轴最终均为 PASS。规范轴要求补强多市场证据，已把 BTC/USDT 与
  ETH/USDT 同时放入测试并断言两次独立请求及逐市场结果；协议文档也已补齐 `bookTicker`。
  修正后全库 **279 passed**、覆盖率 **91.63%**；118 条 warning 均来自 aiohttp/CPython 3.14
  依赖层。wheel/sdist 构建通过，wheel 已确认包含 `ccxt_cm/exchanges/bifu.py`。

### 2026-09-20：第一部分中间验收——24 小时缩略走势（程序在线通过）

- 功能：注册 Bifu 公开 `GET /market/v1/trends` 为 CCXT 原生隐式接口
  `public_get_market_v1_trends`。它用于批量读取固定一小时粒度、最多 24 个收盘价点。
- CCXT 边界：CCXT 没有对应的批量缩略走势统一方法，而且原始响应缺少 open/high/low/volume，
  因此没有新增 `fetch_trends`，也没有伪造成标准 OHLCV；标准 K 线继续使用 `fetch_ohlcv`。该原生
  公共地址可通过同一个 `create_exchange("bifu")` 实例调用，不属于专有高层 `mock`。
- 自动化：本轮新增路由、参数原样传递、无伪造高层方法、单市场结构、交易对 ID、时间、最多
  24 点、有限数值及字符串/布尔/无限值拒绝测试。公开行情专项最终 **26 passed**；全库最终
  **287 passed**、覆盖率 **91.64%**，119 条 warning 均来自 aiohttp/CPython 3.14 依赖层。
- 测试环境：同一只读验收工具通过适配器真实取得 BTC/USDT 的 24 个趋势点；`open_time` 有效，
  首尾收盘价可解析。公开行情会变化，本记录不把具体价格当固定预期。
- 安全：本轮不使用 Key，不访问账户，不创建、修改或撤销订单，也未连接生产环境。
- 复审：需求轴、规范轴最终均为 PASS。规范轴发现列表类型、布尔值、无限时间和文档命名边界，
  已全部补测试并修正；修正后的全库回归、在线验收、静态检查和 wheel/sdist 构建均通过。
- Jacky 手动验收：未开始。运行 `python -m examples.inspect_bifu_ticker BTC/USDT`，结合
  `docs/learning/03-bifu-fetch-ticker.md` 中已经附答案的说明理解输出即可，不再单独提问。

### 2026-09-20：第一部分中间验收——完整业务错误映射

- 功能：根据 Bifu 开发环境接口文档（`https://flame-api.bifu.dev/docs`，2026-09-20 访问），把
  52 个已公开业务码逐项映射为可由 CoinMaker 统一处理的
  CCXT 异常；未知的新业务码不猜含义，继续交给 CCXT 的 HTTP 错误处理。
- 协议注意：文档的“有译文的 code”表列出 `1025 PRICE_BAND`，后面的“完整业务码列表”却漏了
  这一项。适配器保守按已公开有效码映射为 `InvalidOrder`，同时记录这项上游文档不一致。
- 关键边界：`6003 OUTCOME_UNKNOWN` 映射为 `RequestTimeout`，表示写请求可能已经到达服务端，
  调用方必须先用订单号或客户端订单号查单、对账，再决定是否重试；不能把它当作确定失败并自动
  再下单。`3001 NOT_ORDER_OWNER` 与不存在订单统一映射为 `OrderNotFound`，避免泄露其他账户订单。
- 自动化：错误专项原始基线 **55 passed**，覆盖 52 个文档码、未知码内部边界、未知码真实 HTTP 链路和
  `3000/3001` 不可区分提示；同时保留 `6005` 无成交市场的既有错误提示兼容。最终全库
  **342 passed**、覆盖率 **91.65%**。120 条 warning 均来自 aiohttp 在 Python 3.14 下的依赖层
  弃用提示，不是适配器失败。
- 安全：本轮只验证错误转换，不连接生产环境、不使用真实凭据，也不进行任何下单、撤单、划转、
  充值或提现。

### 2026-09-20：第一部分中间验收——Bifu 专有 `mock`

- 协议：官方现货接口确认 `mock` 与普通现货下单共用 `POST /spot/v1/order`，通过
  `type=SANDBOX_MARKET` 区分；固定 IOC，买入使用 `quote_qty`，卖出使用 `qty`。普通账户会被
  拒绝，Sandbox 账户也只能使用这一订单类型。
- 封装边界：新增独立私有写入专有方法 `create_mock_order`，并通过 `special_methods(exchange)`
  可发现；CCXT 标准 `create_order` 继续只接受 `limit` 和 `market`，明确拒绝 `mock` 与
  `SANDBOX_MARKET`，避免模拟刷量与真实交易混用。
- 返回：专有方法返回 CCXT Order 外壳，原始 Bifu ACK 保留在 `info`；ACK 没有最终状态时
  `status=None`，不伪造成已成交。在线验收还必须取得本次标准 Trade，并确认当前挂单无此订单。
- 自动化：`mock` 适配器与验收工具专项 **22 passed**，覆盖专有方法元数据、测试模式保护、买入/卖出
  单位、精度、价格保护、最小金额、非法参数、缺失订单号、ACK 非最终状态、标准 `create_order`
  隔离、显式写入确认词、传输超时仅一次 POST、未知结果不重试、异常清理、资源关闭，以及“有本次
  成交且无当前挂单”的闭环判定。全库 **365 passed**、覆盖率 **91.77%**；127 条 warning
  均来自 aiohttp 在 Python 3.14 下的依赖层弃用提示。
- 在线结果：现有 Jacky 测试 Key 的签名请求到达正确接口，但服务端返回 HTTP 403、业务码
  `4006 SANDBOX_MARKET requires Sandbox account`；请求没有生成模拟订单或成交。该码已映射为
  `AccountNotEnabled`。平台随后提供了独立的开发环境 MockTrade 账户和测试 BTC/USDT 资产。
- 新账号复查：平台最初提供的内网 Key 在公网开发 URL 的私有请求上返回 HTTP 401、
  `4000 UNAUTHENTICATED`。重新生成非内网公网 Key 后，同一适配器通过
  `https://flame-api.bifu.dev` 完成私有只读查询，证明 URL、签名和公网 Key 均有效；但
  `SANDBOX_MARKET` 写入返回 HTTP 403、`4006 SANDBOX_MARKET requires Sandbox account`。
  写入失败后的只读复核显示当前挂单、历史订单和成交均为 0，证明前两套账号均未产生副作用。
- 在线闭环：平台创建公网 Sandbox 账号后，私有只读认证与模拟 BTC/USDT 资产读取成功；随后真实
  完成 5.5 USDT 的 `SANDBOX_MARKET` 买入和 0.001 BTC 的 `SANDBOX_MARKET` 卖出。两笔创建回执均
  包含 CCXT Order 标准字段并保持 `status=None`，各查询到 1 条对应的 CCXT 标准 Trade，且当前挂单
  中均不存在对应订单。输出未包含账户、订单、clientOrderId 或成交标识。
- 安全：验收工具为每轮写入生成唯一 `client_order_id`，结果未知时绝不自动重试，只查询并清理
  本轮可识别的当前挂单；最终在线验收在每次写入后核对私有成交和当前挂单。所有输出均隐去 Key、
  账户、订单、clientOrderId 和成交 ID，未连接生产环境。
- 复审：仓库标准与需求符合性两条独立复审均为 **PASS**，未发现剩余的规范、安全、重复代码、
  死代码或需求偏差问题。
