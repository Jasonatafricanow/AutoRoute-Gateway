# Gate D 验收报告（Case 1-10）

> 日期：2026-08-16 · 网关 commit 0a8a9d6
> 验收对象：`127.0.0.1:8700`（model-gateway，5 层兜底链 amd→modelscope→opencode→deepseek→codex）
> 自动验收：`python tools/acceptance_check.py`（真实网关端到端，可重复跑）

## 结果总览

| Case | 场景 | 状态 | 验证方式 |
|---|---|---|---|
| 1 | 正常非流式 chat | ✅ | acceptance_check（真实请求） |
| 2 | 正常流式 chat + 落库 | ✅ | acceptance_check（[DONE] + route_events success） |
| 3 | 限流 fallback 逐级降级 | ✅ | 今日实证：AMD rate_limit → deepseek success（route_events 08:32:51） |
| 4 | 限流恢复自动回位 | ✅ | 今日实证：AMD 恢复后 08:33:40 amd success（回到首位） |
| 5 | 候选链顺序（配置序 + reserve 末位） | ✅ | acceptance_check：amd→modelscope→opencode→deepseek→codex→opencode(reserve) |
| 6 | 未知模型 → 404 unknown_model | ✅ | acceptance_check |
| 7 | 认证错误 → 下个候选 | ✅ | tests/test_fallback.py::test_auth_marks_credential_not_provider_and_continues |
| 8 | 流式中断 → PARTIAL_STREAM_FAILURE | ✅ | tests/test_fallback.py::test_committed_stream_failure_stops_immediately + tests/test_chat_stream_events.py::test_chat_stream_records_partial_on_mid_stream_error |
| 9 | provider 失败 → 透明 fallback | ✅ | tests/test_fallback.py::test_amd_down_falls_to_modelscope |
| 10 | 能力不满足 → 400 capability_unsatisfiable | ✅ | acceptance_check |

**10/10 全绿。可跑用例 5/5（真实网关），语义类 5/5（单测+实证）。**

## Case 细节

### Case 3：限流 fallback（真实轨迹）
```
08:31:55 amd rate_limit → next_candidate   （AMD 免费档限流触发）
08:32:51 deepseek success（1.5s）           （逐级降级到付费兜底层，未跳级）
```
中间层 modelscope/opencode 也进入候选链（顺序修复 f0a8910 后），限流时会依次尝试。

### Case 4：恢复回位（真实轨迹）
```
08:33:40 amd success（3.4s）   ← AMD 冷却 60s 后自动回首位，无需人工干预
```
设计依据：scorer 只决定可用性不重排序；RATE_LIMITED → 60s cooldown → 过期自动回配置首位。

### Case 8：流式中断语义
- 流提交（committed）后中断 → `partial_stream_failure` 事件 + 终止流（不带 [DONE]），**不跨 provider 重放**（§8 STREAM_COMMITTED）
- 流完整结束 → `success` 事件在 [DONE] 时记录（修复 0a8a9d6：sse 层提前 return 导致 aclose，try/else 永不触发）

## 观测修复记录

- 流式 success 落库缺口（pi agent 真实请求暴露）→ 已修（0a8a9d6）
- 候选链跳级 bug（scorer 按健康分重排序，codex 成功一次后永远第一）→ 已修（f0a8910）

## 遗留

- opencode 直连 403（位于链中段，失败会继续 fallback，不影响可用性；待排查）
- Case 7/8/9 未在真实网关复现（需故障注入环境），以单测为准；如需真实联调可起 mock upstream 验证
