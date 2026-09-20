"""
restart-gateway-launcher.py

Thin Windows launcher: starts the model-gateway Python process
as a fully detached child so that no stdio handles are shared with
the calling RecoveryExecutor subprocess.

Usage (from restart-model-gateway.bat):
    python restart-gateway-launcher.py

The launcher exits immediately after spawning the gateway.
The gateway process has no inherited stdin/stdout/stderr handles,
so the RecoveryExecutor's subprocess.run(..., capture_output=True)
sees EOF immediately and returns without hanging.
"""
from __future__ import annotations

import subprocess
import sys
import os

GATEWAY_ROOT = r"C:\projects\model-gateway"
GATEWAY_PYTHON = os.path.join(GATEWAY_ROOT, r".venv\Scripts\python.exe")
GATEWAY_SCRIPT = os.path.join(GATEWAY_ROOT, "tools", "run_gateway.py")
GATEWAY_CONFIG = os.path.join(GATEWAY_ROOT, "gateway.yaml")
GATEWAY_LOG = os.path.join(GATEWAY_ROOT, "gateway.log")

# Windows process creation flags:
#   CREATE_NEW_PROCESS_GROUP  – prevents Ctrl+C broadcast to the child
#   DETACHED_PROCESS          – detaches from parent's console; child has
#                               no inherited console handles
# Combining them gives us a clean slate: the gateway Python process starts
# with no stdio, stdout or stderr handles borrowed from the recovery cmd.exe.
DETACHED = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

def main() -> None:
    argv = [
        GATEWAY_PYTHON,
        "-m",
        "tools.run_gateway",
        "--config",
        GATEWAY_CONFIG,
    ]
    try:
        # Pass stdin/stdout/stderr as closed (None) so no handles are created.
        # close_fds=True (the default on Windows when stdin/stdout/stderr are
        # None) ensures all inherited FDs are closed in the child.
        subprocess.Popen(
            argv,
            cwd=GATEWAY_ROOT,
            stdin=None,      # no pipe – child has no stdin
            stdout=None,     # no pipe – child writes only to the log file
            stderr=None,     # no pipe
            close_fds=True,
            creationflags=DETACHED,
        )
    except OSError:
        # If the gateway cannot be started, exit non-zero.
        # (Don't write to the log here — the existing gateway may be holding
        # an exclusive handle on it.)
        sys.exit(1)

if __name__ == "__main__":
    main()
