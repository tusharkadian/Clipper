from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipper.api import create_app
from clipper.config import Settings


def client(tmp_path: Path):
    result = TestClient(create_app(Settings(tmp_path, reserve_bytes=0)))
    token = result.get("/api/session").json()["token"]
    result.headers["x-clipper-token"] = token
    return result


def test_local_session_and_origin_required(tmp_path):
    api = client(tmp_path)
    assert api.get("/api/jobs").status_code == 200
    assert api.post("/api/model", headers={"origin": "https://evil.example"}).status_code == 403
    api.headers.pop("x-clipper-token")
    assert api.get("/api/jobs").status_code == 403


def test_upload_stores_controlled_filename_and_options(tmp_path):
    api = client(tmp_path)
    response = api.post("/api/uploads", files={"file": ("../../test.mp4", b"fixture", "video/mp4")})
    assert response.status_code == 201
    job = response.json()
    assert Path(job["source"]).parent == tmp_path / "imports"
    assert job["options"]["captions"]
    assert api.post(f"/api/jobs/{job['id']}/pause").json()["status"] == "paused"
    assert api.get(f"/api/jobs/{job['id']}/files/clip-01/clip.mp4").status_code == 404


def test_invalid_sources_do_not_create_jobs(tmp_path):
    api = client(tmp_path)
    assert api.post("/api/sources", json={"url": "https://127.0.0.1/private"}).status_code == 422
    assert api.post("/api/uploads", files={"file": ("code.py", b"print('no')")}).status_code == 422
    assert api.get("/api/jobs").json() == []


def test_removal_preserves_external_original_and_blocks_active_job(tmp_path):
    from clipper.storage import Store
    api = client(tmp_path)
    original = tmp_path / "user-original.mp4"
    original.write_bytes(b"owned recording")
    store = Store(tmp_path / "jobs.sqlite3")
    job = store.create("Original", "file", str(original), {})
    assert api.delete(f"/api/jobs/{job['id']}").status_code == 409
    store.control(job["id"], "cancel")
    assert api.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert original.exists()


@pytest.mark.parametrize("kind", ["file", "url"])
def test_removal_deletes_managed_source_exports_and_reviews_from_disk(tmp_path, kind):
    from clipper.storage import Store
    api = client(tmp_path)
    store = Store(tmp_path / "jobs.sqlite3")
    original = tmp_path / "original.mp4"
    original.write_bytes(b"external original")
    model = tmp_path / "models/model.bin"
    model.write_bytes(b"retained model")
    if kind == "file":
        response = api.post("/api/uploads", files={"file": ("recording.mp4", original.read_bytes(), "video/mp4")})
    else:
        response = api.post("/api/sources", json={"url": "https://youtu.be/abcdefghijk"})
    assert response.status_code == 201
    job = response.json()
    run = tmp_path / "runs" / job["id"]
    clip = run / "clip-01"
    clip.mkdir(parents=True)
    (clip / "clip.mp4").write_bytes(b"export")
    (run / "speech.flac").write_bytes(b"cached audio")
    source = Path(job["source"]) if kind == "file" else run / "source.mkv"
    if kind == "url":
        source.write_bytes(b"downloaded source")
    review = tmp_path / "reviews" / job["id"]
    review.mkdir(parents=True)
    (review / "preview.mp4").write_bytes(b"review")
    store.control(job["id"], "cancel")

    assert api.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert not source.exists()
    assert not run.exists()
    assert not review.exists()
    assert store.get(job["id"]) is None
    assert original.read_bytes() == b"external original"
    assert model.read_bytes() == b"retained model"


def test_expired_clip_is_denied_before_next_cleanup_sweep(tmp_path):
    from clipper.storage import Store
    api = client(tmp_path)
    store = Store(tmp_path / "jobs.sqlite3")
    job = store.create("expired", "file", "original.mp4", {})
    store.claim("worker")
    store.finish(job["id"], "worker", "failed", result={"exports": [{"id": "clip-01"}]})
    folder = tmp_path / "runs" / job["id"] / "clip-01"
    folder.mkdir(parents=True)
    (folder / "clip.mp4").write_bytes(b"video")
    with store.connect() as database:
        database.execute("UPDATE jobs SET expires_at=1 WHERE id=?", (job["id"],))
    assert api.get(f"/api/jobs/{job['id']}/files/clip-01/clip.mp4").status_code == 410
    assert api.post(f"/api/jobs/{job['id']}/retry").status_code == 410
    assert api.get("/api/jobs").json()[0]["result"] == {}
    assert api.get("/api/system").json()["retention_days"] == 7
    assert (folder / "clip.mp4").exists()


