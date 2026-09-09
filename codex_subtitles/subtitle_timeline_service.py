"""Temporal consensus over sampled OCR, preserving raw text as evidence."""
from __future__ import annotations
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from .hard_subtitle_models import Cue, Observation, number
from .hard_subtitle_ocr_service import validate_ocr_artifact
from .storage import atomic_write_json, stable_id


@dataclass(frozen=True)
class TimelineConfig:
    similarity_threshold: float = .88
    review_confidence: float = .9
    minimum_duration: float = .25
    blank_gap: float = .25
    interference_gap: float = .6
    interference_similarity: float = .95
    watermark_duration: float = 45
    watermark_fraction: float = .85
    watermark_minimum: float = 10

    def __post_init__(self):
        for key, value in asdict(self).items():
            number(value, key, maximum=1 if key in ('similarity_threshold', 'interference_similarity', 'review_confidence', 'watermark_fraction') else None)


def comparison_text(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def text_similarity(a, b):
    return SequenceMatcher(None, comparison_text(a), comparison_text(b), autojunk=False).ratio()


def subtitle_text(obs):
    lines = obs.text.splitlines()
    if not obs.boxes:
        return obs.text
    return '\n'.join(line for line, box in zip(lines, obs.boxes)
                     if .05 <= box['height'] <= .85 and box['width'] >= .08
                     and abs(box['x'] + box['width']/2 - .5) <= .24)


def reconstruct_timeline(frame_index, ocr, root, *, config=None):
    config = config or TimelineConfig()
    validate_ocr_artifact(ocr, frame_index, root)
    if any(record['status'] != 'complete' for record in ocr['records'].values()):
        raise ValueError('timeline: retry failed OCR frames before reconstruction')
    selected = {}
    observations = {}
    for frame_id, record in ocr['records'].items():
        variants = [Observation.from_dict(o) for o in record['observations']]
        observations.update({o.observation_id: o for o in variants})
        # Confidence plus agreement selects mechanical OCR candidates only.
        best = max(variants, key=lambda o: o.confidence + .1*sum(text_similarity(o.text, v.text) for v in variants)/len(variants))
        selected[frame_id] = (best, subtitle_text(best))
    samples = frame_index['samples']
    end = frame_index['sample_range'][1]
    groups = []
    active = None
    for index, sample in enumerate(samples):
        t = sample['timestamp']
        next_t = samples[index+1]['timestamp'] if index+1 < len(samples) else end
        obs, text = selected[sample['frame_id']]
        if not text.strip():
            continue
        if active and t-active['end'] <= config.blank_gap and text_similarity(active['last_text'], text) >= config.similarity_threshold:
            active['end'] = next_t
            active['values'].append((obs, text, next_t-t))
            active['last_text'] = text
        else:
            active = {'start': t, 'end': next_t, 'last_text': text, 'values': [(obs, text, next_t-t)]}
            groups.append(active)
    def consensus(group):
        scores = defaultdict(float)
        for obs, text, weight in group['values']:
            scores[text] += obs.confidence * weight
        return max(scores, key=scores.get)

    # A transient OCR failure must not split a subtitle that is stably visible
    # on both sides. Bridge only rejected short flashes, not another stable cue.
    stable_groups, pending_flashes, rejected = [], [], []
    for group in groups:
        group['consensus'] = consensus(group)
        if group['end']-group['start'] < config.minimum_duration:
            rejected.append({'start': group['start'], 'end': group['end'], 'text': group['consensus'], 'reason': 'short_flash'})
            pending_flashes.append(group)
            continue
        previous = stable_groups[-1] if stable_groups else None
        if (previous and pending_flashes and 0 < group['start']-previous['end'] <= config.interference_gap
                and text_similarity(previous['consensus'], group['consensus']) >= config.interference_similarity):
            for flash in pending_flashes:
                previous['values'].extend(flash['values'])
            previous['values'].extend(group['values'])
            previous['end'] = group['end']
            previous['consensus'] = consensus(previous)
            previous['bridged_interference'] = True
        else:
            stable_groups.append(group)
        pending_flashes = []
    cues = []
    for group in stable_groups:
        duration = group['end']-group['start']
        text = group['consensus']
        if duration >= config.watermark_duration or (duration >= config.watermark_minimum and duration/(end-frame_index['sample_range'][0]) >= config.watermark_fraction):
            rejected.append({'start': group['start'], 'end': group['end'], 'text': text, 'reason': 'persistent_watermark'})
            continue
        weights = sum(v[2] for v in group['values'])
        confidence = sum(o.confidence*w for o, _, w in group['values']) / max(weights, 1e-9)
        issues = ['transient_ocr_interference'] if group.get('bridged_interference') else []
        if confidence < config.review_confidence: issues.append('low_confidence')
        if len({comparison_text(t) for _, t, _ in group['values']}) > 1: issues.append('ocr_text_variation')
        if cues and set(cues[-1].text.splitlines()) & set(text.splitlines()) and comparison_text(cues[-1].text) != comparison_text(text):
            issues.append('rolling_line_transition')
        obs_ids = sorted({o.observation_id for o, _, _ in group['values']}, key=lambda key: observations[key].timestamp)
        # A reused retained crop may precede the current sampling timestamp; preserve that evidence.
        start = min(group['start'], min(observations[key].timestamp for key in obs_ids))
        if cues and start < cues[-1].end:
            start = group['start']
            obs_ids = [key for key in obs_ids if observations[key].timestamp >= start]
        if not obs_ids:
            raise ValueError('timeline: cue has no contemporaneous evidence')
        cue = Cue(f'hc{len(cues)+1:06d}', start, group['end'], text, confidence, obs_ids,
                  issues, 'required' if issues else 'not_required')
        cue.validate_observations(observations)
        if cues and cue.start < cues[-1].end:
            raise ValueError('timeline: overlapping cues')
        cues.append(cue)
    fingerprint = stable_id(json.dumps({'ocr': ocr['fingerprint'], 'config': asdict(config), 'algorithm': 2}, sort_keys=True), 32)
    payload = {'schema_version': 1, 'status': 'valid', 'fingerprint': fingerprint, 'ocr_fingerprint': ocr['fingerprint'],
               'config': asdict(config), 'duration': frame_index['duration'], 'cues': [c.to_dict() for c in cues], 'rejected': rejected}
    from pathlib import Path
    atomic_write_json(Path(root) / 'ocr.cues.json', payload)
    return payload
