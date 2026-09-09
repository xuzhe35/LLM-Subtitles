"""FFmpeg mechanics with bounded subprocesses and cropped evidence only."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

from .hard_subtitle_errors import StageError


def file_checksum(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def executable_version(name):
    binary = shutil.which(name)
    if not binary:
        return None
    try:
        result = subprocess.run([binary, '-version'], capture_output=True, text=True, timeout=15, check=True)
        return result.stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        return None


def probe_video(path, *, ffprobe=None):
    path = Path(path).resolve()
    if not path.is_file() or not path.stat().st_size:
        raise StageError('video_decode_failed', 'Video is missing or empty.', stage='video', artifact=path,
                         next_action='Provide a readable local video or retry the YouTube download.')
    binary = ffprobe or shutil.which('ffprobe')
    if not binary:
        raise StageError('ffprobe_missing', 'FFprobe is required.', stage='video', artifact=path,
                         next_action='Install FFmpeg (including FFprobe), then retry.')
    try:
        result = subprocess.run([binary, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                                capture_output=True, text=True, timeout=60, check=True)
        data = json.loads(result.stdout)
        stream = next(s for s in data['streams'] if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic'))
        duration = float(data.get('format', {}).get('duration') or stream.get('duration'))
        fps = float(Fraction(stream.get('avg_frame_rate') or stream['r_frame_rate']))
        width, height = int(stream['width']), int(stream['height'])
        if not math.isfinite(duration) or duration <= 0 or fps <= 0 or width <= 0 or height <= 0:
            raise ValueError('invalid media dimensions, duration or frame rate')
        return {'schema_version': 1, 'path': str(path), 'checksum': file_checksum(path),
                'duration': duration, 'width': width, 'height': height, 'fps': fps,
                'container': data['format'].get('format_name'), 'streams': [
                    {k: s.get(k) for k in ('index', 'codec_type', 'codec_name', 'width', 'height', 'sample_rate')}
                    for s in data['streams']], 'ffprobe_version': executable_version('ffprobe')}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, StopIteration, TypeError, ZeroDivisionError) as error:
        raise StageError('video_decode_failed', f'FFprobe could not read video ({type(error).__name__}).',
                         stage='video', artifact=path, next_action='Check the video file or download it again.') from error


import os
import select
import struct
import time
import zlib
from dataclasses import asdict, dataclass

from .hard_subtitle_models import Frame, Region, number, validate_records
from .storage import atomic_write_json, read_json, stable_id


@dataclass(frozen=True)
class ExtractionConfig:
    fps: float = 3
    refine_fps: float = 10
    change_threshold: float = .012
    max_height: int = 1080
    start: float = 0
    end: float | None = None

    def __post_init__(self):
        number(self.fps, 'fps', maximum=30)
        number(self.refine_fps, 'refine_fps', maximum=60)
        number(self.change_threshold, 'change_threshold', maximum=1)
        number(self.start, 'start')
        if not self.fps or self.refine_fps < self.fps or type(self.max_height) is not int or self.max_height < 16:
            raise ValueError('invalid extraction rates or maximum height')
        if self.end is not None:
            number(self.end, 'end')
            if self.end <= self.start:
                raise ValueError('end must be later than start')


def png_gray(path, pixels, width, height):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    scanlines = b''.join(b'\x00' + pixels[i*width:(i+1)*width] for i in range(height))
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
    data += chunk(b'IDAT', zlib.compress(scanlines)) + chunk(b'IEND', b'')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.png.tmp')
    temporary.write_bytes(data)
    os.replace(temporary, path)


def signature(pixels, width, height):
    # Spatial samples avoid a dependency on an image/array runtime.
    return bytes(pixels[y * width + x] for y in range(0, height, max(1, height // 48))
                 for x in range(0, width, max(1, width // 256)))


def change_score(a, b):
    if a is None:
        return 1.0
    differences = [abs(x-y) for x, y in zip(a, b)]
    # A short subtitle can occupy <1% of the crop. Local blocks prevent its
    # changes from being diluted by an otherwise static recording background.
    overall = sum(differences)/(255*len(differences))
    local = max(sum(differences[i:i+32])/(255*len(differences[i:i+32]))
                for i in range(0, len(differences), 32))
    return max(overall, local)


def _crop_dimensions(metadata, region, max_height):
    scale = min(1, max_height / metadata['height'])
    full_w, full_h = max(1, round(metadata['width']*scale)), max(1, round(metadata['height']*scale))
    return full_w, full_h, region.pixels(full_w, full_h)


def crop_stream(video, metadata, region, config, *, start=None, end=None, fps=None):
    binary = shutil.which('ffmpeg')
    if not binary:
        raise StageError('ffmpeg_missing', 'FFmpeg is required to extract frames.', stage='frames')
    start = config.start if start is None else start
    end = min(metadata['duration'], config.end or metadata['duration']) if end is None else end
    fps = fps or config.fps
    full_w, full_h, (x, y, width, height) = _crop_dimensions(metadata, region, config.max_height)
    filters = f'scale={full_w}:{full_h},crop={width}:{height}:{x}:{y}:exact=1,fps={fps},format=gray'
    command = [binary, '-v', 'error', '-nostdin', '-ss', str(start), '-i', str(video), '-t', str(end-start),
               '-vf', filters, '-an', '-sn', '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        index = 0
        while True:
            buffer = bytearray()
            deadline = time.monotonic() + 60
            while len(buffer) < width * height:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                    raise StageError('video_decode_failed', 'Frame decoder timed out.', stage='frames', artifact=video)
                data = os.read(process.stdout.fileno(), min(65536, width*height-len(buffer)))
                if not data:
                    break
                buffer.extend(data)
            if not buffer:
                break
            if len(buffer) != width*height:
                raise StageError('video_decode_failed', 'Incomplete decoded frame.', stage='frames', artifact=video)
            timestamp = round(start + index/fps, 6)
            if timestamp < end:
                yield timestamp, bytes(buffer), width, height
            index += 1
        if process.wait(timeout=15):
            raise StageError('video_decode_failed', 'FFmpeg could not decode the video.', stage='frames', artifact=video)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=15)
        process.stdout.close()


def validate_frame_index(payload, root):
    if payload.get('schema_version') != 1 or payload.get('status') != 'valid' or not payload.get('frames'):
        raise ValueError('missing/partial frame index')
    frames = [Frame.from_dict(f) for f in payload['frames']]
    validate_records(frames, root=root, duration=payload['duration'])
    frame_ids = {f.frame_id for f in frames}
    samples = payload['samples']
    if not samples or [s['timestamp'] for s in samples] != sorted(s['timestamp'] for s in samples):
        raise ValueError('invalid sampling timeline')
    for sample in samples:
        number(sample['timestamp'], 'sample timestamp', maximum=payload['duration'])
        if sample['frame_id'] not in frame_ids:
            raise ValueError('unknown sampled frame')
    for frame in frames:
        if payload['image_checksums'][frame.frame_id] != file_checksum(Path(root) / frame.image):
            raise ValueError('evidence checksum mismatch')
    return frames


def extract_frames(video, output_dir, *, region=None, config=None, metadata=None, force=False):
    region, config = region or Region(), config or ExtractionConfig()
    metadata = metadata or probe_video(video)
    root = Path(output_dir).resolve()
    end = min(config.end or metadata['duration'], metadata['duration'])
    if config.start >= end:
        raise ValueError('sampling range lies outside the video')
    settings = {'config': asdict(config), 'region': asdict(region), 'video_checksum': metadata['checksum'],
                'ffmpeg': executable_version('ffmpeg'), 'algorithm': 2}
    fingerprint = stable_id(json.dumps(settings, sort_keys=True), 32)
    index_path = root / 'frames.index.json'
    if index_path.is_file() and not force:
        try:
            cached = read_json(index_path)
            if cached['fingerprint'] == fingerprint:
                validate_frame_index(cached, root)
                return {**cached, 'cache_hit': True}
        except (ValueError, KeyError, OSError, TypeError):
            pass
    # Each generation has its own immutable evidence names. Existing review paths survive.
    generation = fingerprint + (f'-{time.time_ns()}' if force else '')
    retained, samples, transitions = {}, {}, []
    previous, previous_time, active = None, None, None
    def retain(t, pixels, w, h, score, reason):
        frame_id = 'f' + f'{round(t*1000000):012d}'
        image = f'frames/{generation}/{frame_id}.png'
        png_gray(root / image, pixels, w, h)
        retained[t] = Frame(frame_id, t, image, region, reason, score)
        return frame_id
    for timestamp, pixels, width, height in crop_stream(video, metadata, region, config):
        current = signature(pixels, width, height)
        score = change_score(previous, current)
        if previous is None or score >= config.change_threshold:
            active = retain(timestamp, pixels, width, height, score, 'initial' if previous is None else 'subtitle_region_changed')
            if previous_time is not None:
                transitions.append((previous_time, timestamp))
        samples[timestamp] = active
        previous, previous_time = current, timestamp
    for left, right in transitions:
        # Keep dense transition evidence, including unchanged boundary neighbors.
        for timestamp, pixels, width, height in crop_stream(video, metadata, region, config,
                                                           start=left, end=min(end, right+1/config.refine_fps), fps=config.refine_fps):
            active = retain(timestamp, pixels, width, height, 0, 'transition_refinement')
            samples[timestamp] = active
    frames = [retained[t] for t in sorted(retained)]
    if not frames:
        raise StageError('video_decode_failed', 'No video frames decoded.', stage='frames', artifact=video)
    payload = {'schema_version': 1, 'status': 'valid', 'fingerprint': fingerprint, 'settings': settings,
               'duration': metadata['duration'], 'sample_range': [config.start, end],
               'frames': [f.to_dict() for f in frames],
               'samples': [{'timestamp': t, 'frame_id': samples[t]} for t in sorted(samples)],
               'image_checksums': {f.frame_id: file_checksum(root / f.image) for f in frames},
               'inspected_frames': len(samples), 'retained_frames': len(frames)}
    validate_frame_index(payload, root)
    atomic_write_json(index_path, payload)
    return {**payload, 'cache_hit': False}
