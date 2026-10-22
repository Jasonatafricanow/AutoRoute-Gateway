# AutoRoute-Gateway (v0.3.1)

AutoRoute-Gateway is a capability-aware LLM gateway for routing requests across multiple
providers and credentials while preserving explicit failure and response-commit semantics.

The project is primarily about **routing correctness under partial failure**, not about
claiming that one scoring formula can always choose the objectively best model.

## Core boundaries

```text
request
  -> virtual model / capability requirements
  -> compatible provider+credential candidates
  -> configured candidate order (scores are diagnostic)
  -> executor / fallback
  -> response commit boundary
```

Three distinctions drive the design:

1. **Provider != Credential**
   Health/quota/auth state is tracked at credential scope where appropriate. One exhausted
   or invalid key should not automatically disable every credential for the provider.

2. **Capability before configured-order fallback**
   Streaming, tools, vision and context requirements filter incompatible candidates before
   the configured-order fallback chain is executed.

3. **Pre-commit failure != post-commit failure**
   Before substantive output is committed to the client, fallback can remain legal. After
   response content is committed, silently switching providers risks constructing a mixed
   response that no upstream produced, so the gateway surfaces a partial-stream failure.

## Request flow

```text
OpenAI-compatible request
        |
        v
Virtual Model Resolver
        |
        v
Capability Gate
        |
        v
Candidate Builder / Configured Order
        |
        v
Executor + Fallback Engine
        |
        +-- failure before response commit --> next legal candidate
        |
        +-- failure after response commit ---> PARTIAL_STREAM_FAILURE
```

## Verification

GitHub Actions installs the package with development dependencies and runs `pytest -q` on
pushes and pull requests.

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

The repository also contains:

- `docs/SPEC.md` — routing/domain contract;
- `docs/REAL_TRANSPORT_CONTRACT.md` — captured transport assumptions;
- `docs/PROVIDER_CAPABILITY_CONFORMANCE_MATRIX.yaml` — capability records;
- `tools/smoke_test.py` and `tools/capture_proxy.py` — verification utilities.

## Run

```powershell
Copy-Item examples\gateway.yaml.example gateway.yaml
Copy-Item examples\.env.example .env
python -m tools.run_gateway --config gateway.yaml --env .env
```

Default service binding is loopback.

Main endpoints:

- `POST /v1/chat/completions`
- `GET /v1/models`
- `GET /health`
- `GET /ready`
- `GET /providers`
- `GET /routes`

## Layout

```text
gateway/     routing/domain/provider/executor/state/API code
tests/       routing, fallback and regression tests
docs/        contracts and capability records
examples/    configuration templates
tools/       runner, smoke and capture utilities
```

Python 3.11+ · FastAPI · httpx · SQLite-backed state · pytest

MIT — see [LICENSE](../LICENSE).
