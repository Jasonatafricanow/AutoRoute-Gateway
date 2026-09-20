"""SQLite runtime state (§14). Scope-bound rows; no secrets ever stored.

Tables:
  provider_health  (provider_id, model, state, reason, cooldown_until, updated_at)
  credential_state (provider_id, credential_id, state, reason, updated_at)
  quota_state      (provider_id, credential_id, model, state, observed_at,
                    cooldown_until, reason, updated_at)
  route_events     (request_id, virtual_model, attempt, provider_id,
                    credential_id, model, error_class, action, latency_ms, ts)
  usage_daily      (optional, §14)

Rows are keyed by scope (provider_id / +credential_id / +model) — key1
exhaustion can never mark the whole provider EXHAUSTED (§9 P0-3).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..domain.state import CredentialState, HealthState, QuotaState, StateSubject


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class SqliteStateStore:
    def __init__(self, path: str | Path):
        self._path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_health (
                    provider_id TEXT NOT NULL,
                    model TEXT,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    cooldown_until TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider_id, model)
                );
                CREATE TABLE IF NOT EXISTS credential_state (
                    provider_id TEXT NOT NULL,
                    credential_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider_id, credential_id)
                );
                CREATE TABLE IF NOT EXISTS quota_state (
                    provider_id TEXT NOT NULL,
                    credential_id TEXT,
                    model TEXT,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL,
                    cooldown_until TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider_id, credential_id, model)
                );
                CREATE TABLE IF NOT EXISTS route_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    request_id TEXT NOT NULL DEFAULT '',
                    virtual_model TEXT NOT NULL DEFAULT '',
                    attempt INTEGER NOT NULL,
                    provider_id TEXT NOT NULL,
                    credential_id TEXT,
                    model TEXT,
                    error_class TEXT,
                    action TEXT,
                    latency_ms REAL,
                    extra TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_route_events_req ON route_events (request_id);
                CREATE TABLE IF NOT EXISTS usage_daily (
                    day TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    credential_id TEXT,
                    requests INTEGER NOT NULL DEFAULT 0,
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (day, provider_id, credential_id)
                );
                """
            )

    # --- health ---
    def get_health(self, subject: StateSubject) -> HealthState:
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM provider_health WHERE provider_id=? AND model IS ?",
                (subject.provider_id, subject.concrete_model),
            ).fetchone()
            if row is None and subject.concrete_model is not None:
                row = self._conn.execute(
                    "SELECT state FROM provider_health WHERE provider_id=? AND model IS NULL",
                    (subject.provider_id,),
                ).fetchone()
        return HealthState(row["state"]) if row else HealthState.UNKNOWN

    def set_health(self, subject, state, reason="", cooldown_until=None):
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO provider_health (provider_id, model, state, reason, cooldown_until, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (provider_id, model) DO UPDATE SET
                    state=excluded.state, reason=excluded.reason,
                    cooldown_until=excluded.cooldown_until, updated_at=excluded.updated_at
                """,
                (
                    subject.provider_id,
                    subject.concrete_model,
                    state.value,
                    reason,
                    cooldown_until.isoformat() if cooldown_until else None,
                    _now(),
                ),
            )

    # --- quota ---
    def get_quota(self, subject: StateSubject) -> QuotaState:
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM quota_state WHERE provider_id=? AND credential_id IS ? AND model IS ?",
                (subject.provider_id, subject.credential_id, subject.concrete_model),
            ).fetchone()
            if row is None and (subject.credential_id is not None or subject.concrete_model is not None):
                row = self._conn.execute(
                    "SELECT state FROM quota_state WHERE provider_id=? AND credential_id IS ? AND model IS NULL",
                    (subject.provider_id, subject.credential_id),
                ).fetchone()
            if row is None and subject.credential_id is not None:
                row = self._conn.execute(
                    "SELECT state FROM quota_state WHERE provider_id=? AND credential_id IS NULL AND model IS NULL",
                    (subject.provider_id,),
                ).fetchone()
        return QuotaState(row["state"]) if row else QuotaState.UNKNOWN

    def set_quota(self, subject, state, reason="", cooldown_until=None):
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO quota_state (provider_id, credential_id, model, state, reason, observed_at, cooldown_until, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (provider_id, credential_id, model) DO UPDATE SET
                    state=excluded.state, reason=excluded.reason,
                    observed_at=excluded.observed_at, cooldown_until=excluded.cooldown_until,
                    updated_at=excluded.updated_at
                """,
                (
                    subject.provider_id,
                    subject.credential_id,
                    subject.concrete_model,
                    state.value,
                    reason,
                    _now(),
                    cooldown_until.isoformat() if cooldown_until else None,
                    _now(),
                ),
            )

    # --- credential ---
    def get_credential(self, subject: StateSubject) -> CredentialState:
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM credential_state WHERE provider_id=? AND credential_id=?",
                (subject.provider_id, subject.credential_id),
            ).fetchone()
        return CredentialState(row["state"]) if row else CredentialState.ENABLED

    def set_credential(self, subject, state, reason=""):
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO credential_state (provider_id, credential_id, state, reason, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (provider_id, credential_id) DO UPDATE SET
                    state=excluded.state, reason=excluded.reason, updated_at=excluded.updated_at
                """,
                (subject.provider_id, subject.credential_id, state.value, reason, _now()),
            )

    # --- cooldown ---
    def get_cooldown(self, subject: StateSubject) -> datetime | None:
        with self._lock:
            h = self._conn.execute(
                "SELECT cooldown_until FROM provider_health WHERE provider_id=? AND model IS ?",
                (subject.provider_id, subject.concrete_model),
            ).fetchone()
            if h is None and subject.concrete_model is not None:
                h = self._conn.execute(
                    "SELECT cooldown_until FROM provider_health WHERE provider_id=? AND model IS NULL",
                    (subject.provider_id,),
                ).fetchone()
            q = self._conn.execute(
                "SELECT cooldown_until FROM quota_state WHERE provider_id=? AND credential_id IS ? AND model IS ?",
                (subject.provider_id, subject.credential_id, subject.concrete_model),
            ).fetchone()
            if q is None and (subject.credential_id is not None or subject.concrete_model is not None):
                q = self._conn.execute(
                    "SELECT cooldown_until FROM quota_state WHERE provider_id=? AND credential_id IS ? AND model IS NULL",
                    (subject.provider_id, subject.credential_id),
                ).fetchone()
            if q is None and subject.credential_id is not None:
                q = self._conn.execute(
                    "SELECT cooldown_until FROM quota_state WHERE provider_id=? AND credential_id IS NULL AND model IS NULL",
                    (subject.provider_id,),
                ).fetchone()
        times = [t for row in (h, q) if row for t in [_parse_dt(row["cooldown_until"])] if t]
        return max(times) if times else None

    # --- route events ---
    def record_route_event(self, event: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO route_events
                    (ts, request_id, virtual_model, attempt, provider_id, credential_id, model, error_class, action, latency_ms, extra)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now(),
                    str(event.get("request_id", "")),
                    str(event.get("virtual_model", "")),
                    int(event.get("attempt", 0)),
                    str(event.get("provider_id", "")),
                    event.get("credential_id"),
                    event.get("model"),
                    event.get("error_class"),
                    event.get("action"),
                    event.get("latency_ms"),
                    json.dumps({k: v for k, v in event.items() if k not in ("ts", "request_id", "virtual_model", "attempt", "provider_id", "credential_id", "model", "error_class", "action", "latency_ms")}, ensure_ascii=False),
                ),
            )

    # --- usage ---
    def add_usage(self, day: str, provider_id: str, credential_id: str | None, tokens_in: int = 0, tokens_out: int = 0) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO usage_daily (day, provider_id, credential_id, requests, tokens_in, tokens_out)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT (day, provider_id, credential_id) DO UPDATE SET
                    requests=requests+1, tokens_in=tokens_in+excluded.tokens_in, tokens_out=tokens_out+excluded.tokens_out
                """,
                (day, provider_id, credential_id, tokens_in, tokens_out),
            )

    # --- status endpoints ---
    def snapshot(self) -> dict:
        with self._lock:
            health = [dict(r) for r in self._conn.execute("SELECT * FROM provider_health ORDER BY provider_id, model").fetchall()]
            quota = [dict(r) for r in self._conn.execute("SELECT * FROM quota_state ORDER BY provider_id, credential_id, model").fetchall()]
            credential = [dict(r) for r in self._conn.execute("SELECT * FROM credential_state ORDER BY provider_id, credential_id").fetchall()]
        return {"health": health, "quota": quota, "credential": credential}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
