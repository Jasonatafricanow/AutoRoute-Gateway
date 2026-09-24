# AutoRoute-Gateway — Development Record

This document records architecture choices and later corrections. Current code, tests,
configuration and CI take precedence over historical descriptions.

## 1. Provider and credential are separate state

A provider may have multiple credentials with different quota/auth/health state. The
gateway therefore avoids collapsing:

```text
provider = credential = health
```

Credential-scoped failures can remove or back off one credential without automatically
removing every candidate from the same provider.

## 2. Streaming introduced a commit boundary

For non-streaming or not-yet-committed responses, retry/fallback may be legal. Once
substantive output has been emitted to the downstream client, switching upstreams can
produce a mixed response.

The current rule is therefore:

```text
before substantive response commit -> fallback may continue
after response commit              -> surface partial failure
```

A later audit corrected an edge case where role-only/non-substantive stream events could
close the fallback window too early. Regression tests now preserve fallback until
substantive output commits.

## 3. Error classification

Provider-specific failures are normalized into routing-relevant categories such as
authentication failure, rate limiting, quota exhaustion, missing model, context overflow,
server error and timeout. The exact provider mapping remains adapter-specific and should be
validated rather than assumed.

## 4. Optional CLI executor correction

An early design treated the Codex CLI executor as an extreme fallback path. Review showed
that a subprocess executor also inherits execution-context risks such as cwd/environment and
has different operational semantics from an HTTP provider.

The current implementation therefore:

- isolates the executor cwd;
- sanitizes inherited environment;
- requires explicit opt-in;
- excludes Codex CLI from the default route.

It should be treated as an optional adapter, not as a guaranteed availability layer.

## 5. Verification path

The repository evolved through specification, transport capture/capability checks, routing
implementation and scenario/regression tests. The current public CI is the simplest
authority for routine verification:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

For current behavior, prefer the executable tests and `docs/SPEC.md` over older milestone
counts in historical notes.
