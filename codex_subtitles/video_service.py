from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yt_dlp


CHINESE_CODES = ("zh-Hans", "zh-CN", "zh-SG", "zh-Hant", "zh-TW", "zh")
FALLBACK_CODES = ("en", "en-US", "en-GB", "ja", "ko", "th")


@dataclass(frozen=True)
class CaptionChoice:
    language: str
    kind: str
    already_target: bool


def inspect_video(url: str, ydl_factory: Callable[..., Any] = yt_dlp.YoutubeDL) -> dict:
    options = {"skip_download": True, "quiet": True, "no_warnings": True}
    with ydl_factory(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise RuntimeError("yt-dlp returned no video metadata.")
    return info


def _target_codes(target_language: str) -> tuple[str, ...]:
    lowered = target_language.lower()
    if "chinese" in lowered or "中文" in target_language or "汉语" in target_language:
        return CHINESE_CODES
    return (target_language,)


def _match(available: dict, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in available:
            return candidate
    lowered = {str(key).lower(): key for key in available}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def select_caption(
    info: dict,
    *,
    target_language: str,
    source_language: str | None = None,
) -> CaptionChoice | None:
    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    target_codes = _target_codes(target_language)

    for kind, available in (("manual", manual), ("automatic", automatic)):
        code = _match(available, target_codes)
        if code:
            return CaptionChoice(str(code), kind, True)

    requested = (source_language,) if source_language else ()
    candidates = tuple(code for code in requested + FALLBACK_CODES if code)
    for kind, available in (("manual", manual), ("automatic", automatic)):
        code = _match(available, candidates)
        if code:
            return CaptionChoice(str(code), kind, False)

    if manual:
        return CaptionChoice(str(next(iter(manual))), "manual", False)
    if automatic:
        return CaptionChoice(str(next(iter(automatic))), "automatic", False)
    return None


def _latest_nonempty(paths: list[Path]) -> Path | None:
    candidates = [path for path in paths if path.is_file() and path.stat().st_size > 0]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def download_caption(
    url: str,
    choice: CaptionChoice,
    output_base: str | os.PathLike[str],
    ydl_factory: Callable[..., Any] = yt_dlp.YoutubeDL,
) -> Path:
    base = Path(output_base).resolve()
    base.parent.mkdir(parents=True, exist_ok=True)
    options = {
        "skip_download": True,
        "writesubtitles": choice.kind == "manual",
        "writeautomaticsub": choice.kind == "automatic",
        "subtitleslangs": [choice.language],
        "subtitlesformat": "vtt/best",
        "outtmpl": str(base),
        "quiet": True,
        "no_warnings": True,
    }
    before = set(base.parent.glob(f"{base.name}*"))
    with ydl_factory(options) as ydl:
        ydl.download([url])
    after = set(base.parent.glob(f"{base.name}*"))
    created = list(after - before)
    expected = [
        Path(f"{base}.{choice.language}.vtt"),
        Path(f"{base}.{choice.language}.srt"),
    ]
    result = _latest_nonempty(expected + created + list(after))
    if result is None:
        raise RuntimeError(f"yt-dlp did not create a subtitle file for {choice.language}.")
    return result


def download_audio(
    url: str,
    output_base: str | os.PathLike[str],
    ydl_factory: Callable[..., Any] = yt_dlp.YoutubeDL,
) -> Path:
    base = Path(output_base).resolve()
    base.parent.mkdir(parents=True, exist_ok=True)
    options = {
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "outtmpl": str(base),
        "quiet": True,
        "no_warnings": True,
    }
    with ydl_factory(options) as ydl:
        ydl.download([url])
    result = _latest_nonempty([Path(f"{base}.mp3")] + list(base.parent.glob(f"{base.name}*")))
    if result is None:
        raise RuntimeError("yt-dlp did not create an audio file.")
    return result

