from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str

    def to_dict(self):
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }


class TranscriptResult:
    def __init__(self, segments: Iterable[Any]):
        self.segments = normalize_segments(segments)


def normalize_segment(segment: Any) -> Optional[dict]:
    if segment is None:
        return None

    if isinstance(segment, Mapping):
        start = segment.get("start")
        end = segment.get("end")
        text = segment.get("text", "")
    else:
        start = getattr(segment, "start", None)
        end = getattr(segment, "end", None)
        text = getattr(segment, "text", "")

    if start is None or end is None:
        return None

    return Segment(
        start=float(start),
        end=float(end),
        text=str(text),
    ).to_dict()


def normalize_segments(segments: Iterable[Any]) -> List[dict]:
    normalized = []
    for segment in segments or []:
        item = normalize_segment(segment)
        if item is not None:
            normalized.append(item)
    return normalized


def transcript_segments(transcript: Any) -> List[dict]:
    if transcript is None:
        return []
    raw_segments = getattr(transcript, "segments", None)
    if raw_segments is None and isinstance(transcript, Mapping):
        raw_segments = transcript.get("segments", [])
    return normalize_segments(raw_segments or [])
