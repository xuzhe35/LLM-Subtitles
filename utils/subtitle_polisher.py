"""Context-aware, resumable polishing for Realtime Translation subtitle JSON."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import subtitle_windows
from .subtitle_windows import (
    DEFAULT_CONTEXT_CUES,
    DEFAULT_WINDOW_CUES,
    DEFAULT_WINDOW_DURATION_SEC,
    MAX_MERGED_CUE_DURATION_SEC,
)


DEFAULT_POLISH_MODEL = "gpt-5.6"
DEFAULT_MAX_RETRIES = 3
CHECKPOINT_SCHEMA_VERSION = 1
POLISH_PIPELINE_VERSION = "2026-07-13.2"

ProgressCallback = Callable[[str], None]

_LEADING_CONTINUATION_RE = re.compile(r"^[,，、:：;；)）】》」』]")
_VISIBLE_CHAR_RE = re.compile(r"\s+")

# Shared deterministic window primitives live in subtitle_windows so the
# Transcribe + LLM route reuses the exact same proven logic. These aliases
# keep the polisher's long-standing public names stable.
PolishWindow = subtitle_windows.CueWindow


GLOBAL_CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "tone_and_register": {"type": "string"},
        "language_rules": {"type": "array", "items": {"type": "string"}},
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_or_raw": {"type": "string"},
                    "preferred": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["source_or_raw", "preferred", "note"],
                "additionalProperties": False,
            },
        },
        "recurring_expressions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string"},
                    "treatment": {"type": "string"},
                },
                "required": ["expression", "treatment"],
                "additionalProperties": False,
            },
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "tone_and_register",
        "language_rules",
        "terms",
        "recurring_expressions",
        "uncertainties",
    ],
    "additionalProperties": False,
}


POLISHED_WINDOW_SCHEMA = {
    "type": "object",
    "properties": {
        "cues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                    "text": {"type": "string"},
                },
                "required": ["source_ids", "text"],
                "additionalProperties": False,
            },
        },
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["cues", "issues"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class PolishResult:
    translated_segments: list
    global_context: dict
    quality_report: dict
    model: str
    target_language: str
    checkpoint_path: Optional[str] = None

    def to_metadata(self, raw_metadata=None):
        raw_metadata = raw_metadata or {}
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "pipeline_version": POLISH_PIPELINE_VERSION,
            "source_model": str(raw_metadata.get("model") or ""),
            "polish_model": self.model,
            "target_language": self.target_language,
            "duration": float(raw_metadata.get("duration") or 0.0),
            "source_text": str(raw_metadata.get("source_text") or ""),
            "source_segments": list(raw_metadata.get("source_segments") or []),
            "raw_translated_text": str(raw_metadata.get("translated_text") or ""),
            "translated_text": " ".join(
                item["text"] for item in self.translated_segments if item.get("text")
            ).strip(),
            "translated_segments": self.translated_segments,
            "global_context": self.global_context,
            "quality_report": self.quality_report,
            "raw_metadata_preserved": True,
        }


def _clean_text(value):
    return str(value or "").strip()


def _visible_length(text):
    return len(_VISIBLE_CHAR_RE.sub("", _clean_text(text)))


def build_cues(translated_segments, source_segments=None):
    """Normalize raw segments and attach stable IDs plus best-effort source text."""
    translated_segments = list(translated_segments or [])
    source_segments = list(source_segments or [])
    cues = []
    same_length = len(source_segments) == len(translated_segments)

    for index, segment in enumerate(translated_segments):
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        if end <= start:
            end = start + 0.05

        source_text = ""
        if same_length:
            source_text = _clean_text(source_segments[index].get("text"))
        elif source_segments:
            overlaps = []
            for source in source_segments:
                source_start = float(source.get("start") or 0.0)
                source_end = float(source.get("end") or source_start)
                if min(end, source_end) > max(start, source_start):
                    text = _clean_text(source.get("text"))
                    if text:
                        overlaps.append(text)
            source_text = " ".join(overlaps)

        cues.append({
            "id": f"cue_{index:06d}",
            "index": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "source": source_text,
            "translation": _clean_text(segment.get("text")),
        })

    if not cues:
        raise ValueError("Cannot polish an empty translated segment list.")
    return cues


def plan_windows(cues, max_cues=DEFAULT_WINDOW_CUES,
                 max_duration_sec=DEFAULT_WINDOW_DURATION_SEC,
                 context_cues=DEFAULT_CONTEXT_CUES):
    """Create non-overlapping owned windows with punctuation-aware boundaries."""
    return subtitle_windows.plan_cue_windows(
        cues,
        max_cues=max_cues,
        max_duration_sec=max_duration_sec,
        context_cues=context_cues,
        text_key="translation",
    )


def subtitle_quality_metrics(segments):
    segments = list(segments or [])
    lengths = [_visible_length(item.get("text")) for item in segments]
    short_count = sum(length <= 3 for length in lengths)
    empty_count = sum(length == 0 for length in lengths)
    leading_continuations = sum(
        bool(_LEADING_CONTINUATION_RE.search(_clean_text(item.get("text"))))
        for item in segments
    )
    overlaps = 0
    max_chars_per_second = 0.0
    previous_end = None
    for item, length in zip(segments, lengths):
        start = float(item.get("start") or 0.0)
        end = float(item.get("end") or start)
        if previous_end is not None and start < previous_end - 0.001:
            overlaps += 1
        previous_end = max(previous_end or end, end)
        max_chars_per_second = max(max_chars_per_second, length / max(0.05, end - start))

    return {
        "segment_count": len(segments),
        "empty_count": empty_count,
        "short_fragment_count": short_count,
        "leading_continuation_count": leading_continuations,
        "overlap_count": overlaps,
        "average_visible_chars": round(sum(lengths) / max(1, len(lengths)), 2),
        "max_visible_chars_per_second": round(max_chars_per_second, 2),
    }


def _identity(cues, target_language, model, max_cues, max_duration_sec, context_cues):
    payload = {
        "pipeline_version": POLISH_PIPELINE_VERSION,
        "target_language": target_language,
        "model": model,
        "max_cues": max_cues,
        "max_duration_sec": max_duration_sec,
        "context_cues": context_cues,
        "cues": cues,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write_json(path, payload):
    subtitle_windows.atomic_write_json(path, payload, prefix=".polish-checkpoint-")


def _new_checkpoint(identity, windows, cues, target_language, model):
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "pipeline_version": POLISH_PIPELINE_VERSION,
        "identity": identity,
        "target_language": target_language,
        "model": model,
        "cue_count": len(cues),
        "global_context": {"status": "pending", "result": None},
        "windows": [
            {
                "index": window.index,
                "core_ids": [cue["id"] for cue in cues[window.core_slice]],
                "status": "pending",
                "result": None,
                "issues": [],
            }
            for window in windows
        ],
        "complete": False,
    }


def _load_or_create_checkpoint(path, identity, windows, cues, target_language,
                               model, progress_callback):
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as checkpoint_file:
                checkpoint = json.load(checkpoint_file)
            expected_ids = [
                [cue["id"] for cue in cues[window.core_slice]] for window in windows
            ]
            actual_ids = [item.get("core_ids") for item in checkpoint.get("windows", [])]
            if checkpoint.get("identity") == identity and actual_ids == expected_ids:
                completed = sum(
                    item.get("status") == "complete" for item in checkpoint["windows"]
                )
                progress_callback(
                    f"Subtitle polish checkpoint found: {completed}/{len(windows)} window(s) complete."
                )
                return checkpoint
            progress_callback("Subtitle polish settings/source changed; rebuilding its checkpoint.")
        except (OSError, ValueError, TypeError) as exc:
            progress_callback(f"Subtitle polish checkpoint is unreadable; rebuilding it: {exc}")

    checkpoint = _new_checkpoint(identity, windows, cues, target_language, model)
    if path:
        _atomic_write_json(path, checkpoint)
    return checkpoint


def _call_structured(client, *, model, instructions, payload, schema_name, schema,
                     max_retries, progress_callback):
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            request = {
                "model": model,
                "instructions": instructions,
                "input": json.dumps(payload, ensure_ascii=False),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    },
                    "verbosity": "low",
                },
                "store": False,
            }
            if model.startswith("gpt-5") or re.match(r"^o\d", model):
                request["reasoning"] = {"effort": "medium"}
            response = client.responses.create(**request)
            output_text = _clean_text(getattr(response, "output_text", ""))
            if not output_text:
                raise ValueError("OpenAI response did not contain structured output text.")
            parsed = json.loads(output_text)
            if not isinstance(parsed, dict):
                raise ValueError("Structured output root must be an object.")
            return parsed
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            delay = min(8.0, 2.0 ** attempt)
            progress_callback(
                f"Subtitle polish API/validation attempt {attempt + 1} failed: {exc}. "
                f"Retrying in {delay:g}s..."
            )
            time.sleep(delay)
    raise RuntimeError(f"Subtitle polishing failed after retries: {last_error}") from last_error


def _global_context_payload(cues, target_language):
    return {
        "target_language": target_language,
        "source_transcript_available": any(cue["source"] for cue in cues),
        "cues": [
            {
                "id": cue["id"],
                "source": cue["source"],
                "raw_translation": cue["translation"],
            }
            for cue in cues
        ],
    }


def _analyze_global_context(client, cues, target_language, model, max_retries,
                            progress_callback):
    instructions = (
        "You are the lead editor for a full-length subtitle translation. Analyze the entire "
        "transcript before any local rewriting. Build a compact, operational context pack for "
        "later subtitle editors: subject and narrative, speaker tone, consistent names and terms, "
        "recurring verbal tics, fillers and onomatopoeia, and ambiguities. If source text is empty, "
        "infer only from the raw target-language translation and explicitly avoid inventing source "
        "meaning. Preserve meaningful oral texture while allowing redundant disfluencies to be "
        "compressed for readability. Return only the requested structured data."
    )
    return _call_structured(
        client,
        model=model,
        instructions=instructions,
        payload=_global_context_payload(cues, target_language),
        schema_name="subtitle_global_context",
        schema=GLOBAL_CONTEXT_SCHEMA,
        max_retries=max_retries,
        progress_callback=progress_callback,
    )


def _window_payload(cues, window, global_context, target_language):
    items = []
    for cue in cues[window.context_slice]:
        if cue["index"] < window.core_start:
            role = "context_before"
        elif cue["index"] >= window.core_end:
            role = "context_after"
        else:
            role = "core"
        items.append({
            "id": cue["id"],
            "role": role,
            "start": cue["start"],
            "end": cue["end"],
            "source": cue["source"],
            "raw_translation": cue["translation"],
        })
    return {
        "target_language": target_language,
        "global_context": global_context,
        "core_ids": [cue["id"] for cue in cues[window.core_slice]],
        "cues": items,
    }


def validate_and_materialize_window(raw_result, core_cues, max_merge_cues=8,
                                    max_merged_duration_sec=MAX_MERGED_CUE_DURATION_SEC):
    """Enforce exact, ordered, adjacent cue coverage and rebuild trusted timing."""
    return subtitle_windows.validate_and_materialize_window(
        raw_result,
        core_cues,
        max_merge_cues=max_merge_cues,
        max_merged_duration_sec=max_merged_duration_sec,
    )


def _polish_window(client, cues, window, global_context, target_language, model,
                   max_retries, progress_callback):
    instructions = (
        "You are a meticulous full-program subtitle editor. Rewrite only cues whose role is core; "
        "before/after cues are reference only. Use the global context for consistent terminology "
        "and voice. Merge adjacent core cues when they form one broken sentence. Repair punctuation, "
        "word order, dangling particles, and chunk-boundary fragments. Keep meaningful emotion, "
        "catchphrases, hesitations, and onomatopoeia; compress only semantically empty repetition. "
        "Do not summarize, omit claims, add facts, or strengthen ambiguity. When source text exists, "
        "use it to correct the raw translation. Every core ID must appear exactly once, IDs must stay "
        "in order, and only adjacent IDs may share one output cue. Never merge IDs whose combined "
        f"time span exceeds {MAX_MERGED_CUE_DURATION_SEC:g} seconds. Prefer readable cues that can fit "
        "on at most two subtitle lines. Do not output timestamps because "
        "the application owns timing. Return only the requested structured data."
    )
    core_cues = cues[window.core_slice]
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            raw = _call_structured(
                client,
                model=model,
                instructions=instructions,
                payload=_window_payload(cues, window, global_context, target_language),
                schema_name="polished_subtitle_window",
                schema=POLISHED_WINDOW_SCHEMA,
                max_retries=0,
                progress_callback=progress_callback,
            )
            return validate_and_materialize_window(raw, core_cues), list(raw.get("issues") or [])
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            progress_callback(
                f"Subtitle polish window {window.index + 1} attempt {attempt + 1} failed: {exc}. "
                "Retrying the window..."
            )
    raise RuntimeError(
        f"Subtitle polish window {window.index + 1} failed validation: {last_error}"
    ) from last_error


def polish_segments(client, translated_segments, *, source_segments=None,
                    target_language="Simplified Chinese", model=DEFAULT_POLISH_MODEL,
                    checkpoint_path=None, max_cues=DEFAULT_WINDOW_CUES,
                    max_duration_sec=DEFAULT_WINDOW_DURATION_SEC,
                    context_cues=DEFAULT_CONTEXT_CUES,
                    max_retries=DEFAULT_MAX_RETRIES, progress_callback=print):
    cues = build_cues(translated_segments, source_segments)
    windows = plan_windows(
        cues,
        max_cues=max_cues,
        max_duration_sec=max_duration_sec,
        context_cues=context_cues,
    )
    identity = _identity(
        cues, target_language, model, max_cues, max_duration_sec, context_cues
    )
    checkpoint = _load_or_create_checkpoint(
        checkpoint_path,
        identity,
        windows,
        cues,
        target_language,
        model,
        progress_callback,
    )

    global_state = checkpoint["global_context"]
    if global_state.get("status") == "complete" and global_state.get("result"):
        global_context = global_state["result"]
        progress_callback("Reusing completed whole-video subtitle context analysis.")
    else:
        progress_callback(
            f"Analyzing whole-video subtitle context ({len(cues)} raw cues)..."
        )
        global_context = _analyze_global_context(
            client,
            cues,
            target_language,
            model,
            max_retries,
            progress_callback,
        )
        global_state.update({"status": "complete", "result": global_context})
        if checkpoint_path:
            _atomic_write_json(checkpoint_path, checkpoint)

    for window, state in zip(windows, checkpoint["windows"]):
        if state.get("status") == "complete" and state.get("result"):
            continue
        progress_callback(
            f"Polishing subtitle window {window.index + 1}/{len(windows)} "
            f"({window.core_end - window.core_start} cues)..."
        )
        polished, issues = _polish_window(
            client,
            cues,
            window,
            global_context,
            target_language,
            model,
            max_retries,
            progress_callback,
        )
        state.update({"status": "complete", "result": polished, "issues": issues})
        if checkpoint_path:
            _atomic_write_json(checkpoint_path, checkpoint)

    translated = []
    for state in checkpoint["windows"]:
        if state.get("status") != "complete" or not state.get("result"):
            raise RuntimeError("Subtitle polish checkpoint still contains an incomplete window.")
        translated.extend(state["result"])

    source_ids = [source_id for item in translated for source_id in item["source_ids"]]
    expected_ids = [cue["id"] for cue in cues]
    if source_ids != expected_ids:
        raise RuntimeError("Combined polished subtitles failed final source coverage validation.")

    checkpoint["complete"] = True
    if checkpoint_path:
        _atomic_write_json(checkpoint_path, checkpoint)

    raw_metrics = subtitle_quality_metrics(translated_segments)
    polished_metrics = subtitle_quality_metrics(translated)
    report = {
        "raw": raw_metrics,
        "polished": polished_metrics,
        "segment_reduction": raw_metrics["segment_count"] - polished_metrics["segment_count"],
        "short_fragment_reduction": (
            raw_metrics["short_fragment_count"] - polished_metrics["short_fragment_count"]
        ),
        "source_transcript_available": any(cue["source"] for cue in cues),
        "window_count": len(windows),
    }
    progress_callback(
        "Subtitle polishing complete: "
        f"{raw_metrics['segment_count']} -> {polished_metrics['segment_count']} cues; "
        f"short fragments {raw_metrics['short_fragment_count']} -> "
        f"{polished_metrics['short_fragment_count']}."
    )
    return PolishResult(
        translated_segments=translated,
        global_context=global_context,
        quality_report=report,
        model=model,
        target_language=target_language,
        checkpoint_path=checkpoint_path,
    )


def polish_realtime_metadata(client, metadata, *, target_language=None,
                             model=DEFAULT_POLISH_MODEL, checkpoint_path=None,
                             max_cues=DEFAULT_WINDOW_CUES,
                             max_duration_sec=DEFAULT_WINDOW_DURATION_SEC,
                             context_cues=DEFAULT_CONTEXT_CUES,
                             max_retries=DEFAULT_MAX_RETRIES,
                             progress_callback=print):
    if not isinstance(metadata, dict):
        raise ValueError("Realtime metadata must be a JSON object.")
    translated_segments = list(metadata.get("translated_segments") or [])
    source_segments = list(metadata.get("source_segments") or [])
    resolved_target = target_language or str(metadata.get("target_language") or "")
    result = polish_segments(
        client,
        translated_segments,
        source_segments=source_segments,
        target_language=resolved_target,
        model=model,
        checkpoint_path=checkpoint_path,
        max_cues=max_cues,
        max_duration_sec=max_duration_sec,
        context_cues=context_cues,
        max_retries=max_retries,
        progress_callback=progress_callback,
    )
    return result, result.to_metadata(metadata)
