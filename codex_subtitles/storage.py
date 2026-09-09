from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION


_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_name(value: str | None, fallback: str = "video", limit: int = 100) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    if not cleaned or not any(char.isalnum() for char in cleaned):
        cleaned = fallback
    return cleaned[:limit].rstrip(" ._") or fallback


def stable_id(value: str, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def read_json(path: str | os.PathLike[str]) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def atomic_write_json(path: str | os.PathLike[str], payload: dict[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def job_paths(job_dir: str | os.PathLike[str]) -> dict[str, Path]:
    root = Path(job_dir).resolve()
    return {
        "root": root,
        "manifest": root / "job.json",
        "source": root / "source.json",
        "context": root / "context.json",
        "windows": root / "windows",
        "final": root / "final",
        "artifacts": root / "artifacts",
    }


def create_job_dir(
    output_root: str | os.PathLike[str], title: str, video_id: str, target_language: str
) -> Path:
    title_part = safe_name(title, fallback=video_id, limit=72)
    target_part = safe_name(target_language, fallback="target", limit=28)
    return Path(output_root).resolve() / f"{title_part}.{safe_name(video_id)}.{target_part}"


def new_manifest(
    *,
    job_dir: str | os.PathLike[str],
    url: str,
    title: str,
    video_id: str,
    target_language: str,
    source_language: str | None,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": stable_id(f"{video_id}\0{target_language}"),
        "url": url,
        "title": title,
        "video_id": video_id,
        "source_language": source_language,
        "target_language": target_language,
        "source_kind": None,
        "source_already_target": False,
        "status": "created",
        "created_at": now,
        "updated_at": now,
        "job_dir": str(Path(job_dir).resolve()),
        "paths": {},
    }


def update_manifest(job_dir: str | os.PathLike[str], **changes: Any) -> dict[str, Any]:
    paths = job_paths(job_dir)
    manifest = read_json(paths["manifest"])
    manifest.update(changes)
    manifest["updated_at"] = utc_now()
    atomic_write_json(paths["manifest"], manifest)
    return manifest

