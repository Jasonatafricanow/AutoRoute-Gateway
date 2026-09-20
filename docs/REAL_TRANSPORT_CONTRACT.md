# REAL_TRANSPORT_CONTRACT（Gate B1 产物框架）

> 依据：基线 §13 / §19。**先由真实请求 Capture，不靠想象。**
> 本文档由 Phase 0 recording gateway 的 capture 数据填充；当前为框架 +
> 已用合成流量验证的捕获能力清单。

## 状态

- [x] recording gateway 已实现（`gateway/capture/` + `tools/capture_proxy.py`）
- [x] 脱敏与"不存 prompt"约束已实测（authorization → `<redacted>`，content 只记类型+长度）
- [x] **Kayla（pi-coding-agent）真实流量已捕获**（2026-08-16，capture/real_pi.jsonl，capture proxy 8711 透传 8700）
- [ ] 待 Hermes / dsh / 梨园 各跑一轮真实任务，填充下方矩阵
- [x] **Kayla 列冻结**（见下方矩阵）——Gateway 必须兼容的消费端形态已实测

## 采集方法

```powershell
# 让消费端把 endpoint 指向 8711，跑一轮真实任务
python -m tools.capture_proxy --port 8711 --out capture/real_hermes.jsonl
python -m tools.capture_proxy --port 8711 --out capture/real_dsh.jsonl
# 有真实网关时也可做 capture proxy（请求透传，响应同样只记 schema）：
python -m tools.capture_proxy --port 8711 --out capture/real_hermes.jsonl --upstream http://127.0.0.1:8700
```

capture 每行记录：`request`（方法/路径/header schema/模型/messages role 序列/content 块类型/tools/tool_choice/stream/response_format/参数/未知字段）、`response`（shape/choices/message/tool_calls/finish_reason/usage）、`sse_first_chunk`/`sse_done`、`error`（status/body schema/retry-after/rate-limit headers）。

## Request Capability Matrix（Kayla 已实测冻结）

| | Hermes | dsh | 梨园 | **Kayla** | 手机 Agent |
|---|---|---|---|---|---|
| chat（非流式） | ⬜ | ⬜ | ⬜ | ✅ persona 注入走非流式 | ⬜ |
| stream | ⬜ | ⬜ | ⬜ | ✅ 对话流式（stream=bool） | ⬜ |
| tools | ⬜ | ⬜ | ⬜ | ✅ **10 个工具定义**（type/function.name/description/parameters） | ⬜ |
| tool_choice | ⬜ | ⬜ | ⬜ | ⬜（未发送，null） | ⬜ |
| structured output | ⬜ | ⬜ | ⬜ | ⬜（未发送，null） | ⬜ |
| vision content | ⬜ | ⬜ | ⬜ | ⬜（暂无图片消息） | ⬜ |
| 多轮上下文 | ⬜ | ⬜ | ⬜ | ✅ roles=[system,user,assistant,user] | ⬜ |
| 超长 system prompt | ⬜ | ⬜ | ⬜ | ✅ **12,218 字符**（Kayla 人设注入） | ⬜ |
| 特殊字段（未知字段清单） | | | | 无未知字段 | |

> Kayla 实测结论：pi-coding-agent 的 openai-completions 客户端发送**标准 OpenAI 兼容请求**——stream 布尔、tools 列表（10 个）、多轮含 system 角色；无 tool_choice / response_format / vision。这定义了 Gateway 对 pi 的最小兼容面。

## Response Schema Matrix（待真实数据填充）

- response object shape / choices/message shape
- tool_calls shape / finish_reason / usage shape
- 各消费端对 `model` 字段回显的敏感度（回虚拟模型名是否破坏客户端）

> 注：Kayla 已确认对回显 `model=gateway-fast` 不敏感（运行正常）；usage 由 Gateway 透传。

## Streaming / Error Semantics（Kayla 已实测冻结）

- SSE: event/data framing / 首 chunk 结构 / delta.content / delta.tool_calls / [DONE] 语义 → ✅ pi 客户端完整消费 SSE 流（含 [DONE]），流式 success 事件落库（0a8a9d6 修复后）
- error: HTTP status / error body schema / retry-after / rate-limit headers → ✅ 网关 502/404/400 error body `{error:{message,type,code}}`；Kayla 遇错正常降级
- 消费端对"截断流（无 [DONE]）"的容错行为（§8 终止语义必须验证）→ ⬜ 未实测（需故障注入）

## 目标（§13）

不仅要回答"消费端会发什么"，还要回答"**Gateway 必须回什么，消费端才不会坏**"。
