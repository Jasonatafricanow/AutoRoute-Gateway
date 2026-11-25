# AutoRoute-Gateway

[English](README.md) | [简体中文](README.zh-CN.md)

按配置顺序尝试兼容供应商凭据的 LLM 网关，明确区分流式输出开始前和开始后的失败处理。

## 路由行为

- 先按能力要求排除无法处理请求的候选。
- 可用候选保持配置顺序。分数用于诊断，不会按延迟、价格或成功率自适应排序。
- 凭据健康状态可以排除单个密钥，不必关闭整个供应商。
- 有实质输出前可以回退；输出提交后发生失败则明确报错，避免拼接两个供应商的回答。

## 运行与验证

```powershell
python -m pip install -e ".[dev]"
Copy-Item examples\gateway.yaml.example gateway.yaml
Copy-Item examples\.env.example .env
python -m tools.run_gateway --config gateway.yaml --env .env
python -m pytest -q
```

`gateway.yaml` 和 `.env` 是本地部署配置，新部署从示例复制。反向代理后面需配置鉴权 token。可选 Codex CLI 执行器默认禁用；启用时应使用无法读取网关密钥和私有文件的独立系统用户或容器。

## 实现入口

[路由及验证说明](docs/IMPLEMENTATION.md) · [加固历史](docs/AUDIT-HISTORY.md) · [传输契约](docs/REAL_TRANSPORT_CONTRACT.md)

已有 `/v1/chat/completions`、`/v1/models`、`/health`、`/ready`、`/providers`、`/routes`。适配器覆盖 OpenAI 兼容协议和 Gemini。自适应排序、Anthropic 原生 Messages、路由解释和指标接口仍属后续工作。

## 许可

MIT，见 [LICENSE](LICENSE)。
