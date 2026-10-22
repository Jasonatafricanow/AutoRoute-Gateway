# Audit and hardening history

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

Before enabling it, note that `--sandbox read-only` still lets the CLI **read** any file the
gateway's OS user can read, including the `.env` holding upstream keys and the Codex home.
A gateway client can steer that through the prompt, so run the executor under a separate OS
user or container that cannot see the gateway directory.

Without `server.auth.token`, the gateway serves loopback peers only and additionally refuses
requests carrying `Origin` or a non-loopback `Host`, so a web page in a local browser cannot
reach it via cross-origin requests or DNS rebinding. Behind a same-host reverse proxy every
peer looks like loopback: configure a token in that case.


[Current guide](../README.md)
