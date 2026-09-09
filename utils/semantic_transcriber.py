"""Semantic transcription branch for the Transcribe + LLM pipeline.

This module owns the canonical whole-program transcript produced by
``gpt-transcribe`` via ``/v1/audio/transcriptions``:

- whole-file requests when the prepared audio is at or below 24 MB;
- natural-boundary chunking above that ceiling, with a compact
  previous-chunk tail passed as context and a small audio overlap that is
  deduplicated during stitching;
- ``prompt`` / ``keywords`` / ``languages`` normalization and validation;
- resumable per-chunk checkpoints keyed by a stable identity hash.

``gpt-transcribe`` does not return timestamps; chunk start/end values in the
artifact describe application-made cuts only. Timing always comes from the
separate timing backbone.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from typing import Callable, List, Optional, Sequence

from . import audio_splitter
from .subtitle_windows import atomic_write_json, load_json_if_exists


SEMANTIC_MODEL_DEFAULT = "gpt-transcribe"
SCHEMA_VERSION = 1
PIPELINE_VERSION = "transcribe_llm_v1"
PROMPT_VERSION = "semantic-2026-07-30.1"

# The API accepts files up to 25 MB; stay conservatively below it.
MAX_WHOLE_FILE_BYTES = 24 * 1024 * 1024
# Audio overlap retained between adjacent semantic chunks for boundary
# recovery; the overlapping text is deduplicated during stitching.
CHUNK_OVERLAP_SEC = 4.0
# Natural-boundary search radius around an even-split cut point.
BOUNDARY_SEARCH_SEC = 20.0
# Compact previous-chunk tail forwarded as context to the next chunk.
CONTEXT_TAIL_CHARS = 400
DEFAULT_MAX_RETRIES = 3

_CJK_THAI_RE = re.compile(r"[฀-๿぀-ヿ㐀-鿿豈-﫿]")

ProgressCallback = Callable[[str], None]


class SemanticTranscriptionError(RuntimeError):
    """Raised when the semantic branch cannot produce a canonical transcript.

    The checkpoint keeps every completed chunk, so rerunning the stage with
    the same inputs resumes from the failed chunk instead of restarting.
    """


def normalize_languages(languages) -> List[str]:
    """Validate expected source languages as ISO codes (e.g. th, en, zh-Hans)."""
    if languages is None:
        return []
    if isinstance(languages, str):
        languages = [part.strip() for part in languages.split(",")]
    normalized = []
    for raw in languages:
        code = str(raw or "").strip()
        if not code:
            continue
        if not re.match(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$", code):
            raise ValueError(f"Unsupported source language code: {code!r}")
        parts = code.split("-")
        canonical = "-".join([parts[0].lower()] + parts[1:])
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized


def normalize_keywords(keywords) -> List[str]:
    """Validate literal keywords: non-empty, single-line, deduplicated."""
    if keywords is None:
        return []
    if isinstance(keywords, str):
        keywords = [keywords]
    normalized = []
    for raw in keywords:
        if raw is None:
            continue
        keyword = str(raw).strip()
        if not keyword:
            continue
        if "\n" in keyword or "\r" in keyword:
            raise ValueError(f"Keywords must be single-line literals: {raw!r}")
        if keyword not in normalized:
            normalized.append(keyword)
    return normalized


def build_prompt(base_prompt=None, previous_tail=None) -> Optional[str]:
    """Combine user/base context with the previous chunk's compact tail.

    The prompt carries context only (title, subject, prior text); it must not
    restate the transcription task.
    """
    parts = []
    base = str(base_prompt or "").strip()
    if base:
        parts.append(base)
    tail = str(previous_tail or "").strip()
    if tail:
        parts.append(tail[-CONTEXT_TAIL_CHARS:])
    if not parts:
        return None
    return "\n".join(parts)


def hash_file(path, block_size=1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def plan_chunks(total_duration_sec, file_size_bytes, *,
                max_bytes=MAX_WHOLE_FILE_BYTES,
                overlap_sec=CHUNK_OVERLAP_SEC,
                natural_boundaries: Optional[Sequence[float]] = None):
    """Plan semantic chunk cut points.

    Returns None when the whole file fits in one request; otherwise a list of
    (start_sec, end_sec) tuples in source order. Cuts snap to the nearest
    provided natural boundary (silence / speech gap) within a bounded search
    radius, and each chunk keeps a small leading overlap for boundary
    recovery.
    """
    if file_size_bytes <= max_bytes:
        return None
    if not total_duration_sec or total_duration_sec <= 0:
        raise ValueError("Cannot plan semantic chunks without a positive audio duration.")

    chunk_count = int(file_size_bytes // max_bytes) + 1
    even_duration = float(total_duration_sec) / chunk_count
    boundaries = sorted(float(b) for b in (natural_boundaries or []))

    cut_points = []
    for index in range(1, chunk_count):
        target = even_duration * index
        chosen = target
        best_distance = BOUNDARY_SEARCH_SEC
        for boundary in boundaries:
            distance = abs(boundary - target)
            if distance <= best_distance and 0 < boundary < total_duration_sec:
                chosen = boundary
                best_distance = distance
        if cut_points and chosen <= cut_points[-1]:
            chosen = min(total_duration_sec, cut_points[-1] + 1.0)
        cut_points.append(chosen)

    chunks = []
    previous_cut = 0.0
    for cut in cut_points + [float(total_duration_sec)]:
        start = max(0.0, previous_cut - (overlap_sec if chunks else 0.0))
        chunks.append((round(start, 3), round(cut, 3)))
        previous_cut = cut
    return chunks


def _looks_cjk_boundary(previous_text, next_text):
    if not previous_text or not next_text:
        return False
    return bool(_CJK_THAI_RE.match(next_text[0])) and bool(
        _CJK_THAI_RE.search(previous_text[-1])
    )


def _tokenize_for_overlap(text):
    tokens = []
    for match in re.finditer(r"[A-Za-z0-9]+|\S", text or ""):
        tokens.append(match.group(0))
    return tokens


def dedupe_overlap(previous_text, next_text, max_overlap_tokens=80) -> str:
    """Remove text at the head of next_text that repeats the tail of previous_text."""
    previous_tokens = _tokenize_for_overlap(previous_text)
    next_tokens = _tokenize_for_overlap(next_text)
    if not previous_tokens or not next_tokens:
        return (next_text or "").strip()

    limit = min(max_overlap_tokens, len(previous_tokens), len(next_tokens))
    best = 0
    # Require at least a two-token overlap so ordinary repeated words are not
    # mistaken for chunk-boundary duplication.
    for size in range(limit, 1, -1):
        if previous_tokens[-size:] == next_tokens[:size]:
            best = size
            break
    if not best:
        return (next_text or "").strip()

    remainder = next_tokens[best:]
    if not remainder:
        return ""
    # Locate the character position of the first surviving token so spacing
    # and punctuation in the original chunk text are preserved.
    seen = 0
    for match in re.finditer(r"[A-Za-z0-9]+|\S", next_text):
        if seen == best:
            return next_text[match.start():].strip()
        seen += 1
    return " ".join(remainder)


def stitch_chunk_texts(texts) -> str:
    """Join chunk texts in source order, deduplicating boundary overlap."""
    stitched = ""
    for text in texts:
        cleaned = str(text or "").strip()
        if not cleaned:
            continue
        if not stitched:
            stitched = cleaned
            continue
        deduped = dedupe_overlap(stitched, cleaned)
        if not deduped:
            continue
        joiner = "" if _looks_cjk_boundary(stitched, deduped) else " "
        stitched = f"{stitched}{joiner}{deduped}"
    return stitched


def _response_text(response) -> str:
    if isinstance(response, dict):
        return str(response.get("text") or "").strip()
    return str(getattr(response, "text", "") or "").strip()


def _response_languages(response) -> List[dict]:
    if isinstance(response, dict):
        raw = response.get("languages") or response.get("language")
    else:
        raw = getattr(response, "languages", None) or getattr(response, "language", None)
    if not raw:
        return []
    if isinstance(raw, str):
        return [{"code": raw}]
    detected = []
    for item in raw:
        if isinstance(item, str):
            detected.append({"code": item})
        elif isinstance(item, dict) and item.get("code"):
            detected.append({"code": str(item["code"])})
        else:
            code = getattr(item, "code", None)
            if code:
                detected.append({"code": str(code)})
    return detected


def default_extract_chunk(audio_path, start_sec, end_sec, output_path):
    """Extract one semantic chunk with ffmpeg, re-encoding for clean cuts."""
    command = [
        "ffmpeg", "-y",
        "-ss", str(float(start_sec)),
        "-t", str(float(end_sec) - float(start_sec)),
        "-i", audio_path,
        "-acodec", "aac",
        output_path,
    ]
    subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return output_path


def _request_transcription(client, file_handle, *, model, prompt, keywords, languages):
    request = {
        "model": model,
        "file": file_handle,
        "response_format": "json",
    }
    if prompt:
        request["prompt"] = prompt
    extra_body = {}
    if keywords:
        extra_body["keywords"] = list(keywords)
    if languages:
        # The multi-language field replaces the legacy single `language`
        # parameter; they must never be sent together.
        extra_body["languages"] = list(languages)
    if extra_body:
        request["extra_body"] = extra_body
    return client.audio.transcriptions.create(**request)


def _call_with_retries(callable_fn, *, description, max_retries, progress_callback):
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return callable_fn()
        except Exception as exc:  # transient API failures included
            last_error = exc
            if attempt >= max_retries:
                break
            delay = min(8.0, 2.0 ** attempt)
            progress_callback(
                f"{description} attempt {attempt + 1} failed: {exc}. "
                f"Retrying in {delay:g}s..."
            )
            time.sleep(delay)
    raise SemanticTranscriptionError(
        f"{description} failed after retries: {last_error}"
    ) from last_error


def compute_identity(*, audio_hash, model, prompt, keywords, languages, chunk_plan):
    payload = {
        "pipeline_version": PIPELINE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "audio_hash": audio_hash,
        "model": model,
        "prompt": prompt or "",
        "keywords": list(keywords or []),
        "languages": list(languages or []),
        "chunk_plan": chunk_plan,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _new_artifact(identity, *, audio_hash, model, prompt, keywords, languages, chunk_plan):
    if chunk_plan is None:
        chunks = [{
            "index": 0,
            "audio_start": None,
            "audio_end": None,
            "whole_file": True,
            "text": None,
            "status": "pending",
        }]
    else:
        chunks = [
            {
                "index": index,
                "audio_start": start,
                "audio_end": end,
                "whole_file": False,
                "text": None,
                "status": "pending",
            }
            for index, (start, end) in enumerate(chunk_plan)
        ]
    return {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "identity": identity,
        "audio_hash": audio_hash,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "prompt": prompt or "",
        "keywords": list(keywords or []),
        "languages_requested": list(languages or []),
        "languages_detected": [],
        "chunks": chunks,
        "canonical_text": None,
        "complete": False,
    }


def _load_or_create_artifact(checkpoint_path, identity, progress_callback, **kwargs):
    existing = load_json_if_exists(checkpoint_path)
    if existing and existing.get("identity") == identity:
        done = sum(1 for chunk in existing.get("chunks", []) if chunk.get("status") == "complete")
        progress_callback(
            f"Semantic transcript checkpoint found: {done}/{len(existing.get('chunks', []))} "
            "chunk(s) complete."
        )
        return existing
    if existing:
        progress_callback(
            "Semantic transcription settings/audio changed; rebuilding its checkpoint."
        )
    return _new_artifact(identity, **kwargs)


def transcribe_semantic(client, audio_path, *,
                        model=SEMANTIC_MODEL_DEFAULT,
                        prompt=None,
                        keywords=None,
                        languages=None,
                        checkpoint_path=None,
                        max_bytes=MAX_WHOLE_FILE_BYTES,
                        natural_boundaries=None,
                        max_retries=DEFAULT_MAX_RETRIES,
                        get_duration=None,
                        extract_chunk=None,
                        work_dir=None,
                        progress_callback=print) -> dict:
    """Produce the canonical whole-program transcript artifact.

    Returns the normalized artifact dict (also persisted to checkpoint_path
    when provided). Raises SemanticTranscriptionError on unrecoverable
    failure; completed chunks stay in the checkpoint for resume.
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    keywords = normalize_keywords(keywords)
    languages = normalize_languages(languages)
    prompt = str(prompt or "").strip() or None
    get_duration = get_duration or audio_splitter.get_audio_duration
    extract_chunk = extract_chunk or default_extract_chunk

    file_size = os.path.getsize(audio_path)
    audio_hash = hash_file(audio_path)

    if file_size <= max_bytes:
        chunk_plan = None
    else:
        duration = get_duration(audio_path)
        if not duration:
            raise SemanticTranscriptionError(
                "Cannot chunk semantic transcription: audio duration unavailable."
            )
        chunk_plan = plan_chunks(
            duration,
            file_size,
            max_bytes=max_bytes,
            natural_boundaries=natural_boundaries,
        )

    identity = compute_identity(
        audio_hash=audio_hash,
        model=model,
        prompt=prompt,
        keywords=keywords,
        languages=languages,
        chunk_plan=chunk_plan,
    )
    artifact = _load_or_create_artifact(
        checkpoint_path,
        identity,
        progress_callback,
        audio_hash=audio_hash,
        model=model,
        prompt=prompt,
        keywords=keywords,
        languages=languages,
        chunk_plan=chunk_plan,
    )

    def save():
        if checkpoint_path:
            atomic_write_json(checkpoint_path, artifact, prefix=".semantic-")

    if artifact.get("complete") and artifact.get("canonical_text"):
        progress_callback("Reusing completed semantic transcript checkpoint.")
        return artifact

    detected = {entry["code"]: entry for entry in artifact.get("languages_detected", [])}

    if chunk_plan is None:
        chunk = artifact["chunks"][0]
        if chunk.get("status") != "complete":
            progress_callback(
                f"Semantic transcription: whole file "
                f"({file_size / (1024 * 1024):.1f} MB) with {model}..."
            )

            def request_whole():
                with open(audio_path, "rb") as handle:
                    return _request_transcription(
                        client,
                        handle,
                        model=model,
                        prompt=build_prompt(prompt),
                        keywords=keywords,
                        languages=languages,
                    )

            response = _call_with_retries(
                request_whole,
                description="Semantic transcription (whole file)",
                max_retries=max_retries,
                progress_callback=progress_callback,
            )
            text = _response_text(response)
            if not text:
                raise SemanticTranscriptionError(
                    "Semantic transcription returned an empty transcript."
                )
            chunk.update({"text": text, "status": "complete"})
            for entry in _response_languages(response):
                detected.setdefault(entry["code"], entry)
            artifact["languages_detected"] = list(detected.values())
            save()
    else:
        progress_callback(
            f"Semantic transcription: {len(chunk_plan)} natural-boundary chunk(s) "
            f"({file_size / (1024 * 1024):.1f} MB total) with {model}..."
        )
        own_work_dir = None
        if work_dir is None:
            import tempfile
            own_work_dir = tempfile.mkdtemp(prefix="llm_subs_semantic_")
            work_dir = own_work_dir
        try:
            for chunk in artifact["chunks"]:
                if chunk.get("status") == "complete":
                    continue
                index = chunk["index"]
                previous_tail = ""
                if index > 0:
                    previous = artifact["chunks"][index - 1]
                    if previous.get("status") != "complete":
                        raise SemanticTranscriptionError(
                            "Semantic chunks must complete in source order; "
                            f"chunk {index - 1} is incomplete."
                        )
                    previous_tail = str(previous.get("text") or "")
                chunk_path = os.path.join(work_dir, f"semantic_chunk_{index:03d}.m4a")
                progress_callback(
                    f"Semantic chunk {index + 1}/{len(artifact['chunks'])}: "
                    f"{chunk['audio_start']:.1f}s - {chunk['audio_end']:.1f}s"
                )

                def request_chunk():
                    extract_chunk(
                        audio_path, chunk["audio_start"], chunk["audio_end"], chunk_path
                    )
                    try:
                        with open(chunk_path, "rb") as handle:
                            return _request_transcription(
                                client,
                                handle,
                                model=model,
                                prompt=build_prompt(prompt, previous_tail),
                                keywords=keywords,
                                languages=languages,
                            )
                    finally:
                        try:
                            os.remove(chunk_path)
                        except OSError:
                            pass

                response = _call_with_retries(
                    request_chunk,
                    description=f"Semantic transcription chunk {index + 1}",
                    max_retries=max_retries,
                    progress_callback=progress_callback,
                )
                text = _response_text(response)
                if not text:
                    raise SemanticTranscriptionError(
                        f"Semantic chunk {index + 1} returned an empty transcript."
                    )
                chunk.update({"text": text, "status": "complete"})
                for entry in _response_languages(response):
                    detected.setdefault(entry["code"], entry)
                artifact["languages_detected"] = list(detected.values())
                save()
        finally:
            if own_work_dir:
                import shutil
                shutil.rmtree(own_work_dir, ignore_errors=True)

    canonical = stitch_chunk_texts(
        chunk.get("text") for chunk in artifact["chunks"]
    )
    if not canonical:
        raise SemanticTranscriptionError(
            "Stitched canonical transcript is empty."
        )
    artifact["canonical_text"] = canonical
    artifact["complete"] = True
    save()
    progress_callback(
        f"Semantic transcript complete: {len(canonical)} characters, "
        f"{len(artifact['chunks'])} chunk(s)."
    )
    return artifact
