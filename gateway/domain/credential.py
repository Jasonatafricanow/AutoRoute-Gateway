"""Credential entity (§3/§10). First-class, independent of Provider."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Credential:
    """One API key / credential bound to one provider.

    The secret value itself is resolved lazily from the environment at
    request time (see SecretResolver) and must never enter logs or state.
    """

    credential_id: str
    provider_id: str
    #: environment variable holding the secret (e.g. OPENCODE_KEY_1)
    env_var: str
    #: sort priority within the provider's key pool (higher = preferred)
    priority: int = 100
    #: reserve keys stay out of normal rotation; take over when primaries fail
    reserve: bool = False
