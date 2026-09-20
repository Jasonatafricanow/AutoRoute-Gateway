"""pytest configuration: works around a sandbox quirk where directories
created via tempfile.mkdtemp are write-denied (same issue the pip shim
solves). Patching mkdtemp makes pytest's tmp_path fixture usable."""

from __future__ import annotations

import os
import random
import string
import tempfile


def _patched_mkdtemp(suffix=None, prefix=None, dir=None):
    base = dir if dir is not None else tempfile.gettempdir()
    name = (prefix or "tmp") + "".join(random.choices(string.ascii_lowercase + string.digits, k=10)) + (suffix or "")
    path = os.path.join(base, name)
    os.makedirs(path, exist_ok=True)
    return path


def pytest_configure(config):
    tempfile.mkdtemp = _patched_mkdtemp  # type: ignore[assignment]
    # Fresh basetemp per run, deliberately NOT created here: if it already
    # exists pytest rm_rf's and recreates it, and the sandbox then denies
    # subdirectory creation inside recreated dirs. A path that does not exist
    # yet is created by pytest itself (mkdir) and stays trusted.
    base = os.path.join(
        os.getcwd(), ".tmp",
        "ptbase-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8)),
    )
    config.option.basetemp = base
    # the sandbox denies scandir on the basetemp at session teardown — skip it
    try:
        import _pytest.tmpdir as _t

        _t.cleanup_dead_symlinks = lambda root: None  # type: ignore[assignment]
    except Exception:  # noqa: BLE001
        pass
