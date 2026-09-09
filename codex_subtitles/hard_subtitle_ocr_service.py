"""Adaptive local OCR with per-frame atomic checkpoints."""
from __future__ import annotations
import json
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import re
import shutil
import subprocess
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from .hard_subtitle_errors import StageError
from .hard_subtitle_models import Observation, number
from .storage import atomic_write_json, read_json, stable_id
from .video_frame_service import executable_version, file_checksum, validate_frame_index


@dataclass(frozen=True)
class OCRConfig:
    language: str | None = None
    high_confidence: float = .9
    variants: tuple[str, ...] = ('original', 'upscale-2x', 'contrast')

    def __post_init__(self):
        number(self.high_confidence, 'high_confidence', maximum=1)
        if not self.variants or self.variants[0] != 'original' or any(v not in ('original', 'upscale-2x', 'contrast', 'threshold') for v in self.variants):
            raise ValueError('invalid preprocessing variants; original must be first')


def normalize_ocr_text(text):
    # NFC and spacing only: preserve punctuation, case, and semantic wording.
    return '\n'.join(re.sub(r'[^\S\n]+', ' ', line).strip() for line in unicodedata.normalize('NFC', text).splitlines() if line.strip())


def preprocess(image, variant, destination):
    if variant == 'original':
        return image
    filters = {'upscale-2x': 'scale=iw*2:ih*2:flags=lanczos',
               'contrast': 'format=gray,normalize=blackpt=black:whitept=white',
               'threshold': "format=gray,lut=y='if(gte(val,180),255,0)'"}
    destination.parent.mkdir(parents=True, exist_ok=True)
    binary = shutil.which('ffmpeg')
    if not binary:
        raise StageError('ffmpeg_missing', 'FFmpeg is required for preprocessing.', stage='ocr')
    try:
        subprocess.run([binary, '-v', 'error', '-nostdin', '-i', str(image), '-vf', filters[variant],
                        '-frames:v', '1', '-y', str(destination)], capture_output=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise StageError('ocr_preprocessing_failed', 'Image preprocessing failed.', stage='ocr', artifact=image) from error
    return destination


def validate_ocr_artifact(payload, frame_index, root):
    frames = validate_frame_index(frame_index, root)
    if payload.get('schema_version') != 1 or payload.get('status') not in ('valid', 'partial'):
        raise ValueError('invalid OCR artifact')
    if payload.get('frame_fingerprint') != frame_index['fingerprint']:
        raise ValueError('stale OCR frame fingerprint')
    by_id = {f.frame_id: f for f in frames}
    ids = set()
    if set(payload['records']) - set(by_id):
        raise ValueError('unknown OCR frame')
    for frame_id, record in payload['records'].items():
        if record['status'] == 'failed':
            if not record.get('error'):
                raise ValueError('failed frame requires error')
            continue
        if record['status'] != 'complete' or not record['observations']:
            raise ValueError('invalid completed OCR frame')
        for raw in record['observations']:
            obs = Observation.from_dict(raw)
            if obs.frame_id != frame_id or obs.timestamp != by_id[frame_id].timestamp or obs.observation_id in ids:
                raise ValueError('invalid OCR observation reference')
            ids.add(obs.observation_id)
    if payload['status'] == 'valid' and set(payload['records']) != set(by_id):
        raise ValueError('incomplete OCR coverage')
    return frames


def run_ocr(frame_index, output_dir, *, backend, config=None, force=False, preprocess_fn=preprocess, workers=None):
    config = config or OCRConfig()
    workers = getattr(backend, 'max_workers', 1) if workers is None else workers
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError('workers must be an integer between 1 and 8')
    root = Path(output_dir).resolve()
    frames = validate_frame_index(frame_index, root)
    settings = {'config': asdict(config), 'backend': backend.identity,
                'input_checksum': stable_id(json.dumps(frame_index, sort_keys=True)),
                'frame_fingerprint': frame_index['fingerprint'], 'ffmpeg': executable_version('ffmpeg'), 'algorithm': 1}
    # cache_hit is a return value, not an input to stage identity.
    settings['input_checksum'] = stable_id(json.dumps({k: v for k, v in frame_index.items() if k != 'cache_hit'}, sort_keys=True))
    fingerprint = stable_id(json.dumps(settings, sort_keys=True), 32)
    path = root / 'ocr.observations.json'
    payload = {'schema_version': 1, 'status': 'partial', 'fingerprint': fingerprint, 'settings': settings,
               'frame_fingerprint': frame_index['fingerprint'], 'records': {}}
    if path.is_file() and not force:
        try:
            old = read_json(path)
            if old['fingerprint'] == fingerprint:
                validate_ocr_artifact(old, frame_index, root)
                payload = old
        except (ValueError, TypeError, KeyError, OSError):
            pass
    cached = sum(r['status'] == 'complete' for r in payload['records'].values())
    if cached == len(frames) and payload['status'] == 'valid':
        return {**payload, 'cache_hit': True, 'cached_frames': cached, 'failed_frames': 0}
    payload['status'] = 'partial'
    def recognize_frame(frame):
        observations = []
        try:
            for variant in config.variants:
                image = preprocess_fn(root / frame.image, variant, root / 'preprocessed' / fingerprint / f'{frame.frame_id}.{variant}.png')
                lines = backend.recognize(image, language=config.language)
                text = normalize_ocr_text('\n'.join(line.text for line in lines))
                confidence = sum(line.confidence * len(line.text) for line in lines) / max(1, sum(len(line.text) for line in lines))
                observations.append(Observation(f'{frame.frame_id}:{variant}', frame.frame_id, frame.timestamp,
                                                config.language, text, confidence, backend.identity, variant,
                                                [asdict(line.box) for line in lines]).to_dict())
                if text and confidence >= config.high_confidence:
                    break
            return {'status': 'complete', 'observations': observations}
        except Exception as error:
            return {'status': 'failed', 'error': getattr(error, 'details', {'code': type(error).__name__, 'message': 'Local frame OCR failed; safe to retry.'}),
                                                  'observations': observations}
    pending_frames = iter(frame for frame in frames if payload['records'].get(frame.frame_id, {}).get('status') != 'complete')
    # Keep at most a few native processes in flight. Only the coordinator writes
    # checkpoints, in chronological order, so concurrency does not alter artifacts.
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = deque()
        for _ in range(workers):
            frame = next(pending_frames, None)
            if frame is not None:
                pending.append((frame, executor.submit(recognize_frame, frame)))
        while pending:
            frame, future = pending.popleft()
            payload['records'][frame.frame_id] = future.result()
            atomic_write_json(path, payload)
            frame = next(pending_frames, None)
            if frame is not None:
                pending.append((frame, executor.submit(recognize_frame, frame)))
    payload['status'] = 'valid'
    validate_ocr_artifact(payload, frame_index, root)
    atomic_write_json(path, payload)
    return {**payload, 'cache_hit': cached == len(frames), 'cached_frames': cached,
            'failed_frames': sum(r['status'] == 'failed' for r in payload['records'].values())}
