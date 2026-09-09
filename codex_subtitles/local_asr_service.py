from __future__ import annotations

import importlib.util
import os
import platform
from pathlib import Path
from typing import Any

from .source_service import normalize_source_segments


DEFAULT_MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_WHISPER_MODEL = "medium"


def available_backends() -> list[str]:
    backends = []
    if importlib.util.find_spec("mlx_whisper") is not None:
        backends.append("mlx-whisper")
    if importlib.util.find_spec("whisper") is not None:
        backends.append("openai-whisper-local")
    return backends


def choose_backend(requested: str = "auto") -> str:
    available = available_backends()
    if requested != "auto":
        if requested not in available:
            raise RuntimeError(
                f"Local ASR backend {requested!r} is not installed. Available: {available or 'none'}."
            )
        return requested
    if platform.machine() == "arm64" and "mlx-whisper" in available:
        return "mlx-whisper"
    if "openai-whisper-local" in available:
        return "openai-whisper-local"
    if available:
        return available[0]
    raise RuntimeError(
        "No local Whisper runtime is installed. Install mlx-whisper on Apple Silicon "
        "or openai-whisper on another supported machine, then retry."
    )


def _transcribe_mlx(audio_path: str, model: str, language: str | None) -> dict[str, Any]:
    import mlx_whisper

    kwargs: dict[str, Any] = {"path_or_hf_repo": model}
    if language:
        kwargs["language"] = language
    return mlx_whisper.transcribe(audio_path, **kwargs)


def _transcribe_openai_whisper(audio_path: str, model: str, language: str | None) -> dict[str, Any]:
    import whisper

    runtime = whisper.load_model(model)
    kwargs: dict[str, Any] = {"task": "transcribe"}
    if language:
        kwargs["language"] = language
    return runtime.transcribe(audio_path, **kwargs)


def transcribe_local(
    audio_path: str | os.PathLike[str],
    *,
    backend: str = "auto",
    model: str | None = None,
    language: str | None = None,
) -> tuple[list[dict], str, str]:
    source = Path(audio_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    selected = choose_backend(backend)
    if selected == "mlx-whisper":
        resolved_model = model or DEFAULT_MLX_MODEL
        result = _transcribe_mlx(str(source), resolved_model, language)
    else:
        resolved_model = model or DEFAULT_WHISPER_MODEL
        result = _transcribe_openai_whisper(str(source), resolved_model, language)
    segments = normalize_source_segments(result.get("segments", []))
    if not segments:
        raise RuntimeError(f"{selected} returned no timestamped speech segments.")
    return segments, selected, resolved_model


