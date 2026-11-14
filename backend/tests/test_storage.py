import time
from pathlib import Path

import pytest

from clipper.domain import RunOptions, URLInput
from clipper.storage import Store
from clipper.config import RETENTION_SECONDS
from unittest.mock import patch


def test_single_active_worker_and_recovery(tmp_path: Path):
    store = Store(tmp_path / "jobs.db")
    first = store.create("first", "file", "one.mp4", {})
    store.create("second", "file", "two.mp4", {})
    assert store.claim("worker-a")["id"] == first["id"]
    assert store.claim("worker-b") is None
    with store.connect() as database:
        database.execute("UPDATE jobs SET lease=? WHERE id=?", (time.time() - 1, first["id"]))
    assert store.claim("worker-b")["id"] == first["id"]
    assert store.heartbeat(first["id"], "worker-a") == "lost"
    store.finish(first["id"], "worker-a", "succeeded")
    assert store.get(first["id"])["status"] == "running"


def test_pause_resume_and_cancel_are_persistent(tmp_path: Path):
    store = Store(tmp_path / "jobs.db")
    job = store.create("test", "file", "source.mp4", {})
    store.control(job["id"], "pause")
    assert store.claim("worker") is None
    store.control(job["id"], "resume")
    store.claim("worker")
    store.control(job["id"], "cancel")
    assert store.heartbeat(job["id"], "worker") == "cancel"
    store.finish(job["id"], "worker", "cancelled")
    assert Store(store.path).get(job["id"])["status"] == "cancelled"


@pytest.mark.parametrize("url", [
    "http://youtube.com/watch?v=abc", "https://youtube.com.evil.test/watch?v=abc",
    "https://127.0.0.1/video", "https://twitch.tv/channel", "https://youtube.com/playlist?list=a",
    "https://user:password@youtube.com/watch?v=abc",
])
def test_url_allowlist(url):
    with pytest.raises(ValueError):
        URLInput(url=url)


def test_safe_defaults():
    assert RunOptions().profile == "quiet"
    assert RunOptions().captions
    assert URLInput(url="https://www.twitch.tv/videos/123").url.endswith("123")


def test_expiry_starts_at_completion_and_retry_does_not_extend_it(tmp_path: Path):
    store = Store(tmp_path / "jobs.db")
    job = store.create("video", "file", "one.mp4", {})
    assert job["expires_at"] is None
    store.claim("worker")
    with patch("clipper.storage.time.time", return_value=1000):
        store.finish(job["id"], "worker", "failed")
    expiry = 1000 + RETENTION_SECONDS
    assert store.get(job["id"])["expires_at"] == expiry
    store.control(job["id"], "retry")
    store.claim("worker")
    store.finish(job["id"], "worker", "succeeded")
    assert store.get(job["id"])["expires_at"] == expiry


def test_expiry_claim_prevents_requeue_and_leaves_active_work_alone(tmp_path: Path):
    store = Store(tmp_path / "jobs.db")
    job = store.create("completed", "file", "one.mp4", {})
    store.claim("worker")
    store.finish(job["id"], "worker", "succeeded", result={"exports": [{"id": "clip-01"}]})
    paused = store.create("paused", "file", "two.mp4", {})
    store.control(paused["id"], "pause")
    with store.connect() as database:
        database.execute("UPDATE jobs SET expires_at=1")
    expired = store.claim_expired(2)
    assert [item["id"] for item in expired] == [job["id"]]
    assert expired[0]["result"] == {}
    assert store.get(paused["id"])["status"] == "paused"
    with pytest.raises(ValueError):
        store.control(job["id"], "retry")


def test_existing_database_migrates_without_changing_original_expiry(tmp_path: Path):
    import sqlite3
    path = tmp_path / "jobs.db"
    with sqlite3.connect(path) as database:
        database.execute("""CREATE TABLE jobs (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
            options TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'queued',
            progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
            updated REAL NOT NULL, lease REAL, owner TEXT, request TEXT, result TEXT NOT NULL DEFAULT '{}'
        )""")
        database.execute("INSERT INTO jobs(id,title,kind,source,options,status,created,updated) "
                         "VALUES('old','video','file','original.mp4','{}','succeeded',10,20)")
    assert Store(path).get("old")["expires_at"] == 20 + RETENTION_SECONDS
    assert Store(path).get("old")["expires_at"] == 20 + RETENTION_SECONDS