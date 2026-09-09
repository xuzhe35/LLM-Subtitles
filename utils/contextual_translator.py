"""Whole-program source analysis and context-aware window translation for the
Transcribe + LLM pipeline.

The first LLM pass reads every aligned source cue and produces a compact
source context pack (target-independent). A second small pass derives a
target-language policy. Translation then runs over non-overlapping owned cue
windows that reuse the deterministic primitives proven by the Realtime
polisher: stable IDs, exact coverage, adjacent-only merges, trusted timestamp
rebuilding, and resumable checkpoints.

The model never returns timestamps; the application rebuilds timing from the
first and last trusted source cue of each output cue. Prompts here are
translation prompts and are intentionally separate from the Realtime
polishing prompts.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import subtitle_windows
from .subtitle_windows import (
    DEFAULT_CONTEXT_CUES,
    DEFAULT_WINDOW_CUES,
    DEFAULT_WINDOW_DURATION_SEC,
    MAX_MERGED_CUE_DURATION_SEC,
    atomic_write_json,
    load_json_if_exists,
)


DEFAULT_CONTEXT_MODEL = "gpt-5.6-terra"
DEFAULT_TRANSLATION_MODEL = "gpt-5.6-terra"
DEFAULT_ESCALATION_MODEL = "gpt-5.6-sol"
DEFAULT_REASONING_EFFORT = "medium"
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_ESCALATIONS = 12
CHECKPOINT_SCHEMA_VERSION = 1
TRANSLATION_PIPELINE_VERSION = "transcribe_llm_translate_v2"
PROMPT_VERSION = "translate-2026-08-09.2"
# Above this cue count the context pass summarizes deterministic sections
# first and then synthesizes one final pack.
LARGE_TRANSCRIPT_CUES = 800

_NUMBER_RE = re.compile(r"\d+(?:[.,:]\d+)*")

ProgressCallback = Callable[[str], None]


SOURCE_CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "narrative_progression": {"type": "string"},
        "languages": {"type": "array", "items": {"type": "string"}},
        "code_switching_notes": {"type": "string"},
        "speakers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["label", "description"],
                "additionalProperties": False,
            },
        },
        "tone_and_register": {"type": "string"},
        "audience": {"type": "string"},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["name", "kind", "note"],
                "additionalProperties": False,
            },
        },
        "numbers_and_identifiers": {"type": "array", "items": {"type": "string"}},
        "transliteration_rules": {"type": "array", "items": {"type": "string"}},
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
        "attention_spans": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cue_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["cue_ids", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "summary",
        "narrative_progression",
        "languages",
        "code_switching_notes",
        "speakers",
        "tone_and_register",
        "audience",
        "entities",
        "numbers_and_identifiers",
        "transliteration_rules",
        "recurring_expressions",
        "uncertainties",
        "attention_spans",
    ],
    "additionalProperties": False,
}


TARGET_POLICY_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string"},
        "script_and_orthography": {"type": "string"},
        "register": {"type": "string"},
        "transliteration": {"type": "string"},
        "punctuation": {"type": "string"},
        "subtitle_style": {"type": "string"},
        "terminology": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["source", "target", "note"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "language",
        "script_and_orthography",
        "register",
        "transliteration",
        "punctuation",
        "subtitle_style",
        "terminology",
        "notes",
    ],
    "additionalProperties": False,
}


TRANSLATION_WINDOW_SCHEMA = {
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
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                    "type": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["source_ids", "type", "detail"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["cues", "issues"],
    "additionalProperties": False,
}


# Issue types that justify escalating a window to the stronger model. ASR or
# alignment doubts are excluded on purpose: Sol sees text, not audio, so those
# must be fixed upstream, never papered over here.
ESCALATION_ISSUE_MARKERS = ("ambig", "conflict", "uncertain", "inconsisten")


class TranslationValidationError(ValueError):
    """A window failed deterministic validation."""


@dataclass(frozen=True)
class TranslationResult:
    translated_segments: list
    source_context: dict
    target_policy: dict
    quality_report: dict
    model: str
    escalation_model: Optional[str]
    target_language: str
    issues: list = field(default_factory=list)
    escalations: list = field(default_factory=list)
    checkpoint_path: Optional[str] = None


def _clean_text(value):
    return str(value or "").strip()


def _call_structured(client, *, model, instructions, payload, schema_name, schema,
                     reasoning_effort, max_retries, progress_callback):
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
                request["reasoning"] = {"effort": reasoning_effort}
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
                f"Contextual translation API attempt {attempt + 1} failed: {exc}. "
                f"Retrying in {delay:g}s..."
            )
            time.sleep(delay)
    raise RuntimeError(
        f"Contextual translation call failed after retries: {last_error}"
    ) from last_error


def _context_cue_payload(cues):
    return [
        {
            "id": cue["id"],
            "start": cue["start"],
            "end": cue["end"],
            "text": cue["text"],
            "speaker": cue.get("speaker"),
            "alignment_confidence": cue.get("alignment_confidence"),
            "flags": list(cue.get("flags") or []),
        }
        for cue in cues
    ]


_CONTEXT_INSTRUCTIONS = (
    "You are the lead analyst preparing a full-program translation. Read the entire "
    "aligned source transcript and produce a compact, operational source context pack "
    "for later translators: whole-program summary and narrative progression, source "
    "language(s) and code-switching, stable speaker labels, tone/register/audience, "
    "canonical names/organizations/places/products/acronyms, numbers/units/dates and "
    "recurring identifiers, preferred transliteration rules, recurring expressions and "
    "onomatopoeia, ambiguities, and spans needing special attention. Do not translate. "
    "Do not invent facts that are not in the transcript. Return only the requested "
    "structured data."
)


def analyze_source_context(client, cues, *, model=DEFAULT_CONTEXT_MODEL,
                           reasoning_effort=DEFAULT_REASONING_EFFORT,
                           max_retries=DEFAULT_MAX_RETRIES,
                           section_size=LARGE_TRANSCRIPT_CUES,
                           progress_callback=print) -> dict:
    """Build the target-independent whole-program source context pack.

    Ordinary transcripts are analyzed in one request. Unusually large ones are
    summarized in deterministic sections first, then synthesized into one
    final pack so every cue is still covered exactly once.
    """
    cues = list(cues)
    if not cues:
        raise ValueError("Cannot analyze an empty source cue list.")

    if len(cues) <= section_size:
        return _call_structured(
            client,
            model=model,
            instructions=_CONTEXT_INSTRUCTIONS,
            payload={"cues": _context_cue_payload(cues)},
            schema_name="source_context_pack",
            schema=SOURCE_CONTEXT_SCHEMA,
            reasoning_effort=reasoning_effort,
            max_retries=max_retries,
            progress_callback=progress_callback,
        )

    sections = [
        cues[start:start + section_size]
        for start in range(0, len(cues), section_size)
    ]
    progress_callback(
        f"Large transcript: analyzing {len(sections)} deterministic sections "
        "before synthesizing one context pack."
    )
    section_packs = []
    for index, section in enumerate(sections):
        progress_callback(f"Analyzing source section {index + 1}/{len(sections)}...")
        section_packs.append(_call_structured(
            client,
            model=model,
            instructions=_CONTEXT_INSTRUCTIONS,
            payload={"cues": _context_cue_payload(section)},
            schema_name="source_context_pack",
            schema=SOURCE_CONTEXT_SCHEMA,
            reasoning_effort=reasoning_effort,
            max_retries=max_retries,
            progress_callback=progress_callback,
        ))
    return _call_structured(
        client,
        model=model,
        instructions=(
            "Synthesize the following per-section source context packs, which cover the "
            "whole program in order, into one final coherent context pack. Preserve every "
            "distinct entity, term, rule, and uncertainty; merge duplicates; keep the "
            "result compact and operational. Return only the requested structured data."
        ),
        payload={"section_packs": section_packs},
        schema_name="source_context_pack",
        schema=SOURCE_CONTEXT_SCHEMA,
        reasoning_effort=reasoning_effort,
        max_retries=max_retries,
        progress_callback=progress_callback,
    )


def build_target_policy(client, source_context, *, target_language,
                        model=DEFAULT_CONTEXT_MODEL,
                        reasoning_effort=DEFAULT_REASONING_EFFORT,
                        max_retries=DEFAULT_MAX_RETRIES,
                        progress_callback=print) -> dict:
    """Derive language-specific subtitle policy from the source context."""
    return _call_structured(
        client,
        model=model,
        instructions=(
            "You define the target-language subtitle policy for a full-program "
            "translation. Using the source context pack, decide script and orthography, "
            "register, transliteration of names and terms, punctuation conventions, "
            "subtitle style, and a terminology mapping from source terms to preferred "
            "target renderings. Keep decisions consistent and concrete. Return only the "
            "requested structured data."
        ),
        payload={
            "target_language": target_language,
            "source_context": source_context,
        },
        schema_name="target_policy",
        schema=TARGET_POLICY_SCHEMA,
        reasoning_effort=reasoning_effort,
        max_retries=max_retries,
        progress_callback=progress_callback,
    )


def plan_translation_windows(cues, max_cues=DEFAULT_WINDOW_CUES,
                             max_duration_sec=DEFAULT_WINDOW_DURATION_SEC,
                             context_cues=DEFAULT_CONTEXT_CUES):
    return subtitle_windows.plan_cue_windows(
        cues,
        max_cues=max_cues,
        max_duration_sec=max_duration_sec,
        context_cues=context_cues,
        text_key="text",
    )


def find_number_issues(materialized, core_cues):
    """Deterministic number/identifier consistency check.

    Multi-digit sequences present in the source text of a cue group must
    survive into its translation (ignoring thousands separators). Misses are
    reported as issues, not hard failures, because legitimate translations
    may spell numbers out; persistent misses trigger escalation instead.
    """
    core_by_id = {cue["id"]: cue for cue in core_cues}
    issues = []
    for item in materialized:
        if item.get("dropped") or not _clean_text(item.get("text")):
            continue
        source_text = " ".join(
            _clean_text(core_by_id[source_id]["text"]) for source_id in item["source_ids"]
        )
        source_numbers = {
            number for number in _NUMBER_RE.findall(source_text) if len(number) >= 2
        }
        if not source_numbers:
            continue
        translated_numbers = set(_NUMBER_RE.findall(item["text"]))
        translated_compact = {number.replace(",", "") for number in translated_numbers}
        missing = [
            number for number in sorted(source_numbers)
            if number not in translated_numbers
            and number.replace(",", "") not in translated_compact
        ]
        if missing:
            issues.append({
                "source_ids": list(item["source_ids"]),
                "type": "number_inconsistency",
                "detail": f"Source numbers missing from translation: {', '.join(missing)}",
            })
    return issues


def validate_translation_window(raw_result, core_cues,
                                max_merged_duration_sec=MAX_MERGED_CUE_DURATION_SEC):
    """Deterministic acceptance rules for one translated window.

    Structural rules raise TranslationValidationError; the returned issues
    list contains semantic warnings (for example number mismatches) that are
    candidates for selective escalation.
    """
    try:
        materialized = subtitle_windows.validate_and_materialize_window(
            raw_result,
            core_cues,
            max_merged_duration_sec=max_merged_duration_sec,
            forbid_cross_speaker_merge=True,
            allow_empty_text=False,
        )
    except ValueError as exc:
        raise TranslationValidationError(str(exc)) from exc
    return materialized, find_number_issues(materialized, core_cues)


def _window_payload(cues, window, source_context, target_policy, target_language):
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
            "source": cue["text"],
            "speaker": cue.get("speaker"),
            "alignment_confidence": cue.get("alignment_confidence"),
            "flags": list(cue.get("flags") or []),
        })
    return {
        "target_language": target_language,
        "source_context": source_context,
        "target_policy": target_policy,
        "core_ids": [cue["id"] for cue in cues[window.core_slice]],
        "cues": items,
    }


_TRANSLATION_INSTRUCTIONS = (
    "You are a professional subtitle translator working with full-program context. "
    "Translate only cues whose role is core into the target language; before/after cues "
    "are reference only. Follow the source context pack and target policy for consistent "
    "terminology, names, and register. Preserve all claims, negation, numbers, names, and "
    "expressed uncertainty. Use the context to repair sentence fragments. Keep meaningful "
    "tone, emotion, catchphrases, and onomatopoeia; compress only semantically empty "
    "repetition. Never summarize or add facts. Every core ID must appear exactly once, IDs "
    "must stay in source order, only adjacent IDs may share one output cue, merged cues "
    f"must span at most {MAX_MERGED_CUE_DURATION_SEC:g} seconds, and cues from different "
    "speakers must never be merged. Prefer cues readable in at most two subtitle lines. "
    "Every output cue must contain non-empty translated text. Do not delete suspected "
    "noise or hallucinations; translate conservatively and report uncertainty in issues. "
    "Report uncertainty in issues instead of guessing. The provided times are reference "
    "only; do not output timestamps because the application owns timing. Return only the "
    "requested structured data."
)


def _identity(cues, *, target_language, model, escalation_model, source_context,
              target_policy, max_cues, max_duration_sec, context_cues,
              reasoning_effort):
    payload = {
        "pipeline_version": TRANSLATION_PIPELINE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "target_language": target_language,
        "model": model,
        "escalation_model": escalation_model,
        "reasoning_effort": reasoning_effort,
        "max_cues": max_cues,
        "max_duration_sec": max_duration_sec,
        "context_cues": context_cues,
        "source_context": source_context,
        "target_policy": target_policy,
        "cues": [
            {
                "id": cue["id"],
                "start": cue["start"],
                "end": cue["end"],
                "text": cue["text"],
                "speaker": cue.get("speaker"),
            }
            for cue in cues
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _new_checkpoint(identity, windows, cues, target_language, model):
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "pipeline_version": TRANSLATION_PIPELINE_VERSION,
        "identity": identity,
        "target_language": target_language,
        "model": model,
        "cue_count": len(cues),
        "windows": [
            {
                "index": window.index,
                "core_ids": [cue["id"] for cue in cues[window.core_slice]],
                "status": "pending",
                "result": None,
                "issues": [],
                "escalation": None,
            }
            for window in windows
        ],
        "complete": False,
    }


def _load_or_create_checkpoint(path, identity, windows, cues, target_language,
                               model, progress_callback):
    existing = load_json_if_exists(path)
    if existing:
        expected_ids = [
            [cue["id"] for cue in cues[window.core_slice]] for window in windows
        ]
        actual_ids = [item.get("core_ids") for item in existing.get("windows", [])]
        if existing.get("identity") == identity and actual_ids == expected_ids:
            completed = sum(
                item.get("status") == "complete" for item in existing["windows"]
            )
            progress_callback(
                f"Translation checkpoint found: {completed}/{len(windows)} window(s) complete."
            )
            return existing
        progress_callback("Translation settings/source changed; rebuilding its checkpoint.")
    checkpoint = _new_checkpoint(identity, windows, cues, target_language, model)
    if path:
        atomic_write_json(path, checkpoint, prefix=".translate-")
    return checkpoint


def _needs_escalation(model_issues, number_issues):
    reasons = []
    for issue in model_issues:
        issue_type = str(issue.get("type") or "").lower()
        if any(marker in issue_type for marker in ESCALATION_ISSUE_MARKERS):
            reasons.append(f"model_reported:{issue.get('type')}")
    if number_issues:
        reasons.append("number_inconsistency")
    return reasons


def _attempt_window(client, cues, window, source_context, target_policy,
                    target_language, model, reasoning_effort, max_retries,
                    progress_callback):
    """One model's attempts at a window; retries only on structural failure."""
    last_error = None
    for attempt in range(max_retries + 1):
        raw = _call_structured(
            client,
            model=model,
            instructions=_TRANSLATION_INSTRUCTIONS,
            payload=_window_payload(cues, window, source_context, target_policy,
                                    target_language),
            schema_name="translated_subtitle_window",
            schema=TRANSLATION_WINDOW_SCHEMA,
            reasoning_effort=reasoning_effort,
            max_retries=0,
            progress_callback=progress_callback,
        )
        try:
            materialized, number_issues = validate_translation_window(
                raw, cues[window.core_slice]
            )
            return materialized, list(raw.get("issues") or []), number_issues
        except TranslationValidationError as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            progress_callback(
                f"Translation window {window.index + 1} attempt {attempt + 1} "
                f"failed validation: {exc}. Retrying the window..."
            )
    raise TranslationValidationError(
        f"Translation window {window.index + 1} failed validation: {last_error}"
    )


