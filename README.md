# AutoRoute-Gateway (v0.3.1)

> 面向多厂商、多凭证的 Capability-Aware LLM 智能自路由网关。
> 实现依据：`模型网关-v0.3.1-正式基线-最终版.md`（FROZEN 架构基准）。

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-teal.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🌟 核心特性

**AutoRoute-Gateway** 专为多模型环境下的高可用、低故障调用设计。传统 API 网关仅做简单反向代理，而 AutoRoute-Gateway 具备**深度自路由与流量治理能力**：

1. 🧭 **智能自路由（Auto-Routing & Scoring）**：
   * **Capability Gate**：按请求要素（流式、工具调用、多模态视觉、上下文长度）自动过滤不匹配的 Provider。
   * **三态打分器（Scorer）**：基于健康状态、历史延迟、免费/付费策略及配额余量动态排序，自适应调度到当前最优节点。
2. 🔑 **多凭证与独立配额（Multi-Credential Isolation）**：
   * 严格贯彻 **Provider ≠ Credential**：单个 Key 额度耗尽（Quota Exhausted）或触发限流（429）绝不影响同 Provider 下的其他候选 Key。
   * 支持静态主备与平滑轮询（Round-Robin）Key 池。
3. 🛡️ **流式安全提交（Streaming Commit Guard）**：
   * 当下游客户端已经收到首个 chunk 后，上游若发生中断，立即终止当前流并抛出 `PARTIAL_STREAM_FAILURE`，**坚决禁止跨 Provider 重放**，杜绝客户端收到错乱混合回答。
4. 🩺 **七类精细化错误分类与自动降级（Fallback Engine）**：
   * 严格区分 429 短期限流与长期额度耗尽；遇到认证错误（Auth）仅临时屏蔽当前 Key 并立即切换 `next_candidate`。
5. 📊 **无感兼容**：
   * 对外暴露标准 OpenAI 兼容接口（`/v1/chat/completions`、`/v1/models`）。

---

## 🏗️ 架构与请求流

```
客户端请求 (Web / Agent / 外部系统)
    ↓ OpenAI-compatible POST /v1/chat/completions
[ Virtual Model Resolver ] —— 解析虚拟模型（如 gateway-fast, gateway-deep）
    ↓
[ Capability Gate ] —— 按需求（Tools / Vision / Stream）过滤候选者
    ↓
[ Candidate Builder & Scorer ] —— 动态打分与自路由排序
    ↓
[ Router ] —— 生成候选执行队列 (Candidate Queue)
    ↓
[ Executor & Fallback Engine ] —— 执行调用，捕获七类异常，自动流转降级
    ↓ (首包发送后锁定，保护流式一致性)
响应返回下游
```

---

## 📂 目录结构

| 路径 | 内容与作用 |
| :--- | :--- |
| `gateway/` | 核心实现（domain / policy / routing / providers / executors / state / config / api / capture） |
| `tools/` | `run_gateway.py`（启动入口）、`smoke_test.py`（冒烟测试）、`capture_proxy.py`（抓包代理） |
| `tests/` | 82 个单元与集成测试（覆盖 Case 1~10 验收基准） |
| `docs/` | 契约与规范文件（`SPEC.md`、`REAL_TRANSPORT_CONTRACT.md`、`PROVIDER_CAPABILITY_CONFORMANCE_MATRIX.yaml`） |
| `examples/` | 配置示例模板（`gateway.yaml.example` 与 `.env.example`） |

---

## 🚀 快速开始

### 1. 配置准备

```powershell
# 复制配置文件模板
Copy-Item examples\gateway.yaml.example gateway.yaml
Copy-Item examples\.env.example .env

# 编辑 gateway.yaml 配置路由规则；编辑 .env 填写对应 Provider 的 API Key
```

### 2. 运行服务

```powershell
# 使用 Python 3.11+ 运行
python -m tools.run_gateway --config gateway.yaml --env .env
# 默认监听 http://127.0.0.1:8700
```

### 3. 核心接口

* **状态与监控**：
  * `GET /health`：网关健康状态检查
  * `GET /ready`：就绪探针
  * `GET /providers`：已注册 Provider 状态与可用 Key 数量
  * `GET /routes`：当前激活的路由矩阵与虚拟模型映射
* **模型服务面**：
  * `POST /v1/chat/completions`：标准聊天对话与流式接口
  * `GET /v1/models`：可用虚拟模型列表

---

## 🧪 测试与验收

```powershell
# 运行全部单测与验收用例
pytest -q
```

---

## 📄 许可证

本项目采用 [MIT License](LICENSE) 授权。
