# Realtime Subtitle Global Polishing Plan

## Goal

Turn the resumable `gpt-realtime-translate` JSON artifact into coherent,
context-aware subtitles without changing or deleting the raw translation.

The quality-first pipeline is:

```text
raw realtime JSON
-> stable cue IDs and deterministic window plan
-> whole-video terminology/style analysis
-> context-aware window polishing
-> strict cue coverage and timeline validation
-> resumable polished JSON
-> polished SRT and bilingual SRT
```

## Non-negotiable safety properties

- Raw `.json` and `.resume.json` files remain unchanged.
- The model never owns timestamps. It returns source cue IDs and text; the
  application reconstructs start/end times from the raw cues.
- Every raw cue ID is covered exactly once, in order, by the polished output.
- Only adjacent cues may be merged.
- Each successful window is checkpointed immediately.
- A failed polishing run can resume without repeating completed API calls.
- Existing Realtime output remains available if polishing is disabled or fails.

## Implementation and verification steps

### Step 1: Freeze the baseline and inspect real artifacts

- Record the existing unit-test result.
- Inspect Realtime JSON keys and measure segment counts, empty source text, and
  fragmentation statistics.

Verification:

- Existing test suite passes before feature changes.
- At least one real JSON artifact can be loaded without modification.

### Step 2: Build the offline polishing core

Implement stable cue IDs, source/translation alignment, punctuation-aware
window planning, strict output validation, deterministic timestamp rebuilding,
quality metrics, and atomic checkpoints.

Verification:

- Window plans cover every input cue once with no gaps.
- Invalid ID, duplicate ID, skipped ID, reordered ID, and non-adjacent merge
  responses are rejected.
- Model-provided text can never alter source timing.
- Checkpoints survive a simulated mid-run failure and resume at the next window.
- The real Realtime JSON produces a valid offline window plan and baseline
  quality report.

### Step 3: Add the OpenAI Responses API passes

Pass 1 extracts a compact whole-video context pack: subject, tone, terminology,
names, recurring expressions, onomatopoeia policy, and uncertainty notes.

Pass 2 polishes punctuation-aware windows. Every request includes the global
context pack, before/after reference cues, and the owned cue IDs. Responses use
strict JSON Schema output.

Verification:

- API calls use the Responses API and strict Structured Outputs.
- Malformed or semantically invalid structured responses retry and leave a
  resumable checkpoint.
- A mocked multi-window run returns complete, ordered subtitles.
- A bounded real-sample smoke test returns schema-valid output.

### Step 4: Connect the Realtime pipeline and UI

- Explicitly request `gpt-realtime-whisper` input transcription for future
  Realtime JSON artifacts.
- Save raw Realtime outputs first.
- When global polish is enabled, create separate `.polished.json`,
  `.polished.srt`, `.polished.bilingual.srt`, and `.polish.resume.json` files.
- Add GUI and CLI controls for enabling polishing and choosing its text model.
- Default to quality-first polishing with the current flagship text model.

Verification:

- Realtime-only mode still works with polishing disabled.
- Enabled mode preserves raw artifacts and returns polished artifacts.
- GUI and CLI values reach the same processing request fields.
- A polishing failure reports the raw files and keeps the checkpoint usable.

### Step 5: End-to-end regression and sample evaluation

- Run the complete unit-test suite.
- Run `git diff --check`.
- Compare before/after metrics on the repository's real Realtime JSON.
- Perform one bounded online smoke test rather than spending tokens on the full
  72-minute sample before the structure is proven.

Verification:

- All tests pass.
- No whitespace errors are introduced.
- The bounded online output covers all test cue IDs and has monotonic timing.
- The report records raw vs polished cue count and short-fragment count.

