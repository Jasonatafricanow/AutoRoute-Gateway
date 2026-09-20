"""Domain layer for the model gateway (v0.3.1 frozen baseline).

Frozen decisions implemented here (see baseline doc §3/§4/§9):
- Provider and Credential are two independent first-class entities (no single-key structure).
- VirtualModel (policy intent) != Capability (request requirement).
- RouteCandidate is the routing unit: provider_id + credential_id + concrete_model + capabilities.
- health / quota / credential are three orthogonal state dimensions bound to a StateSubject scope.
"""
