from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .config import RETENTION_SECONDS


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL,
                source TEXT NOT NULL, options TEXT NOT NULL, status TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL,
                lease REAL, owner TEXT, request TEXT, result TEXT NOT NULL DEFAULT '{}', expires_at REAL
            )""")
            columns = {row["name"] for row in database.execute("PRAGMA table_info(jobs)")}
            if "expires_at" not in columns:
                database.execute("ALTER TABLE jobs ADD COLUMN expires_at REAL")
            database.execute(
                "UPDATE jobs SET expires_at=updated+? WHERE expires_at IS NULL "
                "AND status IN ('succeeded','failed','cancelled')", (RETENTION_SECONDS,),
            )
            database.execute("PRAGMA user_version=2")

    @contextmanager
    def connect(self):
        database = sqlite3.connect(self.path, timeout=10)
        database.row_factory = sqlite3.Row
        try:
            with database:
                yield database
        finally:
            database.close()

    @staticmethod
    def decode(row) -> dict:
        result = dict(row)
        result["options"] = json.loads(result["options"])
        result["result"] = json.loads(result["result"])
        return result

    def create(self, title: str, kind: str, source: str, options: dict) -> dict:
        identifier = uuid4().hex
        now = time.time()
        with self.connect() as database:
            database.execute(
                "INSERT INTO jobs(id,title,kind,source,options,status,created,updated) VALUES(?,?,?,?,?,'queued',?,?)",
                (identifier, title, kind, source, json.dumps(options), now, now),
            )
        return self.get(identifier)

    def get(self, identifier: str) -> dict | None:
        with self.connect() as database:
            row = database.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
        return self.decode(row) if row else None

    def list(self) -> list[dict]:
        with self.connect() as database:
            return [self.decode(row) for row in database.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 100")]

    def claim(self, owner: str, identifier: str | None = None) -> dict | None:
        now = time.time()
        with self.connect() as database:
            database.execute("BEGIN IMMEDIATE")
            database.execute(
                "UPDATE jobs SET status='queued', owner=NULL, lease=NULL, message='Recovering interrupted stage' "
                "WHERE status='running' AND lease<?", (now,),
            )
            if database.execute("SELECT 1 FROM jobs WHERE status='running' AND lease>=?", (now,)).fetchone():
                return None
            row = database.execute(
                "SELECT id FROM jobs WHERE status='queued' AND (? IS NULL OR id=?) ORDER BY created LIMIT 1",
                (identifier, identifier),
            ).fetchone()
            if row is None:
                return None
            identifier = row["id"]
            database.execute(
                "UPDATE jobs SET status='running', owner=?, lease=?, updated=? WHERE id=?",
                (owner, now + 30, now, identifier),
            )
        return self.get(identifier)

    def heartbeat(self, identifier: str, owner: str) -> str | None:
        with self.connect() as database:
            row = database.execute("SELECT owner,request,status FROM jobs WHERE id=?", (identifier,)).fetchone()
            if not row or row["owner"] != owner or row["status"] != "running":
                return "lost"
            database.execute("UPDATE jobs SET lease=?,updated=? WHERE id=?", (time.time() + 30, time.time(), identifier))
            return row["request"]

    def progress(self, identifier: str, owner: str, stage: str, progress: float, message: str = "") -> None:
        with self.connect() as database:
            database.execute(
                "UPDATE jobs SET stage=?,progress=?,message=?,updated=? WHERE id=? AND owner=? AND status='running'",
                (stage, progress, message, time.time(), identifier, owner),
            )

    def finish(self, identifier: str, owner: str, status: str, message: str = "", result: dict | None = None) -> None:
        now = time.time()
        expires_at = now + RETENTION_SECONDS if status in {"succeeded", "failed", "cancelled"} else None
        with self.connect() as database:
            database.execute(
                "UPDATE jobs SET status=?,message=?,result=?,request=NULL,owner=NULL,lease=NULL,updated=?, "
                "expires_at=COALESCE(expires_at,?) "
                "WHERE id=? AND owner=? AND status='running'",
                (status, message, json.dumps(result or {}), now, expires_at, identifier, owner),
            )

    def control(self, identifier: str, action: str) -> dict:
        with self.connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute("SELECT status FROM jobs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError(identifier)
            status = row["status"]
            if action in {"pause", "cancel"} and status == "running":
                database.execute("UPDATE jobs SET request=?,updated=? WHERE id=?", (action, time.time(), identifier))
            elif action in {"pause", "cancel"} and status in {"queued", "paused", "failed"}:
                database.execute(
                    "UPDATE jobs SET status=?,request=NULL,updated=?,expires_at=COALESCE(expires_at,?) WHERE id=?",
                    ("paused" if action == "pause" else "cancelled", time.time(),
                     time.time() + RETENTION_SECONDS if action == "cancel" else None, identifier),
                )
            elif action in {"resume", "retry"} and status in {"paused", "failed", "cancelled"}:
                database.execute("UPDATE jobs SET status='queued',request=NULL,message='',updated=? WHERE id=?", (time.time(), identifier))
            else:
                raise ValueError(f"Cannot {action} a {status} job.")
        return self.get(identifier)

    def claim_expired(self, now: float) -> list[dict]:
        with self.connect() as database:
            database.execute("BEGIN IMMEDIATE")
            database.execute(
                "UPDATE jobs SET status='expired',result='{}',request=NULL,owner=NULL,lease=NULL, "
                "message='Seven-day media retention elapsed.' "
                "WHERE expires_at<=? AND status IN ('succeeded','failed','cancelled')", (now,),
            )
            rows = database.execute("SELECT * FROM jobs WHERE status='expired'").fetchall()
        return [self.decode(row) for row in rows]

    def active_sources(self) -> list[str]:
        with self.connect() as database:
            return [row["source"] for row in database.execute(
                "SELECT source FROM jobs WHERE kind='file' AND status IN ('queued','running','paused')"
            )]

    def media_references(self) -> list[dict]:
        with self.connect() as database:
            return [dict(row) for row in database.execute("SELECT id,kind,source FROM jobs")]

    def forget_expired(self, identifier: str) -> None:
        with self.connect() as database:
            database.execute("DELETE FROM jobs WHERE id=? AND status='expired'", (identifier,))

    def remove(self, identifier: str) -> dict:
        with self.connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError(identifier)
            if row["status"] not in {"succeeded", "failed", "cancelled", "expired"}:
                raise ValueError("Cancel active or paused work before removing it.")
            database.execute(
                "UPDATE jobs SET status='expired',result='{}',request=NULL,owner=NULL,lease=NULL, "
                "message='Media removal requested.' WHERE id=?", (identifier,),
            )
        return self.decode(row)