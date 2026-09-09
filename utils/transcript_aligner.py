"""Deterministic monotonic alignment of the canonical semantic transcript
onto the trusted timing transcript.

Timing values only ever come from the timing backbone (Whisper words or
diarized segments). This module decides which timing span supports each
canonical sentence; it never invents, edits, or interpolates a timestamp
outside the words/segments that support a cue, and no LLM is involved.

Strategy (per TRANSCRIBE_LLM_PLAN.md Stage 4):
1. normalize + tokenize both transcripts with language-aware rules
   (Latin words, digits, CJK characters, Thai characters);
2. find high-confidence ordered anchors (tokens unique in both streams),
   kept monotonic with a longest-increasing-subsequence pass so repeated
   phrases can never create a crossing match;
3. run bounded difflib sequence alignment inside each inter-anchor block;
4. rebuild readable source cues at sentence and pause boundaries;
5. score confidence per cue; canonical sentences below the threshold are
   rescued onto the uncovered timing segments of their gap (canonical text,
   trusted timing) when possible, and otherwise recorded as unresolved;
6. fall back to trusted timing text for timed speech nothing else covers;
   suspicious watermark loops or isolated foreign words are flagged for
   review but never removed automatically.
"""

from __future__ import annotations

import re
import math
import unicodedata
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List, Optional, Sequence


SCHEMA_VERSION = 1
ALIGNER_VERSION = "aligner-2026-08-09.2"

DEFAULT_CONFIDENCE_THRESHOLD = 0.5
DEFAULT_PAUSE_SPLIT_SEC = 1.2
MAX_CUE_DURATION_SEC = 15.0
# Keep cues readable: split very long sentences even without punctuation
# (Thai frequently has none) at pause boundaries first, then by token count.
MAX_CUE_TOKENS = 42
# Fallback risk heuristics. They annotate uncovered timing speech for the
# quality report; they are never authoritative enough to delete content.
FALLBACK_REPEAT_WARNING_COUNT = 3
FALLBACK_FOREIGN_MAX_DURATION_SEC = 1.5
FALLBACK_FOREIGN_MAX_TOKENS = 2
# Rescued cues are sized by the measured speech rate (with headroom) and
# placed by their hosts' position in the gap, so canonical text that falls
# into untimed silence stays recorded as unresolved instead of being crammed
# into sparse segments at unreadable density.
DEFAULT_RESCUE_TOKENS_PER_SEC = 8.0
RESCUE_RATE_HEADROOM = 1.3
# A resolved sentence's unmatched leading/trailing run at least this long is
# handed to the rescue pool instead of stretching the edge cue's text far
# beyond its timed span; shorter edges stay attached to the edge cue.
RESCUE_MIN_RUN_TOKENS = 10

_SENTENCE_END_CHARS = ".!?。！？…؟۔।॥\n"
_THAI_RE = re.compile(r"[฀-๿]")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿豈-﫿가-힯]")
_NO_SPACE_SCRIPT_RE = re.compile(
    r"[぀-ヿ㐀-鿿豈-﫿가-힯]"
    r"|[฀-๿]"
    r"|[຀-໿]"
    r"|[က-႟]"
    r"|[ក-៿]"
)


@dataclass
class Token:
    text: str
    norm: str
    position: int  # character offset in the source string


@dataclass
class TimingToken:
    text: str
    norm: str
    segment_index: int
    start: float
    end: float
    speaker: Optional[str] = None


@dataclass
class _Sentence:
    start_token: int  # inclusive canonical-token index
    end_token: int    # exclusive
    text: str


def normalize_text(text) -> str:
    """Unicode + case + punctuation + whitespace normalization."""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    return normalized.casefold()


