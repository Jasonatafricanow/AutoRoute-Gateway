"""Independent process writers must retain both sets of route events."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from gateway.state.sqlite import SqliteStateStore


def test_two_process_writers_preserve_all_route_events(tmp_path: Path):
    path = tmp_path / "shared.sqlite"
    store = SqliteStateStore(path)
    store.close()
    code = """
import sys
from gateway.state.sqlite import SqliteStateStore
store = SqliteStateStore(sys.argv[1])
try:
    for i in range(100):
        store.record_route_event({'request_id': sys.argv[2] + '-' + str(i), 'virtual_model': 'test', 'attempt': 1, 'provider_id': 'fixture', 'action': 'success'})
finally:
    store.close()
"""
    children = [subprocess.Popen([sys.executable, "-c", code, str(path), prefix], cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for prefix in ("left", "right")]
    try:
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            assert child.returncode == 0, stdout + stderr
    finally:
        for child in children:
            if child.poll() is None:
                child.kill(); child.wait()
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT request_id FROM route_events").fetchall()
    assert {row[0] for row in rows} == {f"{prefix}-{i}" for prefix in ("left", "right") for i in range(100)}
    assert len(rows) == 200
