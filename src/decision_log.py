"""The decision log (A8). SQLite, one row per ticket processed.

The Project Brief requires every automated decision to carry the input, the
prediction, the confidence, the sources used, the action taken and the reason
for it, and to be reconstructable months later. A8 is checked by counting
logged decisions against tickets processed, so a row is written for every
ticket including the ones that failed — a log written only for successes has
a visible gap.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Outcome

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    input_path  TEXT,
    output_path TEXT,
    ticket_count INTEGER,
    config      TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    logged_at   TEXT NOT NULL,
    ticket_id   TEXT NOT NULL,
    channel     TEXT,
    input_text  TEXT,
    intent      TEXT,
    urgency     TEXT,
    confidence  REAL,
    sources     TEXT,
    action      TEXT NOT NULL,
    rule        TEXT,
    reason      TEXT,
    response    TEXT,
    citations   TEXT,
    guardrails  TEXT,
    latency_ms  REAL,
    degraded    INTEGER DEFAULT 0,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions(run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_run_ticket ON decisions(run_id, ticket_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DecisionLog:
    def __init__(self, path: str = "storage/decisions.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.run_id: str | None = None

    def start_run(self, *, input_path: str, output_path: str, config: dict) -> str:
        self.run_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            "INSERT INTO runs (run_id, started_at, input_path, output_path, config)"
            " VALUES (?,?,?,?,?)",
            (self.run_id, _now(), str(input_path), str(output_path), json.dumps(config)),
        )
        self.conn.commit()
        return self.run_id

    def record(self, outcome: Outcome, input_text: str) -> None:
        # INSERT OR REPLACE keeps the unique (run_id, ticket_id) guarantee even
        # if the same file contains a duplicate ticket_id, so the reconciliation
        # in A8 stays meaningful rather than double-counting.
        self.conn.execute(
            "INSERT OR REPLACE INTO decisions (run_id, logged_at, ticket_id, channel,"
            " input_text, intent, urgency, confidence, sources, action, rule, reason,"
            " response, citations, guardrails, latency_ms, degraded, error)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.run_id, _now(), outcome.ticket_id, outcome.channel,
                input_text[:4000], outcome.intent, outcome.urgency, outcome.confidence,
                json.dumps(outcome.sources), outcome.action, outcome.rule, outcome.reason,
                outcome.response, json.dumps(outcome.citations),
                json.dumps(outcome.guardrail_findings), outcome.latency_ms,
                int(outcome.degraded), outcome.error,
            ),
        )

    def finish_run(self, ticket_count: int) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, ticket_count=? WHERE run_id=?",
            (_now(), ticket_count, self.run_id),
        )
        self.conn.commit()

    def count(self, run_id: str | None = None) -> int:
        rid = run_id or self.run_id
        return self.conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE run_id=?", (rid,)
        ).fetchone()[0]

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
