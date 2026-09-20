"""pip shim: works around a sandbox quirk where directories created via
tempfile.mkdtemp are write-denied. Re-implements the small tempfile surface
pip uses, with os.makedirs/os.open, then delegates to pip's main().

Usage:
    python tools/pip_shim.py <pip args...>
Example:
    python tools/pip_shim.py install --target .venv/Lib/site-packages fastapi
"""

from __future__ import annotations

import os
import random
import string
import sys
import tempfile


def _rand_name() -> str:
    return "dsh" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


_orig_mkdtemp = tempfile.mkdtemp


def _mkdtemp(suffix=None, prefix=None, dir=None):
    base = dir if dir is not None else tempfile.gettempdir()
    name = (prefix or "") + _rand_name() + (suffix or "")
    path = os.path.join(base, name)
    os.makedirs(path, exist_ok=True)
    return path


def _mkstemp(suffix="", prefix=None, dir=None, text=False):
    base = dir if dir is not None else tempfile.gettempdir()
    name = (prefix or "") + _rand_name() + suffix
    fd = os.open(os.path.join(base, name), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    return fd, os.path.join(base, name)


class _TmpFile:
    """File-like object replacing tempfile.NamedTemporaryFile.

    Delegates every attribute to the real file object (pip/cachecontrol use
    read/write directly, not just via the context manager).
    """

    def __init__(self, file_obj, path, delete=True):
        self._delete = delete
        self.name = path
        self._file = file_obj

    def __getattr__(self, item):
        return getattr(self._file, item)

    def close(self):
        try:
            self._file.close()
        finally:
            if self._delete:
                try:
                    os.unlink(self.name)
                except OSError:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _named_temp_file(mode="w+b", buffering=-1, encoding=None, newline=None, suffix=None, prefix=None, dir=None, delete=True, **kw):
    fd, path = _mkstemp(suffix=suffix or "", prefix=prefix, dir=dir)
    kwargs: dict = {}
    if "b" not in mode:
        if encoding is not None:
            kwargs["encoding"] = encoding
        if newline is not None:
            kwargs["newline"] = newline
    return _TmpFile(os.fdopen(fd, mode, **kwargs), path, delete=delete)


def main() -> int:
    tempfile.mkdtemp = _mkdtemp
    tempfile.mkstemp = _mkstemp
    tempfile.NamedTemporaryFile = _named_temp_file  # type: ignore[assignment]

    from pip._internal.cli.main import main as pip_main

    return pip_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
