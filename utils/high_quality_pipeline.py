"""Orchestrator for the opt-in Transcribe + LLM high-quality subtitle route.

Pipeline shape (see TRANSCRIBE_LLM_PLAN.md):

    prepared audio
    ├── semantic branch: gpt-transcribe -> canonical whole-program transcript
    └── timing branch:  whisper-1 word timestamps or gpt-4o diarized segments
                              ↓
                deterministic monotonic alignment
                              ↓
               whole-program source context analysis
                              ↓
          context-aware window translation (+ selective escalation)
                              ↓
            SRT + bilingual SRT + JSON + quality report

Every stage persists a hash-identified artifact and can be resumed or reused
independently: changing the target language reuses the semantic transcript,
timing, alignment, and source context; changing the timing model reuses the
semantic transcript only; and so on. Timing values only ever come from the
timing backbone — no LLM in this pipeline may create or edit a timestamp.

This module is imported lazily by the router so selecting the Realtime route
never touches the high-quality orchestrator.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import (
    audio_splitter,
    contextual_translator,
    semantic_transcriber,
    subtitle_formatter,
    transcript_aligner,
)
from .subtitle_polisher import subtitle_quality_metrics
from .subtitle_windows import atomic_write_json, load_json_if_exists


PIPELINE_VERSION = "transcribe_llm_v2"
SCHEMA_VERSION = 1

TIMING_MODEL_AUTO = "auto"
TIMING_MODEL_WHISPER = "whisper-1"
TIMING_MODEL_DIARIZE = "gpt-4o-transcribe-diarize"
_VALID_TIMING_MODELS = {TIMING_MODEL_AUTO, TIMING_MODEL_WHISPER, TIMING_MODEL_DIARIZE}

DEFAULT_TIMING_MAX_BYTES = 24 * 1024 * 1024
TIMING_CHUNK_OVERLAP_SEC = 4.0
DEFAULT_MAX_RETRIES = 3
# Hallucination heuristics flag suspect segments; they never remove timing
# anchors before alignment.
HALLUCINATION_MIN_REPEATS = 4
HALLUCINATION_REPEAT_RATIO = 0.15

ProgressCallback = Callable[[str], None]


class TimingTranscriptionError(RuntimeError):
    """The timing backbone failed; high-quality output must stop rather than
    invent timing."""


class HighQualityPipelineError(RuntimeError):
    """A stage failed in strict mode; all completed checkpoints are kept so
    the job is resumable."""


@dataclass(frozen=True)
class HighQualitySettings:
    semantic_model: str = semantic_transcriber.SEMANTIC_MODEL_DEFAULT
    timing_model: str = TIMING_MODEL_AUTO
    source_languages: tuple = ()
    prompt: Optional[str] = None
    keywords: tuple = ()
    context_model: str = contextual_translator.DEFAULT_CONTEXT_MODEL
    translation_model: str = contextual_translator.DEFAULT_TRANSLATION_MODEL
    translation_escalation_model: str = contextual_translator.DEFAULT_ESCALATION_MODEL
    translation_reasoning_effort: str = contextual_translator.DEFAULT_REASONING_EFFORT
    enable_selective_escalation: bool = True
    alignment_confidence_threshold: float = transcript_aligner.DEFAULT_CONFIDENCE_THRESHOLD
    multi_speaker: bool = False
    strict: bool = True
    api_max_retries: int = DEFAULT_MAX_RETRIES

    @classmethod
    def from_mapping(cls, mapping):
        mapping = dict(mapping or {})
        known = {}
        for field_name in cls.__dataclass_fields__:
            if field_name in mapping and mapping[field_name] is not None:
                known[field_name] = mapping[field_name]
        if "source_languages" in known and isinstance(known["source_languages"], list):
            known["source_languages"] = tuple(known["source_languages"])
        if "keywords" in known and isinstance(known["keywords"], list):
            known["keywords"] = tuple(known["keywords"])
        return cls(**known)


@dataclass(frozen=True)
class HighQualityArtifacts:
    translated_srt: str
    bilingual_srt: str
    translated_json: str
    quality_json: str
    semantic_json: str
    timing_json: str
    aligned_json: str
    source_context_json: str
    target_policy_json: str


def resolve_timing_model(requested=TIMING_MODEL_AUTO, multi_speaker=False):
    """auto -> whisper-1 unless speaker labels were requested."""
    requested = (requested or TIMING_MODEL_AUTO).strip().lower()
    if requested not in _VALID_TIMING_MODELS:
        raise ValueError(f"Unsupported timing model: {requested}")
    if requested != TIMING_MODEL_AUTO:
        return requested
    return TIMING_MODEL_DIARIZE if multi_speaker else TIMING_MODEL_WHISPER


# ---------------------------------------------------------------------------
# Timing backbone
# ---------------------------------------------------------------------------

def _get_attr(item, name, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _normalize_whisper_response(response, time_offset):
    """Normalize verbose_json segments + words to absolute media time."""
    segments = []
    for segment in _get_attr(response, "segments", None) or []:
        start = _get_attr(segment, "start")
        end = _get_attr(segment, "end")
        text = str(_get_attr(segment, "text", "") or "").strip()
        if start is None or end is None or not text:
            continue
        segments.append({
            "start": float(start) + time_offset,
            "end": float(end) + time_offset,
            "text": text,
            "speaker": None,
            "words": [],
        })

    words = _get_attr(response, "words", None) or []
    normalized_words = []
    for word in words:
        text = str(_get_attr(word, "word", None) or _get_attr(word, "text", "") or "").strip()
        start = _get_attr(word, "start")
        end = _get_attr(word, "end")
        if start is None or end is None or not text:
            continue
        normalized_words.append({
            "text": text,
            "start": float(start) + time_offset,
            "end": float(end) + time_offset,
        })

    # Attach words to the segment whose span contains their midpoint.
    for word in normalized_words:
        midpoint = (word["start"] + word["end"]) / 2.0
        owner = None
        for segment in segments:
            if segment["start"] - 0.001 <= midpoint <= segment["end"] + 0.001:
                owner = segment
                break
        if owner is None and segments:
            owner = min(
                segments,
                key=lambda seg: min(
                    abs(seg["start"] - midpoint), abs(seg["end"] - midpoint)
                ),
            )
        if owner is not None:
            owner["words"].append(word)

    if not segments and normalized_words:
        segments.append({
            "start": normalized_words[0]["start"],
            "end": normalized_words[-1]["end"],
            "text": " ".join(word["text"] for word in normalized_words),
            "speaker": None,
            "words": normalized_words,
        })
    return segments


def _normalize_diarized_response(response, time_offset):
    segments = []
    for segment in _get_attr(response, "segments", None) or []:
        start = _get_attr(segment, "start")
        end = _get_attr(segment, "end")
        text = str(_get_attr(segment, "text", "") or "").strip()
        if start is None or end is None or not text:
            continue
        speaker = _get_attr(segment, "speaker")
        segments.append({
            "start": float(start) + time_offset,
            "end": float(end) + time_offset,
            "text": text,
            "speaker": str(speaker) if speaker is not None else None,
            "words": [],
        })
    return segments


def flag_suspect_hallucinations(segments):
    """Mark heavily repeated texts as suspect without dropping timing anchors."""
    texts = [segment["text"] for segment in segments]
    counts = Counter(texts)
    total = max(1, len(segments))
    suspect_texts = {
        text for text, count in counts.items()
        if count >= HALLUCINATION_MIN_REPEATS and count / total >= HALLUCINATION_REPEAT_RATIO
    }
    for segment in segments:
        segment["suspect_hallucination"] = segment["text"] in suspect_texts
    return segments


def _timing_identity(*, audio_hash, model, language, chunk_plan):
    payload = {
        "pipeline_version": PIPELINE_VERSION,
        "audio_hash": audio_hash,
        "model": model,
        "language": language,
        "chunk_plan": chunk_plan,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def transcribe_timing(client, audio_path, *,
                      model=TIMING_MODEL_WHISPER,
                      language=None,
                      checkpoint_path=None,
                      max_bytes=DEFAULT_TIMING_MAX_BYTES,
                      natural_boundaries=None,
                      max_retries=DEFAULT_MAX_RETRIES,
                      get_duration=None,
                      extract_chunk=None,
                      work_dir=None,
                      progress_callback=print) -> dict:
    """Produce the trusted timing transcript artifact.

    Words (whisper) and speakers (diarize) are preserved; chunked timestamps
    are reconciled back to absolute media time; no timing is ever invented.
    """
    if model not in (TIMING_MODEL_WHISPER, TIMING_MODEL_DIARIZE):
        raise ValueError(f"Timing transcription requires a concrete model, got: {model}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    get_duration = get_duration or audio_splitter.get_audio_duration
    extract_chunk = extract_chunk or semantic_transcriber.default_extract_chunk

    file_size = os.path.getsize(audio_path)
    audio_hash = semantic_transcriber.hash_file(audio_path)

    if file_size <= max_bytes:
        chunk_plan = [(0.0, None)]  # whole file, no cut
    else:
        duration = get_duration(audio_path)
        if not duration:
            raise TimingTranscriptionError(
                "Cannot chunk timing transcription: audio duration unavailable."
            )
        raw_plan = semantic_transcriber.plan_chunks(
            duration,
            file_size,
            max_bytes=max_bytes,
            overlap_sec=TIMING_CHUNK_OVERLAP_SEC,
            natural_boundaries=natural_boundaries,
        )
        chunk_plan = [(start, end) for start, end in raw_plan]

    identity = _timing_identity(
        audio_hash=audio_hash, model=model, language=language, chunk_plan=chunk_plan
    )

    existing = load_json_if_exists(checkpoint_path)
    if existing and existing.get("identity") == identity and existing.get("complete"):
        progress_callback("Reusing completed timing transcript checkpoint.")
        return existing

    artifact = None
    if existing and existing.get("identity") == identity:
        artifact = existing
        done = sum(1 for chunk in artifact["chunks"] if chunk.get("status") == "complete")
        progress_callback(
            f"Timing transcript checkpoint found: {done}/{len(artifact['chunks'])} "
            "chunk(s) complete."
        )
    elif existing:
        progress_callback("Timing settings/audio changed; rebuilding its checkpoint.")

    if artifact is None:
        artifact = {
            "schema_version": SCHEMA_VERSION,
            "pipeline_version": PIPELINE_VERSION,
            "identity": identity,
            "audio_hash": audio_hash,
            "model": model,
            "language": language,
            "chunks": [
                {
                    "index": index,
                    "audio_start": start,
                    "audio_end": end,
                    "segments": None,
                    "status": "pending",
                }
                for index, (start, end) in enumerate(chunk_plan)
            ],
            "segments": [],
            "complete": False,
        }

    def save():
        if checkpoint_path:
            atomic_write_json(checkpoint_path, artifact, prefix=".timing-")

    def request_file(path, offset):
        def call():
            with open(path, "rb") as handle:
                if model == TIMING_MODEL_DIARIZE:
                    response = client.audio.transcriptions.create(
                        model=TIMING_MODEL_DIARIZE,
                        file=handle,
                        response_format="diarized_json",
                        chunking_strategy="auto",
                    )
                    return _normalize_diarized_response(response, offset)
                request = {
                    "model": TIMING_MODEL_WHISPER,
                    "file": handle,
                    "response_format": "verbose_json",
                    "timestamp_granularities": ["word", "segment"],
                }
                if language:
                    request["language"] = language
                response = client.audio.transcriptions.create(**request)
                return _normalize_whisper_response(response, offset)

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return call()
            except Exception as exc:
                last_error = exc
                if attempt >= max_retries:
                    break
                delay = min(8.0, 2.0 ** attempt)
                progress_callback(
                    f"Timing transcription attempt {attempt + 1} failed: {exc}. "
                    f"Retrying in {delay:g}s..."
                )
                time.sleep(delay)
        raise TimingTranscriptionError(
            f"Timing transcription failed after retries: {last_error}"
        ) from last_error

    own_work_dir = None
    if work_dir is None and len(artifact["chunks"]) > 1:
        import tempfile
        own_work_dir = tempfile.mkdtemp(prefix="llm_subs_timing_")
        work_dir = own_work_dir
    try:
        for chunk in artifact["chunks"]:
            if chunk.get("status") == "complete":
                continue
            index = chunk["index"]
            if chunk["audio_end"] is None:
                progress_callback(f"Timing transcription: whole file with {model}...")
                chunk["segments"] = request_file(audio_path, 0.0)
            else:
                progress_callback(
                    f"Timing chunk {index + 1}/{len(artifact['chunks'])}: "
                    f"{chunk['audio_start']:.1f}s - {chunk['audio_end']:.1f}s"
                )
                chunk_path = os.path.join(work_dir, f"timing_chunk_{index:03d}.m4a")
                extract_chunk(audio_path, chunk["audio_start"], chunk["audio_end"], chunk_path)
                try:
                    chunk["segments"] = request_file(chunk_path, float(chunk["audio_start"]))
                finally:
                    try:
                        os.remove(chunk_path)
                    except OSError:
                        pass
            chunk["status"] = "complete"
            save()
    finally:
        if own_work_dir:
            import shutil
            shutil.rmtree(own_work_dir, ignore_errors=True)

    merged = []
    overlap_discarded = 0
    for chunk_index, chunk in enumerate(artifact["chunks"]):
        ownership_start = None
        if chunk_index > 0:
            # The previous chunk owns evidence before its non-overlapped cut.
            # The leading overlap still gives ASR linguistic context, but its
            # duplicate leading segments do not enter the final timeline.
            ownership_start = artifact["chunks"][chunk_index - 1]["audio_end"]
        for segment in chunk["segments"] or []:
            midpoint = (float(segment["start"]) + float(segment["end"])) / 2.0
            if ownership_start is not None and midpoint < float(ownership_start):
                overlap_discarded += 1
                continue
            merged.append(segment)
    merged.sort(key=lambda segment: (segment["start"], segment["end"]))
    flag_suspect_hallucinations(merged)
    for index, segment in enumerate(merged):
        segment["id"] = f"timing_{index + 1:06d}"

    if not merged:
        raise TimingTranscriptionError("Timing transcription produced no segments.")

    artifact["segments"] = merged
    artifact["overlap_segments_discarded"] = overlap_discarded
    artifact["complete"] = True
    save()
    progress_callback(
        f"Timing transcript complete: {len(merged)} segment(s), model={model}."
    )
    return artifact


# ---------------------------------------------------------------------------
# Stage identity helpers
# ---------------------------------------------------------------------------

def _stage_identity(**payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reuse_or_none(path, identity, progress_callback, label):
    existing = load_json_if_exists(path)
    if existing and existing.get("identity") == identity and existing.get("complete", True):
        progress_callback(f"Reusing completed {label} artifact.")
        return existing
    return None


def _safe_tag(value, fallback="target"):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "")).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or fallback


def _mark_lead_extensions(translated_segments, cues_by_id):
    """Mark cues whose first source carries speech with no timing evidence
    before its matched span, so display extension may start them early."""
    for segment in translated_segments:
        first_source = cues_by_id[segment["source_ids"][0]]
        if "unhosted_lead_run" in (first_source.get("flags") or []):
            segment["extend_lead"] = True
    return translated_segments


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_pipeline(client, *,
                 audio_path,
                 original_dir,
                 translated_dir,
                 stem,
                 target_language,
                 settings=None,
                 target_tag=None,
                 progress_callback=print,
                 get_duration=None,
                 extract_chunk=None,
                 natural_boundaries=None,
                 concurrent_branches=True) -> HighQualityArtifacts:
    """Run the full Transcribe + LLM route and write every artifact.

    All stages are hash-identified and resumable; rerunning with unchanged
    inputs reuses eligible upstream stages instead of repeating paid calls.
    """
    if isinstance(settings, HighQualitySettings):
        resolved = settings
    else:
        resolved = HighQualitySettings.from_mapping(settings)

    os.makedirs(original_dir, exist_ok=True)
    os.makedirs(translated_dir, exist_ok=True)
    target_tag = target_tag or _safe_tag(target_language)

    timing_model = resolve_timing_model(resolved.timing_model, resolved.multi_speaker)
    timing_suffix = "diarize" if timing_model == TIMING_MODEL_DIARIZE else "whisper"

    semantic_path = os.path.join(original_dir, f"{stem}.transcribe.semantic.json")
    timing_path = os.path.join(original_dir, f"{stem}.timing.{timing_suffix}.json")
    aligned_path = os.path.join(original_dir, f"{stem}.aligned.json")
    source_context_path = os.path.join(original_dir, f"{stem}.source-context.json")
    target_policy_path = os.path.join(
        translated_dir, f"{stem}.{target_tag}.target-policy.json"
    )
    translation_checkpoint_path = os.path.join(
        translated_dir, f"{stem}.{target_tag}.translation.resume.json"
    )
    translated_json_path = os.path.join(
        translated_dir, f"{stem}.{target_tag}.translated.json"
    )
    translated_srt_path = os.path.join(translated_dir, f"{stem}.{target_tag}.srt")
    bilingual_srt_path = os.path.join(
        translated_dir, f"{stem}.{target_tag}.bilingual.srt"
    )
    quality_json_path = os.path.join(
        translated_dir, f"{stem}.{target_tag}.quality.json"
    )

    progress_callback(
        f"High-quality pipeline: semantic={resolved.semantic_model}, "
        f"timing={timing_model}, translation={resolved.translation_model}, "
        f"target={target_language}"
    )

    chunk_boundaries = natural_boundaries
    large_audio_threshold = min(
        semantic_transcriber.MAX_WHOLE_FILE_BYTES,
        DEFAULT_TIMING_MAX_BYTES,
    )
    if chunk_boundaries is None and os.path.getsize(audio_path) > large_audio_threshold:
        progress_callback("Detecting silence gaps for safer long-audio chunk boundaries...")
        chunk_boundaries = audio_splitter.find_silence_boundaries(audio_path)
        if chunk_boundaries:
            progress_callback(
                f"Found {len(chunk_boundaries)} candidate silence boundary/boundaries."
            )
        else:
            progress_callback(
                "No usable silence boundary found; retaining overlap-based boundary recovery."
            )

    # --- Stage 1+2: semantic and timing branches (independent; run together)
    timing_language = (
        resolved.source_languages[0]
        if len(resolved.source_languages) == 1 else None
    )

    def run_semantic():
        return semantic_transcriber.transcribe_semantic(
            client,
            audio_path,
            model=resolved.semantic_model,
            prompt=resolved.prompt,
            keywords=list(resolved.keywords),
            languages=list(resolved.source_languages),
            checkpoint_path=semantic_path,
            max_retries=resolved.api_max_retries,
            natural_boundaries=chunk_boundaries,
            get_duration=get_duration,
            extract_chunk=extract_chunk,
            progress_callback=progress_callback,
        )

    def run_timing():
        return transcribe_timing(
            client,
            audio_path,
            model=timing_model,
            language=timing_language,
            checkpoint_path=timing_path,
            max_retries=resolved.api_max_retries,
            natural_boundaries=chunk_boundaries,
            get_duration=get_duration,
            extract_chunk=extract_chunk,
            progress_callback=progress_callback,
        )

    semantic_error = None
    if concurrent_branches:
        with ThreadPoolExecutor(max_workers=2) as executor:
            semantic_future = executor.submit(run_semantic)
            timing_future = executor.submit(run_timing)
            # Timing is mandatory: without trusted timestamps there is no
            # high-quality output, so its failure surfaces first.
            timing_artifact = timing_future.result()
            try:
                semantic_artifact = semantic_future.result()
            except Exception as exc:
                semantic_error = exc
                semantic_artifact = None
    else:
        timing_artifact = run_timing()
        try:
            semantic_artifact = run_semantic()
        except Exception as exc:
            semantic_error = exc
            semantic_artifact = None

    degraded_semantic = False
    if semantic_artifact is None:
        if resolved.strict:
            raise HighQualityPipelineError(
                "Semantic transcription failed in strict high-quality mode. "
                "Completed chunks are checkpointed; rerun to resume, or disable "
                f"strict mode to use the degraded timing-text transcript. ({semantic_error})"
            ) from semantic_error
        progress_callback(
            "DEGRADED MODE: semantic transcription failed; using the timing "
            "transcript text as the source transcript. This is recorded in the "
            "quality report."
        )
        degraded_semantic = True
        canonical_text = " ".join(
            segment["text"] for segment in timing_artifact["segments"]
        )
        semantic_identity = f"degraded:{timing_artifact['identity']}"
    else:
        canonical_text = semantic_artifact["canonical_text"]
        semantic_identity = semantic_artifact["identity"]

    # --- Stage 3: deterministic alignment
    alignment_identity = _stage_identity(
        stage="alignment",
        aligner_version=transcript_aligner.ALIGNER_VERSION,
        semantic_identity=semantic_identity,
        timing_identity=timing_artifact["identity"],
        confidence_threshold=resolved.alignment_confidence_threshold,
    )
    aligned_artifact = _reuse_or_none(
        aligned_path, alignment_identity, progress_callback, "alignment"
    )
    if aligned_artifact is None:
        progress_callback("Aligning canonical transcript to trusted timing...")
        aligned_artifact = transcript_aligner.align_transcripts(
            canonical_text,
            timing_artifact["segments"],
            confidence_threshold=resolved.alignment_confidence_threshold,
            semantic_model=resolved.semantic_model,
            timing_model=timing_model,
        )
        aligned_artifact["identity"] = alignment_identity
        aligned_artifact["complete"] = True
        aligned_artifact["degraded_semantic"] = degraded_semantic
        atomic_write_json(aligned_path, aligned_artifact, prefix=".aligned-")
        progress_callback(
            f"Alignment complete: {len(aligned_artifact['cues'])} cue(s), "
            f"{aligned_artifact['stats']['fallback_cues']} timing-text fallback(s), "
            f"{len(aligned_artifact['unresolved_spans'])} unresolved span(s)."
        )

    source_cues = aligned_artifact["cues"]
    if not source_cues:
        raise HighQualityPipelineError("Alignment produced no source cues.")

    # --- Stage 4: whole-program source context (target-independent)
    context_identity = _stage_identity(
        stage="source_context",
        alignment_identity=alignment_identity,
        model=resolved.context_model,
        prompt_version=contextual_translator.PROMPT_VERSION,
        reasoning_effort=resolved.translation_reasoning_effort,
    )
    context_artifact = _reuse_or_none(
        source_context_path, context_identity, progress_callback, "source context"
    )
    if context_artifact is None:
        progress_callback(
            f"Analyzing whole-program source context ({len(source_cues)} cues) "
            f"with {resolved.context_model}..."
        )
        context_pack = contextual_translator.analyze_source_context(
            client,
            source_cues,
            model=resolved.context_model,
            reasoning_effort=resolved.translation_reasoning_effort,
            max_retries=resolved.api_max_retries,
            progress_callback=progress_callback,
        )
        context_artifact = {
            "schema_version": SCHEMA_VERSION,
            "identity": context_identity,
            "model": resolved.context_model,
            "complete": True,
            "context": context_pack,
        }
        atomic_write_json(source_context_path, context_artifact, prefix=".context-")
    source_context = context_artifact["context"]

    # --- Stage 5: target-language policy
    policy_identity = _stage_identity(
        stage="target_policy",
        context_identity=context_identity,
        target_language=target_language,
        model=resolved.context_model,
        prompt_version=contextual_translator.PROMPT_VERSION,
    )
    policy_artifact = _reuse_or_none(
        target_policy_path, policy_identity, progress_callback, "target policy"
    )
    if policy_artifact is None:
        progress_callback(f"Building target policy for {target_language}...")
        policy = contextual_translator.build_target_policy(
            client,
            source_context,
            target_language=target_language,
            model=resolved.context_model,
            reasoning_effort=resolved.translation_reasoning_effort,
            max_retries=resolved.api_max_retries,
            progress_callback=progress_callback,
        )
        policy_artifact = {
            "schema_version": SCHEMA_VERSION,
            "identity": policy_identity,
            "target_language": target_language,
            "model": resolved.context_model,
            "complete": True,
            "policy": policy,
        }
        atomic_write_json(target_policy_path, policy_artifact, prefix=".policy-")
    target_policy = policy_artifact["policy"]

    # --- Stage 6: context-aware window translation
    translation_result = contextual_translator.translate_cues(
        client,
        source_cues,
        target_language=target_language,
        source_context=source_context,
        target_policy=target_policy,
        model=resolved.translation_model,
        escalation_model=resolved.translation_escalation_model,
        enable_escalation=resolved.enable_selective_escalation,
        reasoning_effort=resolved.translation_reasoning_effort,
        checkpoint_path=translation_checkpoint_path,
        max_retries=resolved.api_max_retries,
        progress_callback=progress_callback,
    )

    # --- Stage 7: final assembly
    cues_by_id = {cue["id"]: cue for cue in source_cues}
    translated_segments = []
    bilingual_source_segments = []
    for item in translation_result.translated_segments:
        sources = [cues_by_id[source_id] for source_id in item["source_ids"]]
        source_text = " ".join(
            cue["text"] for cue in sources if cue.get("text")
        ).strip()
        if item.get("dropped") or not str(item.get("text") or "").strip():
            raise HighQualityPipelineError(
                "Translation contained an empty/dropped cue after structural validation."
            )
        translated_segments.append({
            "start": item["start"],
            "end": item["end"],
            "text": item["text"],
            "speaker": item.get("speaker"),
            "source_ids": list(item["source_ids"]),
        })
        bilingual_source_segments.append({
            "start": item["start"],
            "end": item["end"],
            "text": source_text,
        })
    # Give every subtitle its reading time: ends may extend into the
    # following gap, and cues whose speech provably starts before their
    # timed evidence (unhosted lead runs) may appear early into the
    # preceding gap. Evidence times themselves live in the aligned artifact.
    _mark_lead_extensions(translated_segments, cues_by_id)
    duration_reader = get_duration or audio_splitter.get_audio_duration
    try:
        media_duration = duration_reader(audio_path)
    except Exception as exc:
        progress_callback(f"Warning: could not read media duration for display cap: {exc}")
        media_duration = None
    if not media_duration or media_duration <= 0:
        media_duration = max(
            float(segment["end"]) for segment in timing_artifact["segments"]
        )
    translated_segments = subtitle_formatter.extend_display_times(
        translated_segments,
        media_duration=float(media_duration),
    )
    for index, source_segment in enumerate(bilingual_source_segments):
        source_segment["start"] = translated_segments[index]["start"]
        source_segment["end"] = translated_segments[index]["end"]

    translated_metadata = {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "pipeline": "transcribe_llm",
        "semantic_model": resolved.semantic_model,
        "timing_model": timing_model,
        "context_model": resolved.context_model,
        "translation_model": resolved.translation_model,
        "escalation_model": translation_result.escalation_model,
        "target_language": target_language,
        "degraded_semantic": degraded_semantic,
        "source_context": source_context,
        "target_policy": target_policy,
        "cues": [
            {
                "start": segment["start"],
                "end": segment["end"],
                "text": segment["text"],
                "speaker": segment["speaker"],
                "source_ids": segment["source_ids"],
                "source_text": bilingual_source_segments[index]["text"],
            }
            for index, segment in enumerate(translated_segments)
        ],
        "dropped_cues": [],
        "issues": translation_result.issues,
        "escalations": translation_result.escalations,
    }
    atomic_write_json(translated_json_path, translated_metadata, prefix=".translated-")
    progress_callback(f"Translated metadata saved: {translated_json_path}")

    subtitle_formatter.generate_srt(translated_segments, translated_srt_path)
    progress_callback(f"Translated SRT saved: {translated_srt_path}")
    subtitle_formatter.generate_bilingual_srt(
        bilingual_source_segments,
        translated_segments,
        bilingual_srt_path,
        progress_callback=progress_callback,
    )
    progress_callback(f"Bilingual SRT saved: {bilingual_srt_path}")

    covered_ids = [
        source_id
        for item in translation_result.translated_segments
        for source_id in item["source_ids"]
    ]
    quality_report = {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "degraded_semantic": degraded_semantic,
        "alignment": {
            "stats": aligned_artifact["stats"],
            "unresolved_spans": aligned_artifact["unresolved_spans"],
            "dropped_fallbacks": aligned_artifact.get("dropped_fallbacks", []),
            "fallback_warnings": aligned_artifact.get("fallback_warnings", []),
            "confidence_threshold": resolved.alignment_confidence_threshold,
        },
        "translation": translation_result.quality_report,
        "dropped_cues": [],
        "subtitle_metrics": subtitle_quality_metrics(translated_segments),
        "structural_gates": {
            "full_source_coverage": covered_ids == [cue["id"] for cue in source_cues],
            "model_owned_timestamps": 0,
            "invalid_durations": sum(
                1 for segment in translated_segments
                if segment["end"] <= segment["start"]
            ),
        },
        "escalations": translation_result.escalations,
        "issues": translation_result.issues,
    }
    atomic_write_json(quality_json_path, quality_report, prefix=".quality-")
    progress_callback(f"Quality report saved: {quality_json_path}")

    return HighQualityArtifacts(
        translated_srt=translated_srt_path,
        bilingual_srt=bilingual_srt_path,
        translated_json=translated_json_path,
        quality_json=quality_json_path,
        semantic_json=semantic_path,
        timing_json=timing_path,
        aligned_json=aligned_path,
        source_context_json=source_context_path,
        target_policy_json=target_policy_path,
    )