def tokenize(text) -> List[Token]:
    """Unicode-aware tokens for spaced and unspaced writing systems.

    Words remain whole for scripts that normally use spaces (Latin, Arabic,
    Cyrillic, Devanagari, and others). CJK and Southeast Asian scripts that
    commonly omit spaces are emitted as base-character grapheme clusters so
    the aligner still has useful anchors.
    """
    tokens = []
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    index = 0
    while index < len(normalized):
        char = normalized[index]
        category = unicodedata.category(char)
        if char.isspace() or category[0] in ("P", "S", "Z", "C"):
            index += 1
            continue

        end = index + 1
        if char.isdigit():
            while end < len(normalized):
                current = normalized[end]
                if current.isdigit():
                    end += 1
                    continue
                if (current in ".,:" and end + 1 < len(normalized)
                        and normalized[end + 1].isdigit()):
                    end += 1
                    continue
                break
        elif _NO_SPACE_SCRIPT_RE.search(char):
            while (end < len(normalized)
                   and unicodedata.category(normalized[end]).startswith("M")):
                end += 1
        elif category[0] in ("L", "M"):
            while end < len(normalized):
                current = normalized[end]
                current_category = unicodedata.category(current)
                if current_category[0] in ("L", "M"):
                    end += 1
                    continue
                if (current in ("'", "’") and end + 1 < len(normalized)
                        and unicodedata.category(normalized[end + 1]).startswith("L")):
                    end += 1
                    continue
                break
        else:
            index += 1
            continue

        raw = normalized[index:end]
        tokens.append(Token(text=raw, norm=raw.casefold(), position=index))
        index = end
    return tokens


def _split_sentences(text) -> List[tuple]:
    """Split canonical text into sentence spans (char_start, char_end)."""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    spans = []
    start = 0
    for index, char in enumerate(normalized):
        if char in _SENTENCE_END_CHARS:
            end = index + 1
            # Attach trailing closing quotes/brackets to the sentence.
            while end < len(normalized) and normalized[end] in "\"'”’）】》」』)":
                end += 1
            if normalized[start:end].strip():
                spans.append((start, end))
            start = end
    if normalized[start:].strip():
        spans.append((start, len(normalized)))
    return spans, normalized


def build_timing_tokens(timing_segments) -> List[TimingToken]:
    """Flatten timing segments into timed tokens.

    Word timestamps are used when present; otherwise every token inherits its
    segment's boundaries so a cue can never move outside trusted timing.
    """
    tokens = []
    for segment_index, segment in enumerate(timing_segments or []):
        speaker = segment.get("speaker")
        seg_start = float(segment.get("start") or 0.0)
        seg_end = float(segment.get("end") or seg_start)
        words = segment.get("words") or []
        if words:
            for word in words:
                word_text = str(word.get("text") or "")
                word_start = float(word.get("start", seg_start))
                word_end = float(word.get("end", seg_end))
                for token in tokenize(word_text):
                    tokens.append(TimingToken(
                        text=token.text,
                        norm=token.norm,
                        segment_index=segment_index,
                        start=word_start,
                        end=word_end,
                        speaker=speaker,
                    ))
        else:
            for token in tokenize(segment.get("text") or ""):
                tokens.append(TimingToken(
                    text=token.text,
                    norm=token.norm,
                    segment_index=segment_index,
                    start=seg_start,
                    end=seg_end,
                    speaker=speaker,
                ))
    return tokens


def _longest_increasing_matches(pairs):
    """Keep a maximal strictly-increasing subsequence of (ci, ti) anchor pairs."""
    if not pairs:
        return []
    pairs = sorted(pairs)
    tails = []          # tails[k] = smallest ti ending an increasing run of length k+1
    tail_indices = []
    parents = [-1] * len(pairs)
    for index, (_ci, ti) in enumerate(pairs):
        position = bisect_left(tails, ti)
        if position == len(tails):
            tails.append(ti)
            tail_indices.append(index)
        else:
            tails[position] = ti
            tail_indices[position] = index
        parents[index] = tail_indices[position - 1] if position > 0 else -1
    result = []
    cursor = tail_indices[-1]
    while cursor != -1:
        result.append(pairs[cursor])
        cursor = parents[cursor]
    return list(reversed(result))


