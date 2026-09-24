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
  -> candidate scoring
  -> executor / fallback
  -> response commit boundary
```

Three distinctions drive the design:

1. **Provider != Credential**  
   Health/quota/auth state is tracked at credential scope where appropriate. One exhausted
   or invalid key should not automatically disable every credential for the provider.

2. **Capability before scoring**  
   Streaming, tools, vision and context requirements filter incompatible candidates before
   preference/scoring logic is applied.

3. **Pre-commit failure != post-commit failure**  
   Before substantive output is committed to the client, fallback can remain legal. After
   response content is committed, silently switching providers risks constructing a mixed
   response that no upstream produced, so the gateway surfaces a partial-stream failure.

## Audit corrections

A later review found several correctness problems in the first public release. The repair
commit `fix: close SQLite, stream fallback, and Codex executor P1s (#2)` records the
current corrections:

- nullable SQLite scope keys are normalized consistently;
- role-only/non-substantive stream events do not prematurely close the fallback window;
- the optional Codex CLI executor runs with isolated cwd and sanitized inherited environment;
- Codex CLI fallback is **disabled by default** and removed from the default route;
- regressions cover the corrected SQLite, stream and executor boundaries.

The CLI executor remains an explicit opt-in adapter, not a guaranteed last-resort production
path.

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
Candidate Builder / Scorer
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

## Scope / non-claims

This repository demonstrates routing, credential isolation, fallback and streaming commit
semantics under the covered adapters/tests. It does not claim:

- that its scorer is universally optimal across workloads;
- provider-independent behavior that has not been captured/verified for a provider;
- zero-downtime distributed gateway operation;
- that the optional CLI executor should be enabled in a default production route.

## Layout

```text
gateway/     routing/domain/provider/executor/state/API code
tests/       routing, fallback and regression tests
docs/        contracts and capability records
examples/    configuration templates
tools/       runner, smoke and capture utilities
```

Python 3.11+ · FastAPI · httpx · SQLite-backed state · pytest

MIT — see [LICENSE](LICENSE).
