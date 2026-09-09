from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.subtitle_windows import plan_cue_windows, validate_and_materialize_window

from . import SCHEMA_VERSION
from .storage import atomic_write_json, job_paths, read_json


def _source_cues(job_dir: str | Path) -> list[dict]:
    payload = read_json(job_paths(job_dir)["source"])
    cues = payload.get("segments")
    if not isinstance(cues, list) or not cues:
        raise ValueError("source.json has no subtitle segments.")
    return cues


def plan_translation(
    job_dir: str | Path,
    *,
    max_cues: int = 60,
    max_duration_sec: float = 480.0,
    context_cues: int = 6,
) -> list[dict[str, Any]]:
    paths = job_paths(job_dir)
    manifest = read_json(paths["manifest"])
    cues = _source_cues(job_dir)
    windows = plan_cue_windows(
        cues,
        max_cues=max_cues,
        max_duration_sec=max_duration_sec,
        context_cues=context_cues,
    )
    paths["windows"].mkdir(parents=True, exist_ok=True)
    index_entries = []
    for window in windows:
        window_id = f"{window.index + 1:04d}"
        core = cues[window.core_start:window.core_end]
        context = []
        core_ids = {cue["id"] for cue in core}
        for cue in cues[window.context_start:window.context_end]:
            item = dict(cue)
            item["owned"] = item["id"] in core_ids
            context.append(item)
        source_path = paths["windows"] / f"{window_id}.source.json"
        target_path = paths["windows"] / f"{window_id}.target.json"
        atomic_write_json(source_path, {
            "schema_version": SCHEMA_VERSION,
            "job_id": manifest["job_id"],
            "window_id": window_id,
            "target_language": manifest["target_language"],
            "core_ids": [cue["id"] for cue in core],
            "cues": context,
        })
        if not target_path.exists():
            atomic_write_json(target_path, {
                "schema_version": SCHEMA_VERSION,
                "job_id": manifest["job_id"],
                "window_id": window_id,
                "target_language": manifest["target_language"],
                "cues": [],
            })
        index_entries.append({
            "window_id": window_id,
            "source": source_path.name,
            "target": target_path.name,
            "core_ids": [cue["id"] for cue in core],
        })

    atomic_write_json(paths["windows"] / "index.json", {
        "schema_version": SCHEMA_VERSION,
        "job_id": manifest["job_id"],
        "windows": index_entries,
    })
    if not paths["context"].exists():
        atomic_write_json(paths["context"], {
            "schema_version": SCHEMA_VERSION,
            "job_id": manifest["job_id"],
            "summary": "",
            "speakers": [],
            "terminology": [],
            "style": {
                "target_language": manifest["target_language"],
                "register": "natural spoken subtitles",
                "preserve_names_numbers": True,
            },
        })
    return index_entries


def validate_window(job_dir: str | Path, entry: dict) -> list[dict]:
    paths = job_paths(job_dir)
    manifest = read_json(paths["manifest"])
    source = read_json(paths["windows"] / entry["source"])
    target = read_json(paths["windows"] / entry["target"])
    if target.get("window_id") != entry["window_id"]:
        raise ValueError(f"Window ID mismatch in {entry['target']}.")
    if target.get("job_id") != manifest.get("job_id"):
        raise ValueError(f"Job ID mismatch in {entry['target']}.")
    if target.get("target_language") != manifest.get("target_language"):
        raise ValueError(f"Target language mismatch in {entry['target']}.")
    if target.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Schema version mismatch in {entry['target']}.")
    by_id = {cue["id"]: cue for cue in source["cues"]}
    core = [by_id[cue_id] for cue_id in source["core_ids"]]
    return validate_and_materialize_window(
        target,
        core,
        max_merge_cues=8,
        max_merged_duration_sec=15.0,
        forbid_cross_speaker_merge=True,
        allow_empty_text=True,
    )


def translation_status(job_dir: str | Path) -> dict[str, Any]:
    paths = job_paths(job_dir)
    index_path = paths["windows"] / "index.json"
    if not index_path.exists():
        return {"state": "not_planned", "total": 0, "complete": 0, "pending": [], "invalid": []}
    entries = read_json(index_path).get("windows", [])
    complete = []
    pending = []
    invalid = []
    for entry in entries:
        target = read_json(paths["windows"] / entry["target"])
        if not target.get("cues"):
            pending.append(entry["window_id"])
            continue
        try:
            validate_window(job_dir, entry)
        except (KeyError, TypeError, ValueError) as error:
            invalid.append({"window_id": entry["window_id"], "error": str(error)})
        else:
            complete.append(entry["window_id"])
    state = "complete" if len(complete) == len(entries) else "in_progress"
    return {
        "state": state,
        "total": len(entries),
        "complete": len(complete),
        "pending": pending,
        "invalid": invalid,
    }


def materialize_translation(job_dir: str | Path) -> list[dict]:
    paths = job_paths(job_dir)
    entries = read_json(paths["windows"] / "index.json").get("windows", [])
    materialized = []
    for entry in entries:
        materialized.extend(validate_window(job_dir, entry))
    return materialized


def copy_source_to_targets(job_dir: str | Path) -> int:
    """Fill target windows verbatim when the source captions are already in the target language."""
    paths = job_paths(job_dir)
    manifest = read_json(paths["manifest"])
    if not manifest.get("source_already_target"):
        raise ValueError("Source captions are not marked as matching the target language.")
    index_path = paths["windows"] / "index.json"
    if not index_path.exists():
        plan_translation(job_dir)
    entries = read_json(index_path).get("windows", [])
    for entry in entries:
        source = read_json(paths["windows"] / entry["source"])
        by_id = {cue["id"]: cue for cue in source["cues"]}
        atomic_write_json(paths["windows"] / entry["target"], {
            "schema_version": SCHEMA_VERSION,
            "job_id": manifest["job_id"],
            "window_id": entry["window_id"],
            "target_language": manifest["target_language"],
            "cues": [
                {"source_ids": [cue_id], "text": by_id[cue_id]["text"]}
                for cue_id in source["core_ids"]
            ],
        })
    return len(entries)
