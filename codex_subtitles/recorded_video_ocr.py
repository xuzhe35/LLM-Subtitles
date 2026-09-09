"""Recorded video -> timestamped, original-language OCR SRT evidence.

This module never downloads video, transcribes audio, translates, or writes the
translation workflow's source.json. SRT output is an intermediate OCR artifact.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path

from .hard_subtitle_detection_service import detect_hard_subtitles
from .hard_subtitle_errors import StageError
from .hard_subtitle_models import Cue, Observation, Region, validate_records
from .hard_subtitle_ocr_service import OCRConfig, run_ocr, validate_ocr_artifact
from .ocr_backend import create_backend
from .storage import atomic_write_json, read_json, safe_name, stable_id, utc_now
from .subtitle_timeline_service import TimelineConfig, reconstruct_timeline
from .video_frame_service import ExtractionConfig, extract_frames, file_checksum, probe_video


def parse_region(value):
    if isinstance(value, Region):
        return 'explicit', value
    if value in ('auto', 'top', 'bottom'):
        return value, None
    try:
        return 'explicit', Region(*map(float, value.split(',')))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('region: use auto, top, bottom, or normalized x,y,width,height') from None


def srt_timestamp(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError('SRT timestamp must be finite and non-negative')
    milliseconds = round(seconds*1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}'


def _atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def export_ocr_srt(timeline, frame_index, ocr, evidence_root, destination, *, language, time_offset=0):
    """Export uncertain cues too; uncertainty lives in the sidecar, never ASR text."""
    if not math.isfinite(time_offset):
        raise ValueError('time-offset must be finite')
    destination = Path(destination).resolve()
    if destination.suffix.lower() != '.srt':
        raise ValueError('OCR subtitle output must end in .srt')
    frames = validate_ocr_artifact(ocr, frame_index, evidence_root)
    if any(record['status'] != 'complete' for record in ocr['records'].values()):
        raise StageError('ocr_frames_failed', 'Retry failed OCR frames before exporting.', stage='export')
    if timeline['ocr_fingerprint'] != ocr['fingerprint']:
        raise ValueError('timeline belongs to different OCR evidence')
    observations = {raw['observation_id']: Observation.from_dict(raw) for record in ocr['records'].values() for raw in record['observations']}
    cues = [Cue.from_dict(raw) for raw in timeline['cues']]
    validate_records(frames, list(observations.values()), cues, root=evidence_root, duration=frame_index['duration'])
    if not cues:
        raise StageError('ocr_returned_no_text', 'No subtitle text recovered in the selected region.', stage='export',
                         artifact=evidence_root, next_action='Inspect the crops and adjust --region to the visible subtitle band.')
    frame_map = {f.frame_id: f for f in frames}
    blocks, provenance = [], []
    for i, cue in enumerate(cues, 1):
        start, end = cue.start+time_offset, cue.end+time_offset
        if start < 0 or round(end*1000) <= round(start*1000):
            raise ValueError(f'{cue.candidate_id}: offset creates a negative or zero-length SRT cue')
        blocks.append(f'{i}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{cue.text}\n')
        images = list(dict.fromkeys(frame_map[observations[key].frame_id].image for key in cue.observation_ids))
        provenance.append({'srt_index': i, 'candidate_id': cue.candidate_id, 'start': start, 'end': end,
                           'recording_start': cue.start, 'recording_end': cue.end,
                           'confidence': cue.confidence, 'issues': cue.issues,
                           'needs_visual_check': bool(cue.issues) or cue.confidence < .9,
                           'observation_ids': cue.observation_ids, 'evidence_images': images})
    content = '\n'.join(blocks)
    if not destination.is_file() or destination.read_text(encoding='utf-8') != content:
        _atomic_text(destination, content)
    quality_path = destination.with_suffix('.quality.json')
    quality = {'schema_version': 1, 'kind': 'original_language_ocr_evidence', 'language': language,
               'srt': str(destination), 'srt_checksum': file_checksum(destination),
               'timing_basis': 'recording elapsed time plus explicit offset', 'time_offset': time_offset,
               'evidence_root': str(Path(evidence_root).resolve()), 'machine_generated': True,
               'cue_count': len(cues), 'cues_needing_visual_check': sum(c['needs_visual_check'] for c in provenance),
               'cues': provenance, 'rejected_observations': timeline.get('rejected', [])}
    atomic_write_json(quality_path, quality)
    return {'srt': str(destination), 'quality_report': str(quality_path), 'cue_count': len(cues),
            'cues_needing_visual_check': quality['cues_needing_visual_check']}


def extract_recorded_subtitles(video, *, language='en', region='auto', fps=3, refine_fps=10,
                               start=0, end=None, time_offset=0, output_root='output/recorded_subtitles',
                               backend=None, force=False):
    source = Path(video).expanduser().resolve()
    if not source.is_file():
        raise ValueError('video: provide an existing local recording file')
    if not isinstance(language, str) or not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', language):
        raise ValueError('language: use the visible subtitle language code, for example en')
    if not math.isfinite(time_offset):
        raise ValueError('time-offset must be finite')
    region_mode, explicit_region = parse_region(region)
    extraction = ExtractionConfig(fps=fps, refine_fps=refine_fps, start=start, end=end)
    metadata = probe_video(source)
    config = {'language': language, 'region_mode': region_mode,
              'region': asdict(explicit_region) if explicit_region else None, 'extraction': asdict(extraction)}
    identity = stable_id(json.dumps({'video': metadata['checksum'], 'config': config}, sort_keys=True), 12)
    root = Path(output_root).resolve() / f'{safe_name(source.stem, limit=64)}.{identity}'
    evidence = root/'artifacts'
    manifest_path = root/'ocr-job.json'
    previous = read_json(manifest_path) if manifest_path.exists() else {}
    manifest = {'schema_version': 1, 'kind': 'recorded_video_ocr', 'job_dir': str(root),
                'input_video': str(source), 'video_checksum': metadata['checksum'], 'config': config,
                'time_offset': time_offset, 'status': 'video_ready', 'history': previous.get('history', [])}
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(root/'video.json', metadata)
    atomic_write_json(manifest_path, manifest)
    def checkpoint(state, artifact):
        manifest.update(status=state, updated_at=utc_now(), last_successful_artifact=str(artifact))
        manifest['history'].append({'state': state, 'time': manifest['updated_at'], 'artifact': str(artifact)})
        atomic_write_json(manifest_path, manifest)
    try:
        backend = backend or create_backend()
        if region_mode == 'auto':
            detection = detect_hard_subtitles(source, evidence, recognize=backend.recognize, backend_id=backend.identity,
                                               language=language, forced=True, metadata=metadata, start=start, end=end, force=force)
            selected_region = Region(**detection['region'])
            manifest['detection_warning'] = detection['warning']
            checkpoint('region_ready', evidence/'detection.json')
        else:
            selected_region = explicit_region or (Region() if region_mode == 'bottom' else Region(0,0,1,.25))
        frames = extract_frames(source, evidence, region=selected_region, config=extraction, metadata=metadata, force=force)
        manifest['frame_cache_hit'] = frames['cache_hit']
        checkpoint('frames_ready', evidence/'frames.index.json')
        ocr = run_ocr(read_json(evidence/'frames.index.json'), evidence, backend=backend,
                      config=OCRConfig(language=language), force=force)
        manifest['ocr_cache_hit'] = ocr['cache_hit']
        if ocr['failed_frames']:
            raise StageError('ocr_frames_failed', f"{ocr['failed_frames']} OCR frames failed; successful frames were saved.",
                             stage='ocr', artifact=evidence/'ocr.observations.json', next_action='Retry the same command to resume only failed frames.')
        checkpoint('ocr_ready', evidence/'ocr.observations.json')
        timeline = reconstruct_timeline(read_json(evidence/'frames.index.json'), read_json(evidence/'ocr.observations.json'), evidence)
        checkpoint('timeline_ready', evidence/'ocr.cues.json')
        output = root/f'{safe_name(source.stem)}.ocr.{language}.srt'
        result = export_ocr_srt(timeline, read_json(evidence/'frames.index.json'), read_json(evidence/'ocr.observations.json'), evidence,
                                output, language=language, time_offset=time_offset)
        manifest.update(result)
        checkpoint('complete', output)
        return manifest
    except Exception as error:
        manifest.update(status='failed', error=getattr(error, 'details', {'code': type(error).__name__, 'message': str(error)}))
        atomic_write_json(manifest_path, manifest)
        if isinstance(error, StageError):
            error.details['job_id'] = identity
        raise


def recorded_ocr_status(job_dir):
    root = Path(job_dir).resolve()
    manifest = read_json(root/'ocr-job.json')
    if manifest.get('kind') != 'recorded_video_ocr':
        raise ValueError('This is not a recorded-video OCR job.')
    if manifest['status'] == 'complete':
        try:
            if file_checksum(manifest['input_video']) != manifest['video_checksum']:
                raise ValueError('Input video changed.')
            quality = read_json(manifest['quality_report'])
            if file_checksum(manifest['srt']) != quality['srt_checksum']:
                raise ValueError('SRT changed after OCR export.')
        except (OSError, ValueError, KeyError) as error:
            return {**manifest, 'status': 'stale', 'error': str(error)}
    return manifest
