# Gate A —— SPEC 细化（domain model 代码化 + 接口签名）

> 依据：`模型网关-v0.3.1-正式基线-最终版.md`（FROZEN 唯一准绳）
> 状态：✅ 完成 · 单元测试 63 passed · 启动实测通过（故障链路 + 冷却 + 事件落库）

## 1. 模块边界与接口签名

```
gateway/
├─ api/
│  ├─ openai.py        POST /v1/chat/completions（流式/非流式）· GET /v1/models
│  └─ status.py        GET /health · /ready · /providers · /routes
├─ app.py              create_app(config, resolver, store, service) → FastAPI
│                      安全中间件：auth token + network ACL（§24.22）
├─ service.py          GatewayService —— 请求管线编排 + streaming commit
├─ domain/             （纯数据，无 IO）
│  ├─ capability.py    Capability / CapabilitySet / required_capabilities_from_request()
│  ├─ model.py         VirtualModel（策略意图）/ ConcreteModel（具体模型）
│  ├─ provider.py      Provider（kind: openai_compatible|gemini|executor）
│  ├─ credential.py    Credential（独立实体：env_var/priority/reserve）
│  ├─ candidate.py     RouteCandidate{provider_id, credential_id, concrete_model,
│  │                   capabilities, executor_type(api|cli), reserve}
│  └─ state.py         HealthState/QuotaState/CredentialState + StateSubject +
│                      ResetPolicy + QuotaInfo
├─ policy/
│  ├─ error_classifier.py  classify_error(status, body, headers) → 七类
│  └─ retry_policy.py      RetryPolicy + next_action() → RETRY_SAME/NEXT/STOP
├─ routing/
│  ├─ candidate_builder.py build_candidates(virtual_model, config) → [RouteCandidate]
│  ├─ capability_gate.py   capability_gate(candidates, required)  # Router 之前
│  ├─ scorer.py            score_candidates(candidates, store) → [ScoredCandidate]
│  └─ fallback.py          FallbackEngine.run(vm, scored, attempt_fn, request_id)
├─ providers/
│  ├─ base.py          ProviderAdapter / ExecutorAdapter 契约 + UpstreamHttpError
│  ├─ openai_compatible.py  OpenAI 协议 transport（free-proxy 借鉴层，§17）
│  ├─ registry.py      build_adapter(kind, provider_id, base_url, models)
│  └─ gemini.py        Phase 2 独立适配器（显式 capability 声明，§12）
├─ executors/codex.py  ExecutorAdapter，enabled=false（§18）
├─ state/
│  ├─ store.py         StateStore 协议 + InMemoryStateStore
│  └─ sqlite.py        SqliteStateStore（provider_health/credential_state/
│                      quota_state/route_events/usage_daily）
├─ config/
│  ├─ schema.py        GatewayConfig/RouteConfig/ProviderConfig/KeyConfig/…
│  │                   + SecretResolver（secret 不进日志/状态）
│  └─ loader.py        load_gateway_config(yaml, env) + capability matrix
└─ capture/            Phase 0 recording gateway（Gate B1）
```

## 2. 关键接口签名（冻结契约）

### 2.1 ProviderAdapter（API 型 provider，§12）

```python
class ProviderAdapter(ABC):
    provider_id: str
    def list_models(self) -> list[ConcreteModel]: ...
    def capability_manifest(self, model_id: str) -> CapabilitySet: ...
    async def chat_completions(self, payload, credential_secret, model_id) -> dict: ...
    async def chat_completions_stream(self, payload, credential_secret, model_id) -> AsyncIterator[dict]: ...
    async def probe(self, credential_secret) -> HealthState: ...
    async def quota_info(self, credential_secret) -> QuotaInfo | None: ...
```

### 2.2 ExecutorAdapter（CLI 型，§12/§18）

```python
class ExecutorAdapter(ABC):
    executor_type: str = "cli"
    async def execute(self, task: dict) -> dict: ...
    def capability_manifest(self) -> CapabilitySet: ...
```

### 2.3 错误分类（§7，继承 free-proxy）

```python
class ErrorClass(str, Enum):
    AUTH / TOKEN_LIMIT / RATE_LIMIT / QUOTA / MODEL_NOT_FOUND / NETWORK / SERVER / UNKNOWN

def classify_error(status, body_text, headers) -> ErrorClass:
    # body 信号优先；429 默认 rate_limit，body 带 quota 信号 → QUOTA
    # 401/403 → AUTH（仅禁用当前 credential，NOT 全局 STOP）
    # status None → NETWORK
```

### 2.4 Retry / Attempt Budget（§7 v0.3.1）

```python
RetryPolicy(per_candidate_retry_limit=2, route_candidate_limit=None, hard_attempt_ceiling=50)
# 禁止固定小上限截断 candidate pool（P0-2）
```

### 2.5 StateStore（§9 StateSubject 隔离）

```python
class StateStore(Protocol):
    def get_health(subject) -> HealthState: ...   # (p,c,m)→(p,m)→(p) 回退
    def set_health(subject, state, reason, cooldown_until) -> None: ...
    def get_quota(subject) -> QuotaState: ...     # (p,c,m)→(p,c)→(p) 回退
    def set_quota(...) -> None: ...
    def get_credential(subject) -> CredentialState: ...
    def set_credential(...) -> None: ...
    def get_cooldown(subject) -> datetime | None: ...
    def record_route_event(event: dict) -> None: ...
    def snapshot() -> dict: ...
```

