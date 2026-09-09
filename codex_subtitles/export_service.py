from __future__ import annotations

from pathlib import Path

from utils import subtitle_formatter

from . import SCHEMA_VERSION
from .storage import atomic_write_json, job_paths, read_json, safe_name
from .translation_service import materialize_translation


def _merged_originals(source_cues: list[dict], translated: list[dict]) -> list[dict]:
    by_id = {cue["id"]: cue for cue in source_cues}
    originals = []
    for item in translated:
        sources = [by_id[source_id] for source_id in item["source_ids"]]
        originals.append({
            "start": item["start"],
            "end": item["end"],
            "text": " ".join(source["text"].strip() for source in sources if source["text"].strip()),
        })
    return originals


def export_job(job_dir: str | Path) -> dict[str, str | int]:
    paths = job_paths(job_dir)
    manifest = read_json(paths["manifest"])
    source_cues = read_json(paths["source"])["segments"]
    translated_all = materialize_translation(job_dir)
    translated = [item for item in translated_all if not item.get("dropped")]
    if not translated:
        raise ValueError("The translated subtitle would be empty.")
    originals = _merged_originals(source_cues, translated)

    paths["final"].mkdir(parents=True, exist_ok=True)
    target_tag = safe_name(manifest["target_language"], fallback="target")
    translated_path = paths["final"] / f"translated.{target_tag}.srt"
    bilingual_path = paths["final"] / f"bilingual.{target_tag}.srt"
    subtitle_formatter.generate_srt(translated, str(translated_path))
    subtitle_formatter.generate_bilingual_srt(originals, translated, str(bilingual_path))

    result_path = paths["final"] / "result.json"
    atomic_write_json(result_path, {
        "schema_version": SCHEMA_VERSION,
        "job_id": manifest["job_id"],
        "source_cue_count": len(source_cues),
        "translated_cue_count": len(translated),
        "dropped_source_groups": sum(1 for item in translated_all if item.get("dropped")),
        "translated_srt": str(translated_path),
        "bilingual_srt": str(bilingual_path),
    })
    return {
        "translated_srt": str(translated_path),
        "bilingual_srt": str(bilingual_path),
        "result_json": str(result_path),
        "translated_cue_count": len(translated),
    }

