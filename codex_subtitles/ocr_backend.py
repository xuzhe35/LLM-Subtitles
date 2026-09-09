"""Local-only OCR adapter. Discovery never compiles or imports heavy runtimes."""
from __future__ import annotations
import json
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Protocol
from .hard_subtitle_errors import StageError
from .hard_subtitle_models import OCRLine, Region
from .video_frame_service import file_checksum

NATIVE_SOURCE = Path(__file__).parent / 'native/vision_ocr.swift'


class OCRBackend(Protocol):
    identity: str
    def recognize(self, image_path: Path, language: str | None = None, options: dict | None = None) -> list[OCRLine]: ...


def available_backends():
    return ['apple-vision'] if platform.system() == 'Darwin' and shutil.which('swiftc') else []


def choose_backend(requested='auto'):
    available = available_backends()
    if requested == 'auto' and available:
        return available[0]
    if requested in available:
        return requested
    raise StageError('ocr_backend_missing', 'No requested local OCR backend is available.', stage='ocr',
                     available_backends=available, next_action='On macOS install the Command Line Tools for Apple Vision; no hosted fallback is available.')


def build_vision_backend(cache_dir='output/local_ocr'):
    choose_backend('apple-vision')
    key = file_checksum(NATIVE_SOURCE)[:16] + '-' + platform.mac_ver()[0]
    root = Path(cache_dir).resolve() / key
    executable = root / 'vision-ocr'
    if executable.is_file():
        return executable
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / 'vision-ocr.partial'
    try:
        subprocess.run([shutil.which('swiftc'), '-module-cache-path', str(root / 'module-cache'),
                        str(NATIVE_SOURCE), '-o', str(temporary)], capture_output=True, text=True, timeout=300, check=True)
        temporary.replace(executable)
    except (OSError, subprocess.SubprocessError) as error:
        raise StageError('ocr_backend_build_failed', 'Apple Vision helper could not be compiled.', stage='ocr',
                         artifact=executable, next_action='Check the local Swift Command Line Tools installation.') from error
    return executable


class VisionBackend:
    # Each recognition call runs in an independent native process.
    max_workers = 3

    def __init__(self, executable=None, *, cache_dir='output/local_ocr', runner=subprocess.run):
        self.executable = Path(executable) if executable else build_vision_backend(cache_dir)
        self.runner = runner
        self.identity = 'apple-vision:' + platform.mac_ver()[0] + ':' + file_checksum(self.executable)[:16]

    def recognize(self, image_path, language=None, options=None):
        image = Path(image_path).resolve()
        if not image.is_file():
            raise StageError('ocr_evidence_missing', 'OCR image is missing.', stage='ocr', artifact=image)
        if options:
            raise ValueError('Apple Vision has no configurable options in schema version 1')
        try:
            result = self.runner([str(self.executable.resolve()), str(image), language or 'auto'],
                                 capture_output=True, text=True, timeout=60, check=True)
            lines = [OCRLine(row['text'], row['confidence'], Region(**row['box'])) for row in json.loads(result.stdout)]
            return sorted(lines, key=lambda line: (round(line.box.y/.025), line.box.x))
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as error:
            raise StageError('ocr_backend_failed', 'Local Apple Vision recognition failed.', stage='ocr', artifact=image,
                             available_backends=available_backends(),
                             next_action='Retry with access to macOS Vision services (the desktop sandbox may block them); inspect the local image.') from error


def create_backend(requested='auto', **kwargs):
    choose_backend(requested)
    return VisionBackend(**kwargs)
