from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

from .config import Settings
from .storage import Store

ORPHAN_GRACE_SECONDS = 60 * 60


def prune_orphaned(settings: Settings, store: Store, now: float) -> dict:
    removed, pending = [], []
    for collection in ("runs", "reviews", "imports"):
        root = settings.data / collection
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if collection == "imports":
                if not re.fullmatch(r"[a-f0-9]{32}\.(mp4|mkv|mov|webm|avi|m4v|ts)", path.name):
                    continue
            elif not re.fullmatch(r"[a-f0-9]{32}", path.name):
                continue
            name = f"{collection}/{path.name}"
            try:
                if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    raise OSError("Refusing to traverse linked orphan media.")
                if (collection == "imports" and not path.is_file()) or (collection != "imports" and not path.is_dir()):
                    continue
                resolved = path.resolve()
                if resolved.parent != root.resolve():
                    raise OSError("Orphan media is outside managed storage.")
                references = store.media_references()
                if collection != "imports" and any(job["id"] == path.name for job in references):
                    continue
                protected = [Path(job["source"]).resolve() for job in references if job["kind"] == "file"]
                if any(source == resolved or source.is_relative_to(resolved) for source in protected):
                    continue
                recent = path.stat().st_mtime > now - ORPHAN_GRACE_SECONDS
                if path.is_dir():
                    for entry in path.rglob("*"):
                        if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                            raise OSError("Refusing to traverse linked orphan media.")
                        if entry.stat().st_mtime > now - ORPHAN_GRACE_SECONDS:
                            recent = True
                            break
                if recent:
                    continue
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed.append(name)
            except (OSError, ValueError):
                pending.append(name)
                logging.getLogger(__name__).warning("Orphan media cleanup deferred for %s; retrying next sweep.", name)
    return {"removed": removed, "pending": pending}


def remove_owned_media(settings: Settings, job: dict) -> None:
    identifier = job["id"]
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise ValueError("Invalid managed job directory.")
    for collection in ("runs", "reviews"):
        root = settings.data / collection
        directory = root / identifier
        if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
            raise OSError("Refusing to traverse a linked job directory.")
        if directory.exists():
            if directory.resolve().parent != root.resolve():
                raise OSError("Job directory is outside managed storage.")
            shutil.rmtree(directory)
    if job["kind"] == "file":
        source = Path(job["source"]).absolute()
        imports = (settings.data / "imports").absolute()
        if source.parent == imports:
            source.unlink(missing_ok=True)


def prune_expired(settings: Settings, store: Store, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    expired = store.claim_expired(now)
    removed, pending = [], []
    for job in expired:
        try:
            run = (settings.data / "runs" / job["id"]).resolve()
            review = (settings.data / "reviews" / job["id"]).resolve()
            imported = Path(job["source"]).resolve() if job["kind"] == "file" else None
            protected = [Path(source).resolve() for source in store.active_sources()]
            if any(source.is_relative_to(run) or source.is_relative_to(review) or source == imported for source in protected):
                pending.append(job["id"])
                continue
            remove_owned_media(settings, job)
            store.forget_expired(job["id"])
            removed.append(job["id"])
        except (OSError, ValueError):
            pending.append(job["id"])
            logging.getLogger(__name__).warning("Expired media cleanup deferred for job %s; retrying next sweep.", job["id"])
    orphans = prune_orphaned(settings, store, now)
    return {"removed": removed, "pending": pending,
            "orphaned_removed": orphans["removed"], "orphaned_pending": orphans["pending"]}