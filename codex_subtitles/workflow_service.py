from __future__ import annotations

import shutil
from pathlib import Path

from utils import audio_enhancer

from .local_asr_service import transcribe_local
from .source_service import load_subtitle, write_source
from .storage import atomic_write_json, create_job_dir, job_paths, new_manifest, read_json, stable_id, update_manifest
from .translation_service import plan_translation
from .video_service import download_audio, download_caption, inspect_video, select_caption


def prepare_youtube_job(
    url: str,
    *,
    target_language: str = "Simplified Chinese",
    source_language: str | None = None,
    output_root: str | Path = "output/codex_native",
    force_audio: bool = False,
) -> dict:
    info = inspect_video(url)
    video_id = str(info.get("id") or stable_id(url))
    title = str(info.get("title") or video_id)
    job_dir = create_job_dir(output_root, title, video_id, target_language)
    paths = job_paths(job_dir)
    for key in ("root", "artifacts", "windows", "final"):
        paths[key].mkdir(parents=True, exist_ok=True)
    manifest = new_manifest(
        job_dir=job_dir,
        url=url,
        title=title,
        video_id=video_id,
        target_language=target_language,
        source_language=source_language,
    )
    atomic_write_json(paths["manifest"], manifest)

    choice = None if force_audio else select_caption(
        info, target_language=target_language, source_language=source_language
    )
    if choice:
        subtitle_path = download_caption(url, choice, paths["artifacts"] / "source")
        segments = load_subtitle(subtitle_path)
        write_source(
            paths["source"],
            segments,
            language=choice.language,
            source_kind=f"youtube_{choice.kind}_caption",
        )
        manifest = update_manifest(
            job_dir,
            source_kind=f"youtube_{choice.kind}_caption",
            source_language=choice.language,
            source_already_target=choice.already_target,
            status="source_ready",
            paths={
                "source": str(paths["source"]),
                "source_artifact": str(subtitle_path),
            },
        )
    else:
        audio_path = download_audio(url, paths["artifacts"] / "audio")
        manifest = update_manifest(
            job_dir,
            source_kind="audio",
            status="needs_local_asr",
            paths={"audio": str(audio_path)},
        )
    return manifest


def import_source_job(
    subtitle_path: str | Path,
    *,
    target_language: str = "Simplified Chinese",
    source_language: str | None = None,
    output_root: str | Path = "output/codex_native",
    title: str | None = None,
) -> dict:
    source_file = Path(subtitle_path).resolve()
    segments = load_subtitle(source_file)
    video_id = stable_id(str(source_file))
    resolved_title = title or source_file.stem
    job_dir = create_job_dir(output_root, resolved_title, video_id, target_language)
    paths = job_paths(job_dir)
    for key in ("root", "artifacts", "windows", "final"):
        paths[key].mkdir(parents=True, exist_ok=True)
    manifest = new_manifest(
        job_dir=job_dir,
        url=source_file.as_uri(),
        title=resolved_title,
        video_id=video_id,
        target_language=target_language,
        source_language=source_language,
    )
    atomic_write_json(paths["manifest"], manifest)
    copied = paths["artifacts"] / source_file.name
    if copied != source_file:
        shutil.copy2(source_file, copied)
    write_source(paths["source"], segments, language=source_language, source_kind="imported_subtitle")
    return update_manifest(
        job_dir,
        source_kind="imported_subtitle",
        status="source_ready",
        paths={"source": str(paths["source"]), "source_artifact": str(copied)},
    )


def transcribe_job_locally(
    job_dir: str | Path,
    *,
    backend: str = "auto",
    model: str | None = None,
    language: str | None = None,
    enhance: str = "off",
) -> dict:
    paths = job_paths(job_dir)
    manifest = read_json(paths["manifest"])
    audio_path = manifest.get("paths", {}).get("audio")
    if not audio_path:
        raise ValueError("This job has no downloaded audio.")
    prepared_audio = audio_enhancer.enhance_audio(audio_path, mode=enhance)
    segments, selected_backend, selected_model = transcribe_local(
        prepared_audio,
        backend=backend,
        model=model,
        language=language or manifest.get("source_language"),
    )
    write_source(
        paths["source"],
        segments,
        language=language or manifest.get("source_language"),
        source_kind=f"local_asr:{selected_backend}",
    )
    new_paths = dict(manifest.get("paths") or {})
    new_paths.update({"source": str(paths["source"]), "asr_audio": str(prepared_audio)})
    return update_manifest(
        job_dir,
        source_kind=f"local_asr:{selected_backend}",
        local_asr={"backend": selected_backend, "model": selected_model, "enhance": enhance},
        status="source_ready",
        paths=new_paths,
    )


def plan_job(job_dir: str | Path, **window_options) -> dict:
    entries = plan_translation(job_dir, **window_options)
    return update_manifest(job_dir, status="translation_planned", window_count=len(entries))