**scope 回退规则**：provider 级 DEGRADED 对旗下所有 key 可见；key1 的 EXHAUSTED
只命中 (provider, key1) scope，key2 与 provider scope 不受影响（P0-3）。

### 2.6 Fallback Engine 决策表（§7 实现）

| 错误类 | 状态写入（scope） | 动作 |
|---|---|---|
| auth | credential=AUTH_ERROR (p,c) | next_candidate |
| quota | quota=EXHAUSTED (p,c)，observed_at | next_candidate |
| rate_limit | quota=RATE_LIMITED (p,c,m) + cooldown(retry-after) | next_candidate |
| model_not_found | health=DOWN (p,m) | next_candidate |
| network / server | health=DEGRADED (p) + cooldown | next_candidate |
| token_limit | 无 | retry_same（≤ per_candidate_retry_limit）→ next |
| 任意 committed=True（流已提交） | 记录 PARTIAL_STREAM_FAILURE | **stop，禁止重放**（§8） |
| 无 eligible candidate | — | stop（NoEligibleCandidateError） |
| attempts ≥ hard_attempt_ceiling | — | stop（HardAttemptCeilingError 安全阀） |

## 3. Streaming Commit Semantics（§8 实现）

```
STREAM_NOT_COMMITTED = 尚未向下游发送第一个有效 chunk（role-only chunk 不算）
                     → Provider 失败 → 透明 fallback 到下一个候选（验收 Case 9 ✓）
STREAM_COMMITTED     = 已发送 ≥1 个有效 chunk（content/tool_calls/reasoning）
                     → 失败即终止当前 stream，不重放，不补 [DONE]
                     → 记录 PARTIAL_STREAM_FAILURE（验收 Case 8 ✓）
```

实现：`GatewayService.chat_stream` 预取首事件再进入发送循环；
`StreamHandle.committed` 按有效 chunk 判定；post-commit 失败抛
`PartialStreamFailure`，API 层终止 SSE 且**不发送 [DONE]**。

## 4. 配置三分离（§14）

| 文件 | 内容 | 示例 |
|---|---|---|
| `gateway.yaml` | 策略/路由/providers/keys/capability/reset_policy | `examples/gateway.yaml.example` |
| `.env` | secrets + 少量启动配置 | `examples/.env.example` |
| `gateway.db` | SQLite 运行状态 | 自动创建 |

capability 的**权威来源是 Gate B2 实测矩阵**（`capability_matrix_path`），
YAML 声明仅为默认值（§24.21）。

## 5. 已冻结决策的代码落点对照

| Frozen # | 内容 | 落点 |
|---|---|---|
| 1 | policy 自研 | gateway/policy, routing 全新实现 |
| 2 | Provider≠Credential | domain/provider.py + credential.py 独立实体 |
| 3 | multi-key first-class | KeyConfig list + reserve + 只改配置加 key（测试 Case 5） |
| 4 | Router/Fallback 分离 | scorer（只排序）+ FallbackEngine（决策） |
| 5 | 三态正交 | Health/Quota/CredentialState 独立维度（测试） |
| 6 | VirtualModel≠Capability | domain/model.py + capability.py |
| 7 | Capability Gate 在 Router 前 | routing/capability_gate.py（测试） |
| 8 | SQLite runtime state | state/sqlite.py |
| 9 | 配置三分离 | config/ |
| 10 | Codex 独立 Executor | executors/codex.py（disabled） |
| 11 | Streaming commit | service.py + api/openai.py（测试 Case 8/9） |
| 12 | reset_policy 不猜本地日期 | ResetPolicy(unknown 默认)，无本地日期 reset 逻辑 |
| 13 | Windows→NAS 可迁移 | bind/port/db 全配置化 |
| 14 | Phase 0 先抓真实请求 | capture/（Gate B1） |
| 15 | 七类 + 429≠quota | policy/error_classifier.py（测试） |
| 16 | auth=credential 禁用→next | fallback.py _apply_failure（测试） |
| 17 | 禁固定上限截断池 | RetryPolicy 默认值（测试） |
| 18 | StateSubject 隔离 | store scope 回退（测试 P0-3） |
| 19 | Adapter 分离 executor_type | providers/base.py + executors/ |
| 20 | Transport Contract 三件套 | capture/（请求+响应+流/错误） |
| 21 | capability 来自 smoke test | tools/smoke_test.py → matrix → loader |
| 22 | non-loopback 需 ACL/auth | app.py 中间件（默认 loopback-only） |

## 6. 验证结果

- `pytest`：**63 passed**
- 启动实测（127.0.0.1:8710，上游全部不可达场景）：
  - 非流式：amd→modelscope→opencode/ocg-01 依次尝试，全部 network → 502
    `code=network`；route_events 3 行落库 ✓
  - 冷却期第二次请求：0 次尝试 → 502（cooldown 生效，不空耗上游）✓
  - `/health /ready /routes /v1/models /providers` 全部正常 ✓
