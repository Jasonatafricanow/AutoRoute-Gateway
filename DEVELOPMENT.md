# AutoRoute-Gateway · 研发演进与设计基线

本文档记录 AutoRoute-Gateway 的核心架构设计决策、Gate 验收路线与研发演进历程。

---

## 1. 核心设计原则

### 1.1 Provider 与 Credential 解耦
在多模型网关中，单一提供商往往配置了多个 API Key（如团队 Key、主备 Key、个人 Key）。传统网关在遇到单个 Key 触发配额耗尽（Quota Exhausted）或 HTTP 429 限流时，往往会将整个 Provider 标记为不可用。
AutoRoute-Gateway 将 Provider（厂商服务定义）与 Credential（鉴权凭证实体）严格解耦：
* 状态打标绑定在单个 Credential 上。
* 单个 Key 失效仅触发内部退避或切换下一个备用 Key，不影响同 Provider 的其他可用凭据。

### 1.2 流式提交保护（Streaming Commit Guard）
* 非流式请求：在上游模型失败时，可安全地重试或切换至备选模型。
* 流式请求：一旦下游客户端收到了哪怕一个 Token chunk，上游若发生连接中断，严禁跨 Provider 切换重试（否则下游将收到两段不同模型拼凑的不可理解文本）。网关会立即向流内写入 `PARTIAL_STREAM_FAILURE` 标记并关闭通道。

### 1.3 七类精细化错误分类
将上游各厂商杂乱的 HTTP 状态码和 JSON 错误体统一映射为标准错误枚举：
1. `AUTH_FAILURE`：密钥失效或未授权（单 Key 隔离）
2. `RATE_LIMITED`：短期限流（指数退避）
3. `QUOTA_EXHAUSTED`：额度耗尽（长期熔断）
4. `MODEL_NOT_FOUND`：模型不存在或已下架
5. `CONTEXT_LENGTH_EXCEEDED`：提示词超长
6. `SERVER_ERROR`：上游 5xx 故障（触发降级切换）
7. `TIMEOUT`：超时

### 1.4 CLI 兜底执行器（Codex CLI Fallback）
在所有线上 API 均不可用或配额耗尽的极端场景下，网关设计了 `ExecutorAdapter`（`executor_type = cli`）作为终极兜底链路：
* 基于 `codex exec` 非交互模式调用，通过 `--json` 逐行解析 JSONL 事件流与 usage 指标。
* 支持 `--output-schema` 结构化输出校验与只读沙箱隔离（`--sandbox read-only`）。
* 错误分类与降级：通过子进程退出码与末尾 error 事件双路判定，确保在无可用在线 API 时依然具备服务可用性。

---

## 2. Gate 阶段演进与验收

项目研发严格遵循 Gate 门禁驱动模式：

* **Gate A · SPEC 规范冻结**：
  * 完成领域模型（Domain Model）抽象、类型注解与接口签名定义。
  * 产物：`docs/SPEC.md`。
* **Gate B1 · 真实抓包契约提取**：
  * 开发 `capture_proxy.py`，用于透明代理并录制消费端（Agent、Web 前端）真实流量。
  * 产物：`docs/REAL_TRANSPORT_CONTRACT.md`。
* **Gate B2 · 冒烟与能力矩阵实测**：
  * 构建多厂商能力探测脚本 `smoke_test.py`，测试并冻结各厂商模型的工具调用与多模态真实支持情况。
  * 产物：`docs/PROVIDER_CAPABILITY_CONFORMANCE_MATRIX.yaml`。
* **Gate C · Phase 1 MVP 落地**：
  * 实现基于 SQLite 的持久化状态存储、三态打分器、自路由算法与降级引擎。
  * 完成 63 个基础单元测试。
* **Gate D · 场景化验收与测试补全**：
  * 覆盖 Case 1~10 验收基准（含网络抖动、Key 轮询、流式截断、备选降级等极端边界情况）。
  * 测试集扩展至 82 项，全面绿灯。

---

## 3. 测试与验证基线

```powershell
# 执行全部测试套件
pytest -v

# 仅测试核心路由与状态机
pytest tests/test_routing.py tests/test_state.py
```