def _find_anchor_pairs(canonical_tokens, timing_tokens):
    """Tokens (or digit tokens) unique in both streams, kept monotonic."""
    canonical_counts = {}
    for index, token in enumerate(canonical_tokens):
        canonical_counts.setdefault(token.norm, []).append(index)
    timing_counts = {}
    for index, token in enumerate(timing_tokens):
        timing_counts.setdefault(token.norm, []).append(index)

    pairs = []
    for norm, canonical_positions in canonical_counts.items():
        if len(canonical_positions) != 1:
            continue
        timing_positions = timing_counts.get(norm)
        if not timing_positions or len(timing_positions) != 1:
            continue
        # Single-character CJK/Thai tokens are too weak to anchor on alone.
        if len(norm) < 2 and not norm.isdigit():
            continue
        pairs.append((canonical_positions[0], timing_positions[0]))

    return _longest_increasing_matches(pairs)


def _match_tokens(canonical_tokens, timing_tokens):
    """Monotonic canonical->timing matches via anchors + per-block difflib."""
    anchors = _find_anchor_pairs(canonical_tokens, timing_tokens)
    matches = {}

    block_bounds = []
    previous_c, previous_t = 0, 0
    for anchor_c, anchor_t in anchors + [(len(canonical_tokens), len(timing_tokens))]:
        block_bounds.append(((previous_c, anchor_c), (previous_t, anchor_t)))
        if anchor_c < len(canonical_tokens):
            matches[anchor_c] = anchor_t
        previous_c, previous_t = anchor_c + 1, anchor_t + 1

    for (c_start, c_end), (t_start, t_end) in block_bounds:
        if c_start >= c_end or t_start >= t_end:
            continue
        canonical_slice = [token.norm for token in canonical_tokens[c_start:c_end]]
        timing_slice = [token.norm for token in timing_tokens[t_start:t_end]]
        matcher = SequenceMatcher(None, canonical_slice, timing_slice, autojunk=False)
        for block in matcher.get_matching_blocks():
            for offset in range(block.size):
                matches[c_start + block.a + offset] = t_start + block.b + offset
    return matches


def _join_tokens(tokens: Sequence[Token]) -> str:
    """Rebuild readable text from canonical tokens (no source string span)."""
    parts = []
    previous = None
    for token in tokens:
        if (previous is not None and _token_uses_spaces(previous.text)
                and _token_uses_spaces(token.text)):
            parts.append(" ")
        parts.append(token.text)
        previous = token
    return "".join(parts).strip()


def _token_uses_spaces(token_text):
    if not token_text or _NO_SPACE_SCRIPT_RE.search(token_text):
        return False
    return any(unicodedata.category(char)[0] in ("L", "N") for char in token_text)


@dataclass
class _CueDraft:
    token_start: int
    token_end: int
    text: str
    start: float
    end: float
    timing_ids: list
    speaker: Optional[str]
    confidence: float
    source: str = "canonical"
    flags: list = field(default_factory=list)


def _sentence_text(normalized_text, span, tokens, token_range):
    text = normalized_text[span[0]:span[1]].strip()
    if text:
        return text
    return _join_tokens(tokens[token_range[0]:token_range[1]])


def _fallback_repeat_key(text):
    return " ".join(normalize_text(text).split())


def _is_combining_token(text):
    """Tokens that continue the previous grapheme (marks, Thai SARA AM)."""
    return bool(text) and (
        unicodedata.category(text[0]).startswith("M") or text[0] == "ำ"
    )


def _advance_past_combining(tokens, index, limit=None):
    """Move a text-split boundary forward so no slice starts mid-grapheme.

    The limit caps the walk at an owning boundary (sentence or next group)
    so a pathological run of combining marks can never push one cue's token
    range into its neighbor's.
    """
    if limit is None:
        limit = len(tokens)
    while index < limit and _is_combining_token(tokens[index].text):
        index += 1
    return index