def test_api_startup_removes_old_owned_outputs(tmp_path):
    from clipper.storage import Store
    store = Store(tmp_path / "jobs.sqlite3")
    job = store.create("expired", "file", str(tmp_path / "imports/copy.mp4"), {})
    store.control(job["id"], "cancel")
    folder = tmp_path / "runs" / job["id"]
    folder.mkdir(parents=True)
    (folder / "clip.mp4").write_bytes(b"video")
    with store.connect() as database:
        database.execute("UPDATE jobs SET expires_at=1 WHERE id=?", (job["id"],))
    api = client(tmp_path)
    assert api.get("/api/jobs").json() == []
    assert not folder.exists()


def test_busy_manual_removal_is_tracked_for_cleanup_retry(tmp_path):
    from unittest.mock import patch
    from clipper.retention import prune_expired
    from clipper.storage import Store
    api = client(tmp_path)
    store = Store(tmp_path / "jobs.sqlite3")
    job = store.create("video", "file", "original.mp4", {})
    store.control(job["id"], "cancel")
    folder = tmp_path / "runs" / job["id"]
    folder.mkdir(parents=True)
    with patch("clipper.retention.shutil.rmtree", side_effect=PermissionError("open file")):
        assert api.delete(f"/api/jobs/{job['id']}").status_code == 409
    assert store.get(job["id"])["status"] == "expired"
    assert prune_expired(Settings(tmp_path), store)["removed"] == [job["id"]]
    assert not folder.exists()


def test_reranker_install_is_explicit_and_duplicate_requests_are_blocked(tmp_path):
    api = client(tmp_path)
    assert not api.get("/api/system").json()["reranker_ready"]
    response = api.post("/api/reranker/model")
    assert response.status_code == 201
    assert response.json()["kind"] == "model"
    assert response.json()["source"] == "qwen-reranker"
    assert api.post("/api/reranker/model").status_code == 409


def test_webcam_layout_allows_full_gameplay_without_forced_camera_exclusion(tmp_path):
    from clipper.domain import RunOptions
    assert RunOptions().webcam_layout is None
    api = client(tmp_path)
    layout = {"webcam": {"x": 0.8, "y": 0, "width": 0.2, "height": 0.25},
              "gameplay": {"x": 0, "y": 0, "width": 0.8, "height": 1}, "top_fraction": 0.3}
    response = api.post("/api/sources", json={"url": "https://youtu.be/abcdefghijk", "options": {"webcam_layout": layout}})
    assert response.status_code == 201
    assert response.json()["options"]["webcam_layout"] == {**layout, "webcam_zoom": 1, "gameplay_fit": "fit"}
    layout["gameplay"]["width"] = 1
    response = api.post("/api/sources", json={"url": "https://youtu.be/abcdefghijk", "options": {"webcam_layout": layout}})
    assert response.status_code == 201
    assert response.json()["options"]["webcam_layout"]["gameplay"]["width"] == 1
    assert RunOptions(webcam_layout={"webcam": layout["webcam"]}).webcam_layout.gameplay.model_dump() == {
        "x": 0, "y": 0, "width": 1, "height": 1}
    layout.update(webcam_zoom=1.75, gameplay_fit="fit")
    response = api.post("/api/sources", json={"url": "https://youtu.be/abcdefghijk", "options": {"zoom": 1.25, "webcam_layout": layout}})
    assert response.status_code == 201
    assert response.json()["options"]["webcam_layout"] == layout
    assert response.json()["options"]["zoom"] == 1.25


@pytest.mark.parametrize("field,value", [("webcam_zoom", 0.9), ("webcam_zoom", 2.1),
    ("webcam_zoom", float("nan")), ("webcam_zoom", float("inf")), ("gameplay_fit", "stretch")])
def test_invalid_webcam_zoom_or_framing_is_rejected(field, value):
    from pydantic import ValidationError
    from clipper.domain import WebcamLayout
    with pytest.raises(ValidationError):
        WebcamLayout.model_validate({"webcam": {"x": 0.8, "y": 0, "width": 0.2, "height": 0.25}, field: value})


@pytest.mark.parametrize("region", [
    {"x": 0.9, "y": 0, "width": 0.2, "height": 0.25},
    {"x": 0, "y": 0, "width": 0.001, "height": 0.25},
    {"x": float("nan"), "y": 0, "width": 0.2, "height": 0.25},
])
def test_invalid_webcam_crop_is_rejected(region):
    from pydantic import ValidationError
    from clipper.domain import SourceRegion
    with pytest.raises(ValidationError):
        SourceRegion.model_validate(region)