"""Run the Phase 1 MVP gateway.

Usage:
    python -m tools.run_gateway --config gateway.yaml [--env .env] [--matrix docs/PROVIDER_CAPABILITY_CONFORMANCE_MATRIX.yaml]
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import uvicorn

from gateway.app import create_app
from gateway.config import apply_capability_matrix, load_capability_matrix, load_gateway_config
from gateway.state.sqlite import SqliteStateStore


def main() -> int:
    parser = argparse.ArgumentParser(description="model-gateway MVP")
    parser.add_argument("--config", default="gateway.yaml")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--matrix", default=None, help="Gate B2 capability conformance matrix (authoritative capability source)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    config, resolver = load_gateway_config(args.config, args.env)
    if args.matrix:
        matrix = load_capability_matrix(args.matrix)
        config = apply_capability_matrix(config, matrix)
    elif config.capability_matrix_path:
        matrix = load_capability_matrix(config.capability_matrix_path)
        config = apply_capability_matrix(config, matrix)

    store = SqliteStateStore(config.server.database_path)
    app = create_app(config, resolver, store)
    host = args.host or config.server.bind
    port = args.port or config.server.port
    print(f"[model-gateway] listening on http://{host}:{port} (db={config.server.database_path})")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