def _token_script(token_text):
    if _THAI_RE.search(token_text):
        return "thai"
    if _CJK_RE.search(token_text):
        return "cjk"
    if token_text[:1].isdigit():
        return "digit"
    for char in token_text:
        if not unicodedata.category(char).startswith("L"):
            continue
        name = unicodedata.name(char, "")
        for marker, script in (
            ("LATIN", "latin"),
            ("CYRILLIC", "cyrillic"),
            ("ARABIC", "arabic"),
            ("DEVANAGARI", "devanagari"),
            ("KHMER", "khmer"),
            ("LAO", "lao"),
            ("MYANMAR", "myanmar"),
            ("HEBREW", "hebrew"),
            ("GREEK", "greek"),
        ):
            if marker in name:
                return script
        return name.split(" ", 1)[0].lower() if name else "other"
    return "other"


def _dominant_script(tokens):
    """Dominant letter script of a token stream; digits are script-neutral."""
    counts = Counter()
    for token in tokens:
        script = _token_script(token.text)
        if script != "digit":
            counts[script] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def align_transcripts(canonical_text, timing_segments, *,
                      confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
                      pause_split_sec=DEFAULT_PAUSE_SPLIT_SEC,
                      max_cue_duration_sec=MAX_CUE_DURATION_SEC,
                      max_cue_tokens=MAX_CUE_TOKENS,
                      semantic_model=None,
                      timing_model=None) -> dict:
    """Align canonical text to trusted timing and build timed source cues.

    Returns the aligned-source artifact described in the plan: cues with
    stable IDs, trusted timing, confidence, and unresolved spans. Low
    confidence canonical spans are rescued onto the uncovered timing segments
    of their gap when possible (canonical text over trusted segment times);
    remaining uncovered timing keeps its own text as fallback cues. Suspect
    fallback patterns are annotated, so no disagreement is silently removed.
    """
    timing_segments = list(timing_segments or [])
    canonical_tokens_all = tokenize(canonical_text)
    timing_tokens = build_timing_tokens(timing_segments)

    sentence_spans, normalized_text = _split_sentences(canonical_text)
    # Map sentences to canonical token ranges by character position.
    sentences: List[_Sentence] = []
    token_cursor = 0
    for span in sentence_spans:
        span_start_token = token_cursor
        while (token_cursor < len(canonical_tokens_all)
               and canonical_tokens_all[token_cursor].position < span[1]):
            token_cursor += 1
        if token_cursor > span_start_token:
            sentences.append(_Sentence(
                start_token=span_start_token,
                end_token=token_cursor,
                text=_sentence_text(
                    normalized_text, span, canonical_tokens_all,
                    (span_start_token, token_cursor),
                ),
            ))

    matches = _match_tokens(canonical_tokens_all, timing_tokens)

    drafts: List[_CueDraft] = []
    unresolved = []
    # Contiguous runs of unmatched canonical tokens awaiting timing: whole
    # unresolved sentences plus long unmatched edges of resolved sentences.
    # Adjacent runs merge so one timing gap hosts them together.
    rescue_pool = []
    matched_timing_indices = set()

    def pool_add(start_token, end_token, *, entry=None, restore=None):
        if start_token >= end_token:
            return None
        if rescue_pool and rescue_pool[-1]["end_token"] == start_token:
            pool = rescue_pool[-1]
            pool["end_token"] = end_token
        else:
            pool = {
                "start_token": start_token,
                "end_token": end_token,
                "entries": [],
                "restores": [],
            }
            rescue_pool.append(pool)
        if entry is not None:
            pool["entries"].append((entry, start_token, end_token))
        if restore is not None:
            pool["restores"].append(restore)
        return pool

    for sentence in sentences:
        token_indices = list(range(sentence.start_token, sentence.end_token))
        matched = [(ci, matches[ci]) for ci in token_indices if ci in matches]
        confidence = len(matched) / max(1, len(token_indices))

        if not matched or confidence < confidence_threshold:
            entry = {
                "text": sentence.text,
                "reason": "no_timing_match" if not matched else "low_confidence",
                "confidence": round(confidence, 3),
                "rescued": False,
            }
            unresolved.append(entry)
            pool_add(sentence.start_token, sentence.end_token, entry=entry)
            continue

        # Long unmatched sentence edges have no timing evidence at all; give
        # them to the rescue pool instead of stretching an edge cue's text
        # far beyond its supporting span. Short edges stay attached.
        first_matched_ci = min(ci for ci, _ti in matched)
        last_matched_ci = max(ci for ci, _ti in matched)
        lead_boundary = sentence.start_token
        if first_matched_ci - sentence.start_token >= RESCUE_MIN_RUN_TOKENS:
            lead_boundary = first_matched_ci
        trail_run_start = _advance_past_combining(
            canonical_tokens_all, last_matched_ci + 1, limit=sentence.end_token
        )
        trail_boundary = sentence.end_token
        if sentence.end_token - trail_run_start >= RESCUE_MIN_RUN_TOKENS:
            trail_boundary = trail_run_start
        stripped = (lead_boundary != sentence.start_token
                    or trail_boundary != sentence.end_token)

        groups = _split_matched_groups(
            matched,
            canonical_tokens_all,
            timing_tokens,
            pause_split_sec=pause_split_sec,
            max_cue_duration_sec=max_cue_duration_sec,
            max_cue_tokens=max_cue_tokens,
        )
        # The lead pool entry is created first so it can merge with the
        # previous sentence's trailing run; its restore ref is filled below.
        lead_pool = None
        if lead_boundary != sentence.start_token:
            lead_pool = pool_add(sentence.start_token, lead_boundary)

        sentence_drafts = []
        for group_index, group in enumerate(groups):
            group_cis = [ci for ci, _ti in group]
            group_tis = [ti for _ci, ti in group]
            # Attach unmatched canonical tokens to the surrounding groups so
            # no canonical text is dropped when a sentence is split — except
            # long runs, which go to the rescue pool for their own timing.
            token_start = (
                lead_boundary
                if group_index == 0
                else _advance_past_combining(
                    canonical_tokens_all, min(group_cis),
                    limit=sentence.end_token,
                )
            )
            mid_run = None
            if group_index == len(groups) - 1:
                token_end = trail_boundary
            else:
                next_start = _advance_past_combining(
                    canonical_tokens_all,
                    min(ci for ci, _ti in groups[group_index + 1]),
                    limit=sentence.end_token,
                )
                run_start = _advance_past_combining(
                    canonical_tokens_all, max(group_cis) + 1,
                    limit=next_start,
                )
                if next_start - run_start >= RESCUE_MIN_RUN_TOKENS:
                    token_end = run_start
                    mid_run = (run_start, next_start)
                else:
                    token_end = next_start
            text = (
                sentence.text
                if len(groups) == 1 and not stripped
                else _join_tokens(canonical_tokens_all[token_start:token_end])
            )
            if not text:
                # A group of only combining marks renders nothing; keep its
                # timing covered but emit no cue.
                matched_timing_indices.update(group_tis)
                if mid_run is not None:
                    pool_add(mid_run[0], mid_run[1])
                continue
            start = min(timing_tokens[ti].start for ti in group_tis)
            end = max(timing_tokens[ti].end for ti in group_tis)
            speakers = [timing_tokens[ti].speaker for ti in group_tis]
            speaker = speakers[0] if len(set(speakers)) == 1 else None
            draft = _CueDraft(
                token_start=token_start,
                token_end=token_end,
                text=text,
                start=start,
                end=end,
                timing_ids=sorted({
                    timing_tokens[ti].segment_index for ti in group_tis
                }),
                speaker=speaker,
                confidence=round(len(group) / max(1, token_end - token_start), 3),
            )
            drafts.append(draft)
            sentence_drafts.append(draft)
            matched_timing_indices.update(group_tis)
            if mid_run is not None:
                pool_add(
                    mid_run[0], mid_run[1],
                    restore=(draft, "trail", mid_run[1], sentence),
                )

        if lead_pool is not None and sentence_drafts:
            lead_pool["restores"].append(
                (sentence_drafts[0], "lead", sentence.start_token, sentence)
            )
        if trail_boundary != sentence.end_token:
            pool_add(
                trail_boundary, sentence.end_token,
                restore=(
                    (sentence_drafts[-1], "trail", sentence.end_token, sentence)
                    if sentence_drafts else None
                ),
            )

    covered_segments = {
        timing_tokens[ti].segment_index for ti in matched_timing_indices
    }

    # Candidate fallback segments: timed speech with no accepted-cue support.
    candidates = {}
    for segment_index, segment in enumerate(timing_segments):
        if segment_index in covered_segments:
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        candidates[segment_index] = segment

    # Flag suspicious uncovered ASR text without deleting it. Repetition and
    # script mismatch are useful review signals, but neither proves that the
    # speech is absent from the media.
    dominant_script = _dominant_script(canonical_tokens_all)
    repeat_counts = Counter(
        _fallback_repeat_key(str(segment.get("text") or ""))
        for segment in candidates.values()
    )
    fallback_warning_reasons = {}
    fallback_warnings = []
    for segment_index in sorted(candidates):
        segment = candidates[segment_index]
        text = str(segment.get("text") or "").strip()
        reason = None
        if segment.get("suspect_hallucination"):
            reason = "suspect_hallucination"
        elif repeat_counts[_fallback_repeat_key(text)] >= FALLBACK_REPEAT_WARNING_COUNT:
            reason = "repeated_fallback_loop"
        else:
            text_tokens = tokenize(text)
            scripts = {_token_script(token.text) for token in text_tokens}
            scripts.discard("digit")
            duration = (float(segment.get("end") or 0.0)
                        - float(segment.get("start") or 0.0))
            if (dominant_script and scripts and dominant_script not in scripts
                    and len(text_tokens) <= FALLBACK_FOREIGN_MAX_TOKENS
                    and duration < FALLBACK_FOREIGN_MAX_DURATION_SEC):
                reason = "isolated_foreign_word"
        if reason:
            fallback_warning_reasons.setdefault(segment_index, []).append(reason)
            fallback_warnings.append({
                "timing_id": f"timing_{segment_index + 1:06d}",
                "text": text,
                "start": float(segment.get("start") or 0.0),
                "end": float(segment.get("end") or 0.0),
                "reason": reason,
            })

    # Rescue unresolved canonical sentences onto the uncovered segments of
    # their timing gap: the canonical text survives with trusted segment
    # timing instead of being discarded in favor of garbled fallback text.
    segment_token_span = {}
    for ti, token in enumerate(timing_tokens):
        span = segment_token_span.setdefault(token.segment_index, [ti, ti])
        span[1] = ti

    match_items = sorted(matches.items())
    match_cis = [ci for ci, _ti in match_items]
    match_tis = [ti for _ci, ti in match_items]
    prefix_max = []
    running = -1
    for ti in match_tis:
        running = max(running, ti)
        prefix_max.append(running)
    suffix_min = [0] * len(match_tis)
    running = len(timing_tokens)
    for position in range(len(match_tis) - 1, -1, -1):
        running = min(running, match_tis[position])
        suffix_min[position] = running

    matched_token_total = sum(
        draft.token_end - draft.token_start
        for draft in drafts if draft.source == "canonical"
    )
    matched_duration_total = sum(
        max(0.0, draft.end - draft.start)
        for draft in drafts if draft.source == "canonical"
    )
    if matched_token_total and matched_duration_total > 1.0:
        speech_rate = matched_token_total / matched_duration_total
    else:
        speech_rate = DEFAULT_RESCUE_TOKENS_PER_SEC

    def finalize_pool(pool, placed):
        """Settle the pool's bookkeeping from what was actually placed.

        Every entry (whole unresolved sentence) is marked rescued only if
        some of ITS tokens reached a host. Every stripped run whose tokens
        were not placed at all is re-attached to its edge cue, flagged so the
        display layer knows that side of the cue carries un-timed speech.
        """
        base = pool["start_token"]

        def placed_overlaps(lo, hi):
            lo -= base
            hi -= base
            return any(b < hi and e > lo for b, e in placed)

        for entry, entry_start, entry_end in pool["entries"]:
            entry["rescued"] = placed_overlaps(entry_start, entry_end)
        for draft, side, boundary, sentence in pool["restores"]:
            if side == "lead":
                run_lo, run_hi = boundary, draft.token_start
            else:
                run_lo, run_hi = draft.token_end, boundary
            if placed_overlaps(run_lo, run_hi):
                continue
            if side == "lead":
                draft.token_start = boundary
                flag = "unhosted_lead_run"
            else:
                draft.token_end = boundary
                flag = "unhosted_trail_run"
            if flag not in draft.flags:
                draft.flags.append(flag)
            covers_sentence = (
                draft.token_start == sentence.start_token
                and draft.token_end == sentence.end_token
            )
            draft.text = (
                sentence.text if covers_sentence
                else _join_tokens(
                    canonical_tokens_all[draft.token_start:draft.token_end]
                )
            )

    for group in rescue_pool:
        left = bisect_left(match_cis, group["start_token"])
        right = bisect_left(match_cis, group["end_token"])
        prev_ti = prefix_max[left - 1] if left > 0 else -1
        next_ti = suffix_min[right] if right < len(match_tis) else len(timing_tokens)

        hosts = []
        for segment_index in sorted(candidates):
            span = segment_token_span.get(segment_index)
            if span and span[0] > prev_ti and span[1] < next_ti:
                hosts.append(segment_index)
        if not hosts:
            finalize_pool(group, [])
            continue

        group_tokens = canonical_tokens_all[group["start_token"]:group["end_token"]]
        token_count = len(group_tokens)
        host_start = float(candidates[hosts[0]].get("start") or 0.0)
        host_end = float(candidates[hosts[-1]].get("end") or 0.0)
        gap_start = timing_tokens[prev_ti].end if prev_ti >= 0 else host_start
        gap_end = (timing_tokens[next_ti].start
                   if next_ti < len(timing_tokens) else host_end)
        gap_start = min(gap_start, host_start)
        gap_end = max(gap_end, host_end)
        gap_span = max(0.1, gap_end - gap_start)

        placed = []
        cursor = 0
        for segment_index in hosts:
            segment = candidates[segment_index]
            seg_start = float(segment.get("start") or 0.0)
            seg_end = float(segment.get("end") or 0.0)
            duration = max(0.05, seg_end - seg_start)
            count = min(
                token_count,
                MAX_CUE_TOKENS,
                max(1, round(duration * speech_rate * RESCUE_RATE_HEADROOM)),
            )
            # Place the slice where this host sits in the gap's timeline so
            # text spoken during untimed silence is skipped, not shifted.
            center = ((seg_start + seg_end) / 2 - gap_start) / gap_span * token_count
            begin = int(round(center - count / 2))
            begin = max(cursor, min(begin, token_count - count))
            # Never split inside a grapheme cluster: skip leading combining
            # marks and pull trailing ones into this slice.
            while (begin < token_count
                   and _is_combining_token(group_tokens[begin].text)):
                begin += 1
            end_index = min(begin + count, token_count)
            while (end_index < token_count
                   and _is_combining_token(group_tokens[end_index].text)):
                end_index += 1
            slice_tokens = group_tokens[begin:end_index]
            cursor = end_index
            if not slice_tokens:
                continue  # exhausted text: host stays a fallback candidate
            candidates.pop(segment_index)
            placed.append((begin, end_index))
            drafts.append(_CueDraft(
                token_start=-1,
                token_end=-1,
                text=_join_tokens(slice_tokens),
                start=seg_start,
                end=seg_end,
                timing_ids=[segment_index],
                speaker=segment.get("speaker"),
                confidence=0.0,
                source="unresolved_rescue",
                flags=(
                    ["unresolved_span_rescue"]
                    + fallback_warning_reasons.get(segment_index, [])
                ),
            ))
        finalize_pool(group, placed)

    # Trusted-timing fallback: any remaining uncovered segment keeps its own
    # text so timed speech is never dropped.
    for segment_index in sorted(candidates):
        segment = candidates[segment_index]
        drafts.append(_CueDraft(
            token_start=-1,
            token_end=-1,
            text=str(segment.get("text") or "").strip(),
            start=float(segment.get("start") or 0.0),
            end=float(segment.get("end") or 0.0),
            timing_ids=[segment_index],
            speaker=segment.get("speaker"),
            confidence=0.0,
            source="timing_fallback",
            flags=(
                ["canonical_timing_disagreement"]
                + fallback_warning_reasons.get(segment_index, [])
            ),
        ))

    drafts.sort(key=lambda draft: (draft.start, draft.end))

    # Overlapping timing backbones are valid evidence and SRT permits overlap.
    # Never move one cue to manufacture ordering; reject invalid evidence
    # instead of fabricating a timestamp outside its supporting span.
    for draft in drafts:
        if (not math.isfinite(draft.start) or not math.isfinite(draft.end)
                or draft.start < 0.0 or draft.end <= draft.start):
            raise ValueError(
                "Timing evidence must have finite, non-negative, positive spans; "
                f"got {draft.start!r}-{draft.end!r} for {draft.text!r}."
            )

    cues = []
    for index, draft in enumerate(drafts):
        cues.append({
            "id": f"cue_{index + 1:06d}",
            "index": index,
            "start": round(draft.start, 3),
            "end": round(draft.end, 3),
            "text": draft.text,
            "speaker": draft.speaker,
            "alignment_confidence": draft.confidence,
            "source": draft.source,
            "timing_ids": [
                f"timing_{timing_id + 1:06d}" for timing_id in draft.timing_ids
            ],
            "flags": list(draft.flags),
        })

    total_matched = len(matched_timing_indices)
    return {
        "schema_version": SCHEMA_VERSION,
        "aligner_version": ALIGNER_VERSION,
        "semantic_model": semantic_model,
        "timing_model": timing_model,
        "confidence_threshold": confidence_threshold,
        "cues": cues,
        "unresolved_spans": unresolved,
        "dropped_fallbacks": [],
        "fallback_warnings": fallback_warnings,
        "stats": {
            "canonical_tokens": len(canonical_tokens_all),
            "timing_tokens": len(timing_tokens),
            "matched_timing_tokens": total_matched,
            "fallback_cues": sum(1 for cue in cues if cue["source"] == "timing_fallback"),
            "rescued_cues": sum(
                1 for cue in cues if cue["source"] == "unresolved_rescue"
            ),
            "dropped_fallback_cues": 0,
            "fallback_warning_cues": len(fallback_warnings),
            "mean_confidence": round(
                sum(cue["alignment_confidence"] for cue in cues) / max(1, len(cues)), 3
            ),
        },
    }


def _split_matched_groups(matched, canonical_tokens, timing_tokens, *,
                          pause_split_sec, max_cue_duration_sec, max_cue_tokens):
    """Split one sentence's matches at speaker changes, long pauses, and size
    limits, keeping every split deterministic."""
    groups = []
    current = []
    for pair in matched:
        if not current:
            current.append(pair)
            continue
        previous_ti = current[-1][1]
        ti = pair[1]
        previous_token = timing_tokens[previous_ti]
        token = timing_tokens[ti]
        speaker_change = token.speaker != previous_token.speaker
        pause = token.start - previous_token.end >= pause_split_sec
        duration = token.end - min(timing_tokens[p[1]].start for p in current)
        too_long = duration > max_cue_duration_sec
        too_many = len(current) >= max_cue_tokens
        if speaker_change or pause or too_long or too_many:
            groups.append(current)
            current = [pair]
        else:
            current.append(pair)
    if current:
        groups.append(current)
    return groups
