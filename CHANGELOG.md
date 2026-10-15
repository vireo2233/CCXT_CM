# Changelog

## Unreleased — 2026-09-20

- 完成 Bifu 公共及私有异步 REST、Bifu 专有 `create_mock_order`，以及公共及私有 WebSocket 适配。
- 写入验收工具统一强制 Bifu Sandbox、预生成 `clientOrderId` 并按本轮订单安全清理；禁止误用生产实例。
- 完成测试环境在线验收、全量回归、双轴代码复审、无调用点清理检查及 wheel/sdist 正式构建。

## 0.1.0 — 2026-09-15

- 官方 sync / async REST / Pro 类动态解析和原实例复用。
- 自定义 Extension 注册、模式区分、冲突保护和显式安装包 entry point。
- 保守的自定义 Exchange / AsyncExchange 基类与四态能力检查。
- 特殊接口元数据，不新增交易返回类型或任意 URL 网关。
- 仅 loopback 的参考 REST/Pro 适配器，测试签名/解析/行情/订单/余额/快照增量/断连恢复。
- 适配器模板、离线检查示例、公共只读 opt-in 示例。
- 中文架构、接入、接口、WS、测试、Antarctic 审计和 market_maker_v2 迁移文档。
- CI、锁文件及 wheel/sdist 构建。未发布 PyPI；未接入真实 Antarctic 或做市系统生产环境。
- Bifu 公共及私有 WebSocket：行情、逐笔成交、深度、买一卖一、1 分钟 K 线、订单、账户成交和现货余额。
- Bifu 私有流使用签名请求头且不在 URL 暴露凭证；每次重连重新签名，鉴权头不会泄露到公共连接。
- Bifu 深度序号断档会只清理相关缓存并重连获取新快照；其他仍在线的订阅不受影响。
- Bifu 盘口先建立 WebSocket 并缓冲增量，再以 REST 快照为基线衔接序号，不假设首条增量是全量盘口。
