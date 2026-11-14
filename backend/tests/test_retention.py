import os
from pathlib import Path
from unittest.mock import patch

import pytest

from clipper.config import Settings
from clipper.retention import ORPHAN_GRACE_SECONDS, prune_expired
from clipper.storage import Store


def completed(settings: Settings, store: Store, source: Path | None = None) -> dict:
    job = store.create("video", "file", str(source or settings.data / "imports/owned.mp4"), {})
    store.claim("worker")
    store.finish(job["id"], "worker", "succeeded", result={"exports": [{"id": "clip-01"}]})
    with store.connect() as database:
        database.execute("UPDATE jobs SET expires_at=100 WHERE id=?", (job["id"],))
    for collection in ("runs", "reviews"):
        folder = settings.data / collection / job["id"]
        folder.mkdir(parents=True)
        (folder / "clip.mp4").write_bytes(b"generated")
    return job


def test_expired_outputs_downloads_and_imported_copy_removed_not_models(tmp_path: Path):
    settings = Settings(tmp_path)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    job = completed(settings, store)
    source = Path(job["source"])
    source.write_bytes(b"imported")
    model = tmp_path / "models/model.bin"
    model.write_bytes(b"keep")
    assert prune_expired(settings, store, now=99)["removed"] == []
    assert source.exists()
    assert prune_expired(settings, store, now=100)["removed"] == [job["id"]]
    assert not (tmp_path / "runs" / job["id"]).exists()
    assert not (tmp_path / "reviews" / job["id"]).exists()
    assert not source.exists()
    assert model.exists()
    assert store.get(job["id"]) is None


def test_external_original_is_never_deleted(tmp_path: Path):
    settings = Settings(tmp_path / "data")
    settings.prepare()
    store = Store(settings.data / "jobs.sqlite3")
    original = tmp_path / "original.mp4"
    original.write_bytes(b"original")
    job = completed(settings, store, original)
    assert prune_expired(settings, store, now=100)["removed"] == [job["id"]]
    assert original.read_bytes() == b"original"


def test_windows_delete_failure_is_retried_and_job_cannot_be_resumed(tmp_path: Path):
    settings = Settings(tmp_path)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    job = completed(settings, store)
    with patch("clipper.retention.shutil.rmtree", side_effect=PermissionError("open file")):
        assert prune_expired(settings, store, now=100)["pending"] == [job["id"]]
    assert store.get(job["id"])["status"] == "expired"
    assert store.get(job["id"])["result"] == {}
    with pytest.raises(ValueError):
        store.control(job["id"], "retry")
    assert prune_expired(settings, store, now=101)["removed"] == [job["id"]]


def test_cleanup_is_not_limited_to_job_list_page(tmp_path: Path):
    settings = Settings(tmp_path)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    for index in range(105):
        job = store.create(str(index), "model", "", {})
        store.control(job["id"], "cancel")
    with store.connect() as database:
        database.execute("UPDATE jobs SET expires_at=1")
    assert len(prune_expired(settings, store, now=2)["removed"]) == 105


def test_active_and_paused_media_remain_even_with_old_deadline(tmp_path: Path):
    settings = Settings(tmp_path)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    running = store.create("running", "file", "one.mp4", {})
    store.claim("worker")
    paused = store.create("paused", "file", "two.mp4", {})
    store.control(paused["id"], "pause")
    for job in (running, paused):
        folder = tmp_path / "runs" / job["id"]
        folder.mkdir(parents=True)
        (folder / "clip.mp4").write_bytes(b"in use")
    with store.connect() as database:
        database.execute("UPDATE jobs SET expires_at=1")
    assert prune_expired(settings, store, now=2)["removed"] == []
    assert all((tmp_path / "runs" / job["id"] / "clip.mp4").exists() for job in (running, paused))


def test_source_referenced_by_another_job_is_not_removed(tmp_path: Path):
    settings = Settings(tmp_path)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    job = completed(settings, store)
    original_clip = tmp_path / "runs" / job["id"] / "clip.mp4"
    dependent = store.create("new benchmark", "file", str(original_clip), {})
    assert prune_expired(settings, store, now=100)["pending"] == [job["id"]]
    assert original_clip.exists()
    store.control(dependent["id"], "cancel")
    assert prune_expired(settings, store, now=100)["removed"] == [job["id"]]


def age_media(path: Path):
    for entry in [*(path.rglob("*") if path.is_dir() else []), path]:
        os.utime(entry, (1, 1))


