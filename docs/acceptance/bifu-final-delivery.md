# Bifu 最终交付说明

日期：2026-09-20（Asia/Shanghai）

分支：`Jacky`

版本：以包含本文件的 commit 为准

## 交付范围

本项目使用同一个 Bifu 适配器连接测试或生产环境，调用方通过 URL、Key 和 sandbox 参数选择环境，
不维护两套实现。本次完成以下范围：

- 公共异步 REST：市场元数据、Ticker、批量 Ticker、买一卖一、盘口、K 线、公共成交和 Bifu 原生趋势。
- 私有异步 REST：余额、资金流水、当前/历史/单笔订单、个人成交、单笔/批量下单、撤单和改单。
- Bifu 专有私有方法：`create_mock_order`，固定使用 `SANDBOX_MARKET`，不冒充 CCXT 标准 `createOrder`。
- 公共 WebSocket：Ticker、批量 Ticker、成交、盘口、买一卖一和 1 分钟 K 线。
- 私有 WebSocket：订单、个人成交和现货余额。
- 所有统一方法返回 CCXT 标准结构；HTTP、业务码、鉴权、权限、订单不存在、限流、超时和协议错误映射为 CCXT 异常。

能力的逐项状态、限制和历史验收证据以
[Bifu 能力与验收矩阵](bifu-capability-matrix.md)为准。

## 第三部分修复

最终复审发现写入验收工具存在安全规则分散的问题，已完成以下修复：

1. 所有写入验收统一调用 `prepare_sandbox_exchange`；即使调用方传入现成实例，也必须同时满足
   `exchange.id == "bifu"` 和 `isSandboxModeEnabled is True`，否则在任何网络请求前拒绝。
2. 所有下单路径在写请求前生成 `clientOrderId`。即使创建请求超时、客户端未拿到订单 ID，也可按
   本轮 client ID 查询和清理，不会盲目重试下单。
3. 失败清理只处理本轮订单，并轮询当前挂单确认已清除；不会撤销其他调用方的订单。
4. `fetch_balance` 明确拒绝 Bifu 未支持的额外参数，不再静默透传。
5. 环境创建、sandbox 校验、client ID 生成和失败清理集中到 `examples/_bifu_write.py`，删除重复实现。
6. 写入验收除检查 sandbox 标志外，还逐项核对当前 REST/WS 地址与适配器声明的测试地址；即使
   sandbox 标志被误设为真，只要仍指向生产地址，就会在任何网络请求前拒绝执行。

## 自动化与代码质量

- 全库：`391 passed`。
- 核心包 statement/branch coverage：`91.37%`，高于 90% 门槛。
- Warning：144 条，均为 aiohttp 在 Python 3.14.7 下的依赖层弃用提示，不是适配器失败。
- `ruff check .`、`ruff format --check .`、`compileall` 和 `git diff --check`：通过。
- 内部辅助方法及 `src/`、`examples/` 定义引用扫描：未发现无调用点代码。
- 规范轴复审最初发现 3 类问题，均已修复；需求轴最初只指出第三部分尚未形成交付记录，本文件补齐后再次复审。

## 测试环境在线复验

本轮只读复验没有创建订单、撤单或移动资金：

- 公共 REST：24 个现货市场；`BTC-USDT` 正确转换为 `BTC/USDT`；行情、买一卖一和趋势均取得真实数据。
- 公共 WebSocket：6 类流全部收到真实更新并通过标准字段检查。
- 私有 REST：当前挂单 0；历史订单和个人成交各读取 1 条；资金流水读取 2 条 USDT 样本；余额返回
  2 个币种，`free/used/total` 字段齐全。订单号、账户号、精确余额和流水金额未写入仓库。
- 私有 WebSocket：第二部分已使用 Sandbox `create_mock_order` 同时验收订单、个人成交和余额事件；
  本轮适配器未改动该协议路径，因此没有重复制造写入。

在线过程中 `GET /market/v1/meta` 曾连续两次连接超时；随后独立 HTTPS 探测返回 HTTP 200，重试后
全部公共项目通过，判断为公网开发环境短时链路波动。适配器不会自动重试写请求。

## 构建产物

- `dist/ccxt_cm-0.1.0-py3-none-any.whl`：`e32bb021e175a39269289757c2c09a0ec2869c8c0de56b1ff198e9fe1f492176`
- `dist/ccxt_cm-0.1.0.tar.gz`：已生成；最终 wheel 与 sdist 哈希保存在本地 `dist/SHA256SUMS`。
- wheel 中 `ccxt_cm/exchanges/bifu.py` 与工作区源文件 SHA-256 一致：`cf86ef8d6d23ae74e813ff66ea85d09cfb1d459306bb49124990b68a0cf58147`
- wheel 已安装到临时目录，并从仓库外成功导入 `ccxt_cm` 和 `BifuREST`；实际加载路径来自临时安装目录。

构建产物受 `.gitignore` 排除，不提交二进制；以上文件保留在本地 `dist/`，代码、测试和交付说明
提交到 `Jacky` 分支。

## 已知边界

- Bifu 结束订单可能从单笔查询接口移入历史列表并返回 `OrderNotFound`；适配器不会凭历史列表伪造
  单笔查询终态，调用方可结合历史订单和当前挂单对账。
- WebSocket 文档目前只确认 `1m` K 线；其他周期不虚报支持。
- `fetch_closed_orders`、`fetch_my_trades` 和 `fetch_ledger` 当前按已确认的单页/游标能力实现，边界已在
  能力矩阵说明。
- 测试和生产使用同一适配器，但本轮没有生产 URL、生产 Key 或生产写入验收。生产上线前应先执行
  明确授权的只读验收，再单独决定是否允许生产写操作。
