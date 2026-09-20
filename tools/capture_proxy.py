"""Run the Phase 0 recording gateway / capture proxy (§19).

Usage:
    python -m tools.capture_proxy --port 8701 --out capture/real_hermes.jsonl
    python -m tools.capture_proxy --port 8701 --out capture/real_dsh.jsonl --upstream http://127.0.0.1:8700
"""

from __future__ import annotations

import argparse
from datetime import datetime

import uvicorn

from gateway.capture import CaptureRecorder, create_capture_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 0 recording gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8701)
    parser.add_argument("--out", required=True, help="JSONL capture output (schema only, no prompt content)")
    parser.add_argument("--upstream", default=None, help="optional upstream gateway to relay to (capture proxy mode)")
    args = parser.parse_args()

    out_path = args.out.replace("{ts}", datetime.now().strftime("%Y%m%d_%H%M%S"))
    recorder = CaptureRecorder(out_path)
    app = create_capture_app(recorder, args.upstream)
    print(f"[recording-gateway] listening on http://{args.host}:{args.port} → capture: {out_path}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
