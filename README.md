# AutoRoute Gateway

AutoRoute Gateway is an OpenAI-compatible gateway for routing requests across multiple providers, credentials, and models.

The router keeps provider health, credential state, quota, and model capabilities separate so one exhausted key or incompatible model does not incorrectly disable an entire provider.

## Request path

```text
OpenAI-compatible request
        |
        v
virtual model resolution
        |
        v
capability filtering
        |
        v
candidate scoring
        |
        v
ordered provider/credential/model candidates
        |
        v
execute request
        |
        +---- safe fallback before output commit
        |
        +---- stop cross-provider retry after stream commit
        |
        v
client response
```

## Routing model

A route candidate is effectively:

```text
provider + credential + model + capabilities
```

These are not collapsed into one object.

A provider may be healthy while one credential is rate-limited or invalid. A model may be available but lack tools, vision, streaming, or enough context for the request.

Capability checks happen before preference/scoring.

## Failure handling

Errors are classified before fallback.

Examples include:

- authentication failure;
- quota exhaustion;
- rate limiting;
- transport failure;
- provider/server failure;
- partial streaming failure.

Streaming has a hard boundary:

```text
before first client-visible chunk -> another candidate may be tried
after output is committed         -> do not splice in another generation
```

A post-commit failure is surfaced instead of silently creating one response from two different upstream generations.

## Current implementation

The repository includes:

- OpenAI-compatible `/v1/chat/completions`;
- OpenAI-compatible model listing;
- virtual-model resolution;
- provider/credential separation;
- capability-aware filtering;
- dynamic candidate scoring;
- health/quota/credential state;
- classified fallback behavior;
- streaming commit protection;
- health/readiness/provider/route inspection endpoints.

## Verification

```bash
pip install -e ".[dev]"
pytest -q
```

Routing policy is tested separately from live provider transport so capability filtering, credential isolation, fallback, and streaming behavior can be verified without depending on an upstream API.

## Scope

AutoRoute is a routing gateway, not a billing platform or global load balancer. Latency policy, provider cost, and deployment topology remain configuration/deployment concerns.

## Stack

Python 3.11+ · FastAPI · httpx · Pydantic · PyYAML · pytest

## Repository history

The public repository is a cleaned publication of an earlier local project line, so the first public commit is not the beginning of the original development history.
