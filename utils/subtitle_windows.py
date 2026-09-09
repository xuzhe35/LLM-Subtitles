"""Deterministic subtitle-window primitives shared by the Realtime polisher
and the Transcribe + LLM contextual translator.

These helpers own the parts of window-based subtitle editing that must never
depend on a model: stable cue windows, exact cue-ID coverage validation,
trusted timestamp rebuilding, and atomic checkpoint writes. Prompts and
schemas stay in the calling modules.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Callable, List, Optional


DEFAULT_WINDOW_CUES = 80
DEFAULT_WINDOW_DURATION_SEC = 8 * 60
DEFAULT_CONTEXT_CUES = 6
MAX_MERGED_CUE_DURATION_SEC = 15.0

_SENTENCE_END_RE = re.compile(r"[.!?。！？…][\"'”’）】》」』]*$")


@dataclass(frozen=True)
class CueWindow:
    index: int
    core_start: int
    core_end: int
    context_start: int
    context_end: int

    @property
    def core_slice(self):
        return slice(self.core_start, self.core_end)

    @property
    def context_slice(self):
        return slice(self.context_start, self.context_end)


def _clean_text(value):
    return str(value or "").strip()


def _is_natural_boundary(cues, end_index, text_key):
    if end_index <= 0 or end_index >= len(cues):
        return True
    previous = cues[end_index - 1]
    following = cues[end_index]
    if _SENTENCE_END_RE.search(_clean_text(previous.get(text_key))):
        return True
    return float(following["start"]) - float(previous["end"]) >= 1.0


def _evidence_span(cues, start, end):
    owned = cues[start:end]
    if not owned:
        return 0.0
    return (
        max(float(cue["end"]) for cue in owned)
        - min(float(cue["start"]) for cue in owned)
    )


def plan_cue_windows(cues, max_cues=DEFAULT_WINDOW_CUES,
                     max_duration_sec=DEFAULT_WINDOW_DURATION_SEC,
                     context_cues=DEFAULT_CONTEXT_CUES,
                     text_key="text") -> List[CueWindow]:
    """Create non-overlapping owned windows with punctuation-aware boundaries."""
    if max_cues < 2:
        raise ValueError("max_cues must be at least 2.")
    if max_duration_sec <= 0:
        raise ValueError("max_duration_sec must be positive.")
    if context_cues < 0:
        raise ValueError("context_cues cannot be negative.")

    windows = []
    start = 0
    while start < len(cues):
        hard_end = min(len(cues), start + int(max_cues))
        while (
            hard_end > start + 1
            and _evidence_span(cues, start, hard_end) > float(max_duration_sec)
        ):
            hard_end -= 1

        end = hard_end
        if hard_end < len(cues):
            minimum_end = start + max(2, min(20, int(max_cues) // 2))
            for candidate in range(hard_end, minimum_end - 1, -1):
                if _is_natural_boundary(cues, candidate, text_key):
                    end = candidate
                    break

        if end <= start:
            end = min(len(cues), start + 1)
        windows.append(CueWindow(
            index=len(windows),
            core_start=start,
            core_end=end,
            context_start=max(0, start - int(context_cues)),
            context_end=min(len(cues), end + int(context_cues)),
        ))
        start = end

    covered = [index for window in windows for index in range(window.core_start, window.core_end)]
    if covered != list(range(len(cues))):
        raise AssertionError("Window plan does not cover every cue exactly once.")
    return windows


def validate_and_materialize_window(raw_result, core_cues, max_merge_cues=8,
                                    max_merged_duration_sec=MAX_MERGED_CUE_DURATION_SEC,
                                    forbid_cross_speaker_merge=False,
                                    allow_empty_text=False):
    """Enforce exact, ordered, adjacent cue coverage and rebuild trusted timing.

    The model output must never contain timestamps; final times are rebuilt
    from the minimum start and maximum end of all referenced source cues.

    With allow_empty_text, an output cue whose text is empty is a deliberate
    drop (for ASR hallucinations and other untranslatable noise): it still
    consumes its source IDs so coverage stays exact, materializes with
    ``"dropped": True``, and skips the display-only duration and speaker
    merge rules because nothing is ever shown for it.
    """
    if not isinstance(raw_result, dict) or not isinstance(raw_result.get("cues"), list):
        raise ValueError("Window output must contain a cues array.")
    core_by_id = {cue["id"]: cue for cue in core_cues}
    expected_ids = [cue["id"] for cue in core_cues]
    flattened = []
    materialized = []

    for item in raw_result["cues"]:
        if not isinstance(item, dict):
            raise ValueError("Each output cue must be an object.")
        source_ids = item.get("source_ids")
        text = _clean_text(item.get("text"))
        if not isinstance(source_ids, list) or not source_ids:
            raise ValueError("Each output cue must reference at least one source ID.")
        if len(source_ids) > max_merge_cues:
            raise ValueError(f"A cue may merge at most {max_merge_cues} source cues.")
        dropped = allow_empty_text and not text
        if not text and not allow_empty_text:
            raise ValueError("Subtitle text cannot be empty.")
        if any(source_id not in core_by_id for source_id in source_ids):
            raise ValueError("A cue referenced an ID outside its owned window.")
        if len(set(source_ids)) != len(source_ids) or any(
            source_id in flattened for source_id in source_ids
        ):
            raise ValueError("Every owned cue ID must appear exactly once.")

        indices = [core_by_id[source_id]["index"] for source_id in source_ids]
        if indices != list(range(indices[0], indices[0] + len(indices))):
            raise ValueError("Only adjacent source cue IDs may be merged.")
        first = core_by_id[source_ids[0]]
        sources = [core_by_id[source_id] for source_id in source_ids]
        trusted_start = min(float(source["start"]) for source in sources)
        trusted_end = max(float(source["end"]) for source in sources)
        if (
            not dropped
            and len(source_ids) > 1
            and trusted_end - trusted_start > max_merged_duration_sec
        ):
            raise ValueError(
                f"Merged subtitles may span at most {max_merged_duration_sec:g} seconds."
            )
        if forbid_cross_speaker_merge and not dropped and len(source_ids) > 1:
            speakers = {core_by_id[source_id].get("speaker") for source_id in source_ids}
            if len(speakers) > 1:
                raise ValueError("Merged subtitles must never cross a speaker change.")
        flattened.extend(source_ids)
        entry = {
            "start": trusted_start,
            "end": trusted_end,
            "text": text,
            "source_ids": source_ids,
        }
        if dropped:
            entry["dropped"] = True
        if forbid_cross_speaker_merge or first.get("speaker") is not None:
            entry["speaker"] = first.get("speaker")
        materialized.append(entry)

    if flattened != expected_ids:
        raise ValueError(
            "Output must cover every owned cue exactly once and in source order."
        )
    return materialized


def atomic_write_json(path, payload, prefix=".checkpoint-"):
    """Write JSON so a crash can never leave a truncated artifact behind."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=directory,
        prefix=prefix,
        suffix=".tmp",
        delete=False,
    )
    temp_path = handle.name
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def load_json_if_exists(path) -> Optional[dict]:
    """Read a JSON artifact, returning None when missing or unreadable."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None