def test_orphaned_runs_reviews_and_imports_removed_but_other_storage_preserved(tmp_path: Path):
    settings = Settings(tmp_path)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    identifier = "a" * 32
    for collection in ("runs", "reviews"):
        folder = tmp_path / collection / identifier
        folder.mkdir(parents=True)
        (folder / "clip.mp4").write_bytes(b"orphaned media")
        age_media(folder)
    source = tmp_path / "imports" / f"{identifier}.mp4"
    source.write_bytes(b"orphaned import")
    age_media(source)
    preserved = [tmp_path / "models/model.bin", tmp_path / "fixtures/video.mp4",
                 tmp_path / "imports/original.mp4", tmp_path / "imports" / f"{identifier}.part",
                 tmp_path / "imports" / f"{'f' * 32}.mp4" / "notes.txt",
                 tmp_path / "reviews/manual-notes/clip.mp4"]
    for path in preserved:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"keep")
        age_media(path)
    result = prune_expired(settings, store, now=ORPHAN_GRACE_SECONDS + 2)
    assert set(result["orphaned_removed"]) == {f"runs/{identifier}", f"reviews/{identifier}", f"imports/{identifier}.mp4"}
    assert all(not (tmp_path / name).exists() for name in result["orphaned_removed"])
    assert all(path.read_bytes() == b"keep" for path in preserved)


def test_orphan_cleanup_protects_recent_descendants_and_all_registered_references(tmp_path: Path):
    settings = Settings(tmp_path)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    registered = store.create("registered", "file", str(tmp_path / "imports" / f"{'b' * 32}.mp4"), {})
    registered_paths = [tmp_path / "runs" / registered["id"], tmp_path / "reviews" / registered["id"]]
    dependent_source = tmp_path / "runs" / ("c" * 32) / "clip.mp4"
    dependent_source.parent.mkdir(parents=True)
    dependent_source.write_bytes(b"used by registered job")
    store.create("dependent", "file", str(dependent_source), {})
    for path in registered_paths:
        path.mkdir(parents=True)
        (path / "clip.mp4").write_bytes(b"registered output")
        age_media(path)
    imported = Path(registered["source"])
    imported.write_bytes(b"registered source")
    age_media(imported)
    age_media(dependent_source.parent)
    for index in range(105):
        store.create(str(index), "model", "", {})
    recent = tmp_path / "reviews" / ("d" * 32)
    recent.mkdir(parents=True)
    (recent / "new.mp4").write_bytes(b"recent orphan")
    os.utime(recent, (1, 1))
    recent_import = tmp_path / "imports" / f"{'d' * 32}.mp4"
    recent_import.write_bytes(b"new upload awaiting registration")
    assert prune_expired(settings, store, now=ORPHAN_GRACE_SECONDS + 2)["orphaned_removed"] == []
    assert all(path.exists() for path in [*registered_paths, imported, dependent_source, recent / "new.mp4", recent_import])


def test_busy_orphan_cleanup_retries_on_next_sweep(tmp_path: Path):
    settings = Settings(tmp_path)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    folder = tmp_path / "reviews" / ("e" * 32)
    folder.mkdir(parents=True)
    (folder / "preview.mp4").write_bytes(b"locked media")
    age_media(folder)
    with patch("clipper.retention.shutil.rmtree", side_effect=PermissionError("open file")):
        assert prune_expired(settings, store, now=ORPHAN_GRACE_SECONDS + 2)["orphaned_pending"] == [f"reviews/{folder.name}"]
    assert folder.exists()
    assert prune_expired(settings, store, now=ORPHAN_GRACE_SECONDS + 3)["orphaned_removed"] == [f"reviews/{folder.name}"]


def test_orphan_cleanup_refuses_linked_folders(tmp_path: Path):
    settings = Settings(tmp_path)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    folder = tmp_path / "reviews" / ("f" * 32)
    folder.mkdir(parents=True)
    (folder / "original.mp4").write_bytes(b"keep")
    age_media(folder)
    with patch.object(Path, "is_junction", return_value=True, create=True), patch("clipper.retention.shutil.rmtree") as remove:
        assert prune_expired(settings, store, now=ORPHAN_GRACE_SECONDS + 2)["orphaned_pending"] == [f"reviews/{folder.name}"]
    remove.assert_not_called()
    assert (folder / "original.mp4").read_bytes() == b"keep"