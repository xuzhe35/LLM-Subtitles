"""Conservative region detection, independently testable with OCR evidence."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .hard_subtitle_models import OCRLine, Region
from .hard_subtitle_errors import StageError
from .storage import atomic_write_json, read_json, stable_id
from .video_frame_service import ExtractionConfig, crop_stream, executable_version, file_checksum, png_gray, probe_video


def rank_region(samples, *, minimum_phrases=2, minimum_hits=3):
    texts, geometry, times = [], [], []
    for sample in samples:
        lines = [line if isinstance(line, OCRLine) else OCRLine.from_dict(line) for line in sample['lines']]
        # A subtitle occupies a horizontal central portion of the candidate crop.
        useful = [line for line in lines if line.confidence >= .5 and len(line.text.strip()) >= 3
                  and .05 <= line.box.height <= .8 and .12 <= line.box.width <= .98
                  and abs(line.box.x + line.box.width/2 - .5) <= .22]
        if useful:
            text = ' '.join(line.text.casefold().strip() for line in useful)
            texts.append(text)
            geometry.append(sum(line.confidence for line in useful)/len(useful))
            times.append(sample['timestamp'])
    count = len(texts)
    distinct = len(set(texts))
    dominant = max(Counter(texts).values(), default=0) / max(1, count)
    coverage = count / max(1, len(samples))
    persistent_phrases = {texts[i] for i in range(1, len(texts)) if texts[i] == texts[i-1] and times[i]-times[i-1] <= 1.01}
    reasons = []
    if count < minimum_hits: reasons.append('insufficient_persistent_text')
    if distinct < minimum_phrases: reasons.append('unchanged_text_or_watermark')
    if not count: reasons.append('no_subtitle_geometry')
    if dominant > .85: reasons.append('dominant_static_text')
    if len(persistent_phrases) < minimum_phrases: reasons.append('insufficient_adjacent_phrase_persistence')
    confidence = min(1., coverage*.4 + min(1, distinct/3)*.3 + (sum(geometry)/max(1, count))*.3)
    if count < minimum_hits or distinct < minimum_phrases or dominant > .85 or len(persistent_phrases) < minimum_phrases:
        confidence = min(confidence, .35)
    return {'confidence': round(confidence, 4), 'hits': count, 'distinct_phrases': distinct,
            'persistent_phrases': len(persistent_phrases), 'coverage': coverage, 'dominant_phrase_fraction': dominant,
            'sampled_timestamps': [s['timestamp'] for s in samples], 'text_timestamps': times,
            'reasons': reasons, 'usable': confidence >= .65 and not reasons}


def decide_region(candidates, *, forced=False):
    ranked = sorted(candidates, key=lambda c: c['confidence'], reverse=True)
    if not ranked:
        raise ValueError('no region candidates')
    selected = ranked[0]
    found = selected['usable']
    code = 'hard_subtitles_detected' if found else ('hard_subtitle_detection_uncertain' if selected['hits'] else 'no_hard_subtitles_detected')
    return {'schema_version': 1, 'status': 'valid', 'outcome': code, 'detected': found,
            'proceed': found or forced, 'forced': forced, 'region': selected['region'],
            'confidence': selected['confidence'], 'selected': selected,
            'rejected_alternatives': ranked[1:], 'warning': None if found else code}


def detect_hard_subtitles(video, output_dir, *, recognize, backend_id, language=None, region_mode='auto',
                           explicit_region=None, forced=False, metadata=None, start=0, end=None, force=False):
    metadata = metadata or probe_video(video)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    end = min(end or metadata['duration'], metadata['duration'])
    if start >= end:
        raise ValueError('detection range is outside the video')
    if region_mode not in ('auto', 'bottom', 'top', 'explicit'):
        raise ValueError('invalid region mode')
    if region_mode == 'explicit' and explicit_region is None:
        raise ValueError('explicit region required')
    bands = {'bottom': Region(), 'top': Region(0, 0, 1, .25)}
    if region_mode == 'explicit': bands = {'explicit': explicit_region}
    elif region_mode != 'auto': bands = {region_mode: bands[region_mode]}
    settings = {'algorithm': 2, 'video_checksum': metadata['checksum'], 'backend': backend_id, 'language': language,
                'regions': {k: asdict(v) for k, v in bands.items()}, 'forced': forced, 'range': [start, end],
                'ffmpeg': executable_version('ffmpeg')}
    fingerprint = stable_id(json.dumps(settings, sort_keys=True), 32)
    path = root / 'detection.json'
    if path.exists() and not force:
        try:
            cached = read_json(path)
            if cached['fingerprint'] == fingerprint and cached['status'] == 'valid':
                for evidence in cached['evidence']:
                    from .ocr_storage import safe_path
                    if file_checksum(safe_path(root, evidence['image'])) != evidence['checksum']:
                        raise ValueError('changed detection evidence')
                return {**cached, 'cache_hit': True}
        except (KeyError, ValueError, OSError):
            pass
    candidates, evidence = [], []
    for name, region in bands.items():
        # Adjacent triples at distributed timestamps distinguish persistent subtitles
        # from unrelated text appearing once per scene.
        groups = min(8, max(1, int(end-start)))
        samples = []
        for group in range(groups):
            left = start + group*(end-start)/groups
            right = min(end, left + min(1, (end-start)/groups))
            config = ExtractionConfig(fps=3, start=left, end=right)
            for timestamp, pixels, width, height in crop_stream(video, metadata, region, config):
                image = f'detection/{fingerprint}/{name}-{len(samples):04d}.png'
                png_gray(root / image, pixels, width, height)
                lines = recognize(root / image, language=language)
                samples.append({'timestamp': timestamp, 'lines': [line.to_dict() for line in lines], 'image': image})
                evidence.append({'image': image, 'checksum': file_checksum(root / image)})
        candidates.append({'name': name, 'region': asdict(region), 'samples': samples, **rank_region(samples)})
    result = {**decide_region(candidates, forced=forced), 'fingerprint': fingerprint,
              'settings': settings, 'evidence': evidence}
    atomic_write_json(path, result)
    return {**result, 'cache_hit': False}
