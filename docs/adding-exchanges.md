# 新增交易所规范

## 选择实现方式

1. 先检查当前官方 `ccxt.exchanges` / `ccxt.pro.exchanges` 和相应方法。官方已有的能力直接用官方实现。
2. 官方有主体实现但缺一个接口：继承官方类，只增加差异；使用不同的显式扩展 ID，并说明原始官方 ID。
3. 官方尚未支持：从 `ccxt_cm.AsyncExchange` 开始实现 REST，Pro 再继承此 REST 类。同步类可选，不强制复制一套。
4. 独立接入包与库内模块都可注册。不依赖固定几家交易所，也无需修改工厂分支。

建议目录（均相对新适配器包根目录）：

```text
src/vendor_adapter/
  __init__.py       # 导出 EXTENSION，不联网
  rest.py           # describe / implicit endpoints / sign / parse / fetch / create / cancel
  pro.py            # 继承 rest，认证/订阅/消息转换
  implicit.py       # 可选：ccxt.base.types.Entry endpoint 声明
  protocol/         # 若有 protobuf：schema 与可复现生成方法
 tests/             # 脱敏 fixtures、签名 golden case、REST/WS 契约测试
 docs/              # 能力矩阵、接口来源、限制、真实验收记录
```

空模板在 `templates/exchange.py`。可执行的完整小协议实现见 `examples/reference_exchange.py`；仅用于本机演示，不是 Antarctic API 的猜测实现。

## 最小接入步骤

### A. 锁定协议和能力

- 记录官方 API 文档 URL/版本、REST 与 WS 分别的生产/测试环境、鉴权方式、频率限制。
- 确认市场类型、统一 symbol、amount 单位、contractSize、precisionMode、limits、状态码。
- 建立 REST/Pro/特殊接口矩阵；没实现或没验证的标 `False`，未知标 `None`，组合实现标 `'emulated'`。
- 缺少文档、脱敏响应、协议序号或账户权限时，明确列入待验证，不编造契约。

### B. REST

- `describe()` 声明 id、urls、API 路由和成本、credentials、has、features（有依据时）、exceptions。
- 新类声明静态 `id` 与 `describe()['id']` 一致。注册时检查静态 ID 与类型。
- `sign()` 只做请求构造，使用官方工具；覆盖 GET 查询排序、编码、JSON/body、nonce/时间戳、签名大小写等 golden cases。
- 复用官方 `request`、HTTP 生命周期、限流与代理。不要把所有 endpoint cost 写为 0。
- 先实现 `fetch_markets` 和 `parse_*`，再实现统一请求方法。
- 遵守 `create_order(symbol, type, side, amount, price, params)`；用测试核对签名。
- 不接受的订单类型/参数抛错。要求 symbol 的查询明确抛 `ArgumentsRequired`，不假装支持跨 symbol 查询。
- 批量/分页/快单/撤单 ACK 的边界要单独测试。

### C. Pro

按 [WebSocket 规范](websocket.md) 实现。复用官方 `watch` / `client` / `resolve` / `reject` / `ArrayCache*`；不要复制整个 client 或重写框架的连接管理，除非协议确实要求且有专项测试。

### D. 注册

直接注册：

```python
from ccxt_cm import Extension, Registry
from vendor_adapter.rest import VenueREST
from vendor_adapter.pro import VenuePro

EXTENSION = Extension("target", rest=VenueREST, pro=VenuePro)
registry = Registry()
registry.register(EXTENSION)
exchange = registry.create_exchange("target", mode="pro")
```

通过 Python 安装包 entry point（值指向一个 `Extension` 对象，不是 factory 函数）：

```toml
[project.entry-points."ccxt_cm.exchanges"]
my_target = "vendor_adapter:EXTENSION"
```

```python
from ccxt_cm import Registry

registry = Registry()
registry.load_extensions(["my_target"])
```

安装不等于自动执行插件。只加载明确名称；同名 entry point、多次注册、与官方冲突、错误基类、Pro 不继承已注册 REST 都报错。批量注册失败不留下半套注册状态。

## 受管现货执行

- SDK 能创建客户端不代表已通过账户验证、原单确认和断线补账。通过同一套离线验收后，在 `ccxt_cm.spot_execution` 登记原生客户端的市场能力；自定义 Pro 客户端可在现有注册类上声明 `cm_spot_execution = {环境: 已验收适配实例}`。范围为具体交易所、测试/模拟环境和现货，不自动放行正式环境。
- 优先使用 CCXT 的统一方法；只在统一结果缺失身份、终页、精确费用等关键证据时补原生适配。原单结果区分 `found`、`complete_absence`、`unconfirmed`。查无并不证明写入被拒绝；接口失败或不完整不能当作查无。
- 核心执行与公共成交完整窗口、双腿限价、独立人工下单等能力分别登记。Bybit Testnet 保留既有能力；Binance Spot Testnet 只登记本次离线覆盖的核心现货执行，未登记补量、人工下单等额外能力。真实环境验收仍须另做。

## 发布门槛

- [ ] 官方能力已检查，没有复制维护官方已实现代码。
- [ ] 无策略、数据库、Nacos、后台任务或做市系统模型依赖。
- [ ] 所有 `has=True` 对应具体实现和正/负例测试；没有 `pass` 成功路径。
- [ ] 每种支持市场的精度、最小量/金额、contractSize 和符号测试通过。
- [ ] 签名、认证、权限、限流、业务错误映射已测。
- [ ] 未知状态/成交/余额保留未知，不伪造执行结果。
- [ ] 单笔与批量写操作不盲目重试，params/clientOrderId 语义有据可查。
- [ ] WS 快照、增量、重复、乱序、断档、认证失败、断连、重订阅、close 已测。
- [ ] 私有流完整性、缓存大小和 newUpdates 语义已说明。
- [ ] 特殊方法有参数、返回、权限、是否写入、错误语义和测试。
- [ ] raw fixtures 脱敏；无私钥、密钥、账户 ID、内部 endpoint 或客户数据。
- [ ] 完成打包与新环境安装；写清 Python/CCXT 版本和真实验收范围。

通过离线测试只代表协议契约验证通过。真实上线前，另行执行公共只读、私有只读、sandbox 写入及明确授权的生产验收；记录具体环境、日期和方法，不写笼统“交易所已全面支持”。