def translate_cues(client, source_cues, *, target_language,
                   source_context, target_policy,
                   model=DEFAULT_TRANSLATION_MODEL,
                   escalation_model=DEFAULT_ESCALATION_MODEL,
                   enable_escalation=True,
                   max_escalations=DEFAULT_MAX_ESCALATIONS,
                   reasoning_effort=DEFAULT_REASONING_EFFORT,
                   checkpoint_path=None,
                   max_cues=DEFAULT_WINDOW_CUES,
                   max_duration_sec=DEFAULT_WINDOW_DURATION_SEC,
                   context_cues=DEFAULT_CONTEXT_CUES,
                   max_retries=DEFAULT_MAX_RETRIES,
                   progress_callback=print) -> TranslationResult:
    """Translate every aligned source cue exactly once with global context."""
    cues = list(source_cues)
    if not cues:
        raise ValueError("Cannot translate an empty source cue list.")

    windows = plan_translation_windows(
        cues,
        max_cues=max_cues,
        max_duration_sec=max_duration_sec,
        context_cues=context_cues,
    )
    identity = _identity(
        cues,
        target_language=target_language,
        model=model,
        escalation_model=escalation_model if enable_escalation else None,
        source_context=source_context,
        target_policy=target_policy,
        max_cues=max_cues,
        max_duration_sec=max_duration_sec,
        context_cues=context_cues,
        reasoning_effort=reasoning_effort,
    )
    checkpoint = _load_or_create_checkpoint(
        checkpoint_path, identity, windows, cues, target_language, model,
        progress_callback,
    )

    escalations_used = sum(
        1 for state in checkpoint["windows"] if state.get("escalation")
    )

    for window, state in zip(windows, checkpoint["windows"]):
        if state.get("status") == "complete" and state.get("result"):
            continue
        progress_callback(
            f"Translating window {window.index + 1}/{len(windows)} "
            f"({window.core_end - window.core_start} cues) with {model}..."
        )
        escalation_record = None
        try:
            materialized, model_issues, number_issues = _attempt_window(
                client, cues, window, source_context, target_policy,
                target_language, model, reasoning_effort, max_retries,
                progress_callback,
            )
        except TranslationValidationError as exc:
            if not (enable_escalation and escalation_model
                    and escalations_used < max_escalations):
                raise
            progress_callback(
                f"Window {window.index + 1} failed on {model}; escalating to "
                f"{escalation_model}: {exc}"
            )
            materialized, model_issues, number_issues = _attempt_window(
                client, cues, window, source_context, target_policy,
                target_language, escalation_model, reasoning_effort, 1,
                progress_callback,
            )
            escalations_used += 1
            escalation_record = {
                "reason": f"structural_failure:{exc}",
                "model": escalation_model,
                "primary_result": None,
            }
        else:
            reasons = _needs_escalation(model_issues, number_issues)
            if (reasons and enable_escalation and escalation_model
                    and escalations_used < max_escalations):
                progress_callback(
                    f"Window {window.index + 1}: escalating to {escalation_model} "
                    f"({'; '.join(reasons)})"
                )
                try:
                    escalated, escalated_issues, escalated_numbers = _attempt_window(
                        client, cues, window, source_context, target_policy,
                        target_language, escalation_model, reasoning_effort, 1,
                        progress_callback,
                    )
                except TranslationValidationError as exc:
                    progress_callback(
                        f"Window {window.index + 1}: escalation failed validation; "
                        f"keeping {model} output. ({exc})"
                    )
                else:
                    escalations_used += 1
                    escalation_record = {
                        "reason": "; ".join(reasons),
                        "model": escalation_model,
                        "primary_result": materialized,
                    }
                    materialized = escalated
                    model_issues = escalated_issues
                    number_issues = escalated_numbers
            elif reasons and enable_escalation and escalations_used >= max_escalations:
                progress_callback(
                    f"Window {window.index + 1}: escalation budget exhausted; "
                    f"keeping {model} output with recorded issues."
                )

        state.update({
            "status": "complete",
            "result": materialized,
            "issues": list(model_issues) + list(number_issues),
            "escalation": escalation_record,
        })
        if checkpoint_path:
            atomic_write_json(checkpoint_path, checkpoint, prefix=".translate-")

    translated = []
    all_issues = []
    escalations = []
    for state in checkpoint["windows"]:
        if state.get("status") != "complete" or not state.get("result"):
            raise RuntimeError("Translation checkpoint still contains an incomplete window.")
        translated.extend(state["result"])
        all_issues.extend(state.get("issues") or [])
        if state.get("escalation"):
            escalations.append({
                "window_index": state["index"],
                **{k: v for k, v in state["escalation"].items() if k != "primary_result"},
            })

    source_ids = [source_id for item in translated for source_id in item["source_ids"]]
    expected_ids = [cue["id"] for cue in cues]
    if source_ids != expected_ids:
        raise RuntimeError("Combined translation failed final source coverage validation.")

    checkpoint["complete"] = True
    if checkpoint_path:
        atomic_write_json(checkpoint_path, checkpoint, prefix=".translate-")

    dropped_output_count = sum(
        1 for item in translated
        if item.get("dropped") or not _clean_text(item.get("text"))
    )
    dropped_source_count = sum(
        len(item.get("source_ids") or []) for item in translated
        if item.get("dropped") or not _clean_text(item.get("text"))
    )
    quality_report = {
        "window_count": len(windows),
        "cue_count": len(cues),
        "translated_cue_count": len(cues) - dropped_source_count,
        "translated_output_cue_count": len(translated) - dropped_output_count,
        "dropped_cue_count": dropped_source_count,
        "dropped_output_cue_count": dropped_output_count,
        "issue_count": len(all_issues),
        "escalated_window_count": len(escalations),
        "escalation_budget": max_escalations if enable_escalation else 0,
        "full_coverage": True,
    }
    progress_callback(
        f"Translation complete: {len(cues)} source cues -> {len(translated)} "
        f"subtitles; {len(escalations)} window(s) escalated."
    )
    return TranslationResult(
        translated_segments=translated,
        source_context=source_context,
        target_policy=target_policy,
        quality_report=quality_report,
        model=model,
        escalation_model=escalation_model if enable_escalation else None,
        target_language=target_language,
        issues=all_issues,
        escalations=escalations,
        checkpoint_path=checkpoint_path,
    )
