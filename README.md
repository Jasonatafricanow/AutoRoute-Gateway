# AutoRoute-Gateway

[English](README.md) | [简体中文](README.zh-CN.md)

An LLM gateway that tries compatible provider credentials in configured order and handles failure before and after streaming output begins.

## Routing behavior

- Capability checks exclude candidates that cannot serve a request.
- Eligible candidates retain configuration order. Scores are diagnostic; they do not provide adaptive latency, price or success-rate ranking.
- Credential health can exclude a key without disabling its whole provider.
- Failure before substantive output may fall back. After output is committed, a failure is surfaced instead of mixing two providers' responses.

## Run and verify

```powershell
python -m pip install -e ".[dev]"
Copy-Item examples\gateway.yaml.example gateway.yaml
Copy-Item examples\.env.example .env
python -m tools.run_gateway --config gateway.yaml --env .env
python -m pytest -q
```

`gateway.yaml` and `.env` are local deployment configuration. Use the example template for a new installation. Configure an authentication token behind a reverse proxy. The optional Codex CLI executor is disabled by default; if enabled, isolate its OS user/container from gateway secrets and private files.

## Inspect the implementation

[Routing and verification guide](docs/IMPLEMENTATION.md) · [hardening history](docs/AUDIT-HISTORY.md) · [transport contract](docs/REAL_TRANSPORT_CONTRACT.md)

Endpoints include `/v1/chat/completions`, `/v1/models`, `/health`, `/ready`, `/providers` and `/routes`. OpenAI-compatible and Gemini adapters are present. Adaptive ranking, Anthropic-native Messages, route explanations and a metrics endpoint remain future work.

## License

MIT — see [LICENSE](LICENSE).
