"""Paths for local OCR evidence; independent of source/translation jobs."""
from pathlib import Path


def safe_path(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError('expected a relative evidence path')
    result = (root / value).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError('evidence path escapes the root')
    return result
