# AutoRoute Gateway

**A capability-aware, multi-provider, multi-credential LLM gateway.**

AutoRoute Gateway exposes an OpenAI-compatible surface while treating model routing as a stateful decision problem rather than a static reverse proxy.

Its central design question is:

> When several providers, models, and credentials are available, what may safely fail over — and what must become irreversible once output has reached the client?

## Portfolio role

This project belongs to the infrastructure layer of the portfolio.

```text
Agent / Application
        |
        v
 AutoRoute Gateway
   |     |     |
   v     v     v
Provider / Credential / Model candidates
```

It complements [LocalModelService](https://github.com/Jasonatafricanow/LocalModelService): LocalModelService standardizes a local inference surface; AutoRoute handles selection and failure across heterogeneous upstreams.

## The problem

A basic gateway can map one model name to one upstream URL. That model breaks down when real deployments contain:

- several providers with overlapping capabilities;
- several credentials under one provider;
- quota and rate-limit failures that apply to one key but not another;
- models that differ in tool, vision, streaming, or context support;
- partial streamed responses that cannot be replayed safely.

The routing unit therefore cannot simply be "provider".

## How the design evolved

### 1. Provider routing was too coarse

A provider can remain healthy while one credential is exhausted or invalid. Treating provider and credential as the same object turns a local key failure into a global provider failure.

The architecture separates them as first-class entities.

### 2. Model names were not enough

A request is not only asking for a name. It may require tools, vision, streaming, or a minimum context window.

Routing therefore starts with a capability gate before scoring candidates.

### 3. Streaming changed fallback semantics

Before the first downstream chunk is committed, retrying another candidate can be safe. After output has been exposed to the client, switching providers risks producing one response assembled from two unrelated generations.

This creates a commit boundary:

```text
before first committed chunk -> fallback may continue
after first committed chunk  -> no cross-provider replay
```

A partial stream failure is surfaced explicitly instead of being hidden behind unsafe retry.

## Key design decisions

### Provider != Credential

Health, quota, and credential state are tracked independently. A bad key does not automatically poison every route through that provider.

### Capability before preference

Candidates that cannot satisfy the request are removed before scoring. Preference cannot override incompatibility.

### Routing state is scoped

Health, quota, and credential state are attached to explicit subjects rather than stored as one undifferentiated global flag.

### Fallback is error-aware

Rate limits, quota exhaustion, authentication errors, transport failures, and partial-stream failures have different recovery semantics.

### The client contract is stable

The gateway exposes OpenAI-compatible chat-completion and model-list endpoints so applications are not tightly coupled to the internal routing policy.

## Request path

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
Ordered Candidate Queue
        |
        v
Executor + Error Classifier
        |
        +---- safe pre-commit fallback
        |
        +---- streaming commit guard
        |
        v
Client response
```

## Current scope

The repository includes:

- virtual-model resolution;
- capability-aware candidate filtering;
- multi-provider / multi-credential routing;
- candidate scoring;
- quota, health, and credential state;
- fallback by classified failure type;
- streaming commit protection;
- OpenAI-compatible `/v1/chat/completions` and `/v1/models`;
- health, readiness, provider, and route inspection endpoints.

## Verification

The project uses pytest / pytest-asyncio and keeps routing policy separate from transport execution so the decision layer can be tested without a live provider.

```bash
pip install -e ".[dev]"
pytest -q
```

## Boundaries and non-claims

AutoRoute Gateway is not presented as:

- an internet-scale global load balancer;
- a billing or metering platform;
- a provider-quality oracle;
- proof that one scoring policy is optimal for every workload.

The architecture focuses on routing correctness and failure boundaries. Real latency, provider reliability, cost policy, and deployment topology remain environment-specific.

## Stack

Python 3.11+ · FastAPI · httpx · Pydantic · PyYAML · pytest

## Repository history

This public repository is a cleaned publication of an earlier local project line. The public Git history begins at the publication baseline and should not be interpreted as the complete development timeline.

## Engineering philosophy

Fallback is useful only while the system can still preserve one coherent response.

The gateway therefore treats **capability, state scope, credential identity, and commit boundaries** as more important than aggressive retry.
