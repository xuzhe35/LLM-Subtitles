from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Iterable

from utils import subtitle_formatter
from utils.segments import normalize_segments

from . import SCHEMA_VERSION
from .storage import atomic_write_json


_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def clean_caption_text(value: str) -> str:
    text = html.unescape(_TAG_RE.sub("", str(value or "")))
    lines = [_SPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return " ".join(line for line in lines if line).strip()


def normalize_source_segments(segments: Iterable[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for segment in normalize_segments(segments):
        text = clean_caption_text(segment.get("text", ""))
        start = max(0.0, float(segment["start"]))
        end = max(start, float(segment["end"]))
        if not text or end <= start:
            continue
        if cleaned and cleaned[-1]["text"] == text and start <= cleaned[-1]["end"] + 0.25:
            cleaned[-1]["end"] = max(cleaned[-1]["end"], end)
            continue
        cleaned.append({"start": start, "end": end, "text": text})

    for index, segment in enumerate(cleaned):
        segment["id"] = f"c{index + 1:06d}"
        segment["index"] = index
    return cleaned


def load_subtitle(path: str | Path) -> list[dict]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".srt":
        segments = subtitle_formatter.parse_srt(str(source))
    elif suffix in {".vtt", ".webvtt"}:
        segments = subtitle_formatter.parse_vtt(str(source))
    elif suffix == ".json":
        with open(source, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        segments = payload.get("segments", payload.get("source_segments", [])) if isinstance(payload, dict) else payload
    else:
        raise ValueError(f"Unsupported subtitle format: {source.suffix}")
    normalized = normalize_source_segments(segments)
    if not normalized:
        raise ValueError(f"No usable subtitle cues found in {source}.")
    return normalized


def write_source(
    path: str | Path,
    segments: Iterable[dict],
    *,
    language: str | None,
    source_kind: str,
) -> Path:
    normalized = normalize_source_segments(segments)
    if not normalized:
        raise ValueError("Cannot write an empty source transcript.")
    return atomic_write_json(path, {
        "schema_version": SCHEMA_VERSION,
        "language": language,
        "source_kind": source_kind,
        "segments": normalized,
    })
