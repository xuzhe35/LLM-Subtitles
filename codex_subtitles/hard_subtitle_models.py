"""Versioned records. Coordinates are top-left based; time is seconds."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .ocr_storage import safe_path

SCHEMA_VERSION = 1


def number(value, name, *, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (maximum is not None and value > maximum):
        raise ValueError(f'{name}: expected a finite non-negative number' + (f' <= {maximum}' if maximum is not None else ''))


def nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name}: expected non-empty text')


class Record:
    def to_dict(self):
        return {'schema_version': SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        version = data.pop('schema_version', None)
        if type(version) is not int or version != SCHEMA_VERSION:
            raise ValueError('schema_version: expected version 1')
        return cls(**data)


@dataclass(frozen=True)
class Region(Record):
    x: float = 0
    y: float = .75
    width: float = 1
    height: float = .25

    def __post_init__(self):
        for key in ('x', 'y', 'width', 'height'):
            number(getattr(self, key), key, maximum=1)
        if self.width <= 0 or self.height <= 0 or self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError('region: must have positive area inside the image')

    def pixels(self, width, height):
        if width < 1 or height < 1:
            raise ValueError('invalid image dimensions')
        x, y = int(self.x * width), int(self.y * height)
        return x, y, max(1, min(width-x, round(self.width*width))), max(1, min(height-y, round(self.height*height)))


@dataclass(frozen=True)
class Frame(Record):
    frame_id: str
    timestamp: float
    image: str
    region: Region
    selection_reason: str = 'subtitle_region_changed'
    change_score: float = 0

    def __post_init__(self):
        nonempty(self.frame_id, 'frame_id')
        number(self.timestamp, 'timestamp')
        number(self.change_score, 'change_score', maximum=1)
        nonempty(self.selection_reason, 'selection_reason')
        safe_path(Path('/evidence'), self.image)
        if not isinstance(self.region, Region):
            object.__setattr__(self, 'region', Region(**self.region))

    def validate_evidence(self, root):
        path = safe_path(Path(root), self.image)
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f'{self.frame_id}: missing evidence {self.image}')


@dataclass(frozen=True)
class OCRLine(Record):
    text: str
    confidence: float
    box: Region

    def __post_init__(self):
        nonempty(self.text, 'text')
        number(self.confidence, 'confidence', maximum=1)
        if not isinstance(self.box, Region):
            object.__setattr__(self, 'box', Region(**self.box))


@dataclass(frozen=True)
class Observation(Record):
    observation_id: str
    frame_id: str
    timestamp: float
    language: str | None
    text: str
    confidence: float
    backend: str
    preprocessing: str = 'original'
    boxes: list[dict] = field(default_factory=list)

    def __post_init__(self):
        for key in ('observation_id', 'frame_id', 'backend', 'preprocessing'):
            nonempty(getattr(self, key), key)
        if not isinstance(self.text, str):
            raise ValueError('text must be a string (empty means no text)')
        number(self.timestamp, 'timestamp')
        number(self.confidence, 'confidence', maximum=1)
        for box in self.boxes:
            Region(**box)


@dataclass(frozen=True)
class Cue(Record):
    candidate_id: str
    start: float
    end: float
    text: str
    confidence: float
    observation_ids: list[str]
    issues: list[str] = field(default_factory=list)
    review_status: str = 'not_required'

    def __post_init__(self):
        nonempty(self.candidate_id, 'candidate_id')
        nonempty(self.text, 'text')
        number(self.start, 'start')
        number(self.end, 'end')
        number(self.confidence, 'confidence', maximum=1)
        if self.end <= self.start:
            raise ValueError('cue end must be later than start')
        if not self.observation_ids or len(set(self.observation_ids)) != len(self.observation_ids):
            raise ValueError('cue needs unique observation IDs')
        if self.review_status not in ('not_required', 'required', 'accepted', 'corrected', 'dropped'):
            raise ValueError('invalid review_status')

    def validate_observations(self, observations):
        try:
            selected = [observations[key] for key in self.observation_ids]
        except KeyError as error:
            raise ValueError(f'{self.candidate_id}: unknown observation {error}') from None
        times = [o.timestamp for o in selected]
        if times != sorted(times) or any(t < self.start or t > self.end for t in times):
            raise ValueError(f'{self.candidate_id}: unsorted or out-of-cue observations')


def validate_records(frames, observations=(), cues=(), *, root, duration):
    number(duration, 'duration')
    for records, key in ((frames, 'frame_id'), (observations, 'observation_id'), (cues, 'candidate_id')):
        ids = [getattr(r, key) for r in records]
        if len(ids) != len(set(ids)):
            raise ValueError(f'duplicate {key}')
    frame_map = {f.frame_id: f for f in frames}
    times = [f.timestamp for f in frames]
    if times != sorted(times) or any(t > duration for t in times):
        raise ValueError('invalid frame ordering or duration')
    for frame in frames:
        frame.validate_evidence(root)
    obs_map = {o.observation_id: o for o in observations}
    for obs in observations:
        if obs.frame_id not in frame_map or obs.timestamp != frame_map[obs.frame_id].timestamp:
            raise ValueError(f'{obs.observation_id}: invalid frame reference or timestamp')
    end = 0
    for cue in cues:
        if cue.start < end or cue.end > duration:
            raise ValueError(f'{cue.candidate_id}: overlapping/out-of-bounds cue')
        cue.validate_observations(obs_map)
        end = cue.end
