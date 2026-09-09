# Hard-Subtitle OCR Code Plan

## 1. Status

This document is an implementation plan only. No hard-subtitle extraction code,
new dependencies, CLI commands, or Skill behavior described below is implemented
yet.

The implementation must proceed in small stages. Every stage has an observable
artifact, automated tests, and an exit gate. A later stage must not begin while
the current stage's gate is failing.

## 2. Goal

Extend the Codex-native subtitle workflow so that a YouTube URL or local video
with burned-in subtitles can be processed without manual screenshots and without
calling a paid transcription, OCR, translation, or OpenAI API.

The completed source-selection workflow will be:

```text
YouTube manual caption
-> YouTube automatic caption
-> burned-in subtitle detection and local OCR
-> optional local ASR evidence
-> Codex review/fusion in the current subscribed task
-> existing source.json translation, validation, and export pipeline
```

The hard-subtitle path must automatically:

1. Obtain a local video file from a YouTube URL or accept a local video file.
2. Detect or accept the subtitle region.
3. Extract only useful timestamped frames from that region.
4. Recognize subtitle text with an on-device OCR backend.
5. Merge repeated frame observations into stable subtitle cues.
6. Optionally align those cues with on-device ASR evidence.
7. Produce review windows for Codex without invoking a model endpoint from code.
8. Write a validated `source.json` compatible with the existing workflow.
9. Preserve evidence and checkpoints so every stage is inspectable and resumable.

## 3. Non-goals

The first release will not:

- Remove burned-in subtitles from the video image.
- Render translated subtitles back into a re-encoded video.
- Treat arbitrary titles, signs, logos, or player controls as dialogue subtitles.
- Depend on a hosted OCR, ASR, or language-model API.
- Guarantee accurate recovery of animated karaoke subtitles, vertically scrolling
  text, or multiple unrelated subtitle tracks in the first milestone.
- Replace existing YouTube-caption or local-ASR behavior when the user explicitly
  requests one of those source modes.

## 4. Architectural rules

### 4.1 Local services, not network microservices

“Service” means a focused Python module and CLI operation. The services exchange
versioned files in the job directory. They do not need HTTP servers, containers,
queues, or background daemons.

### 4.2 Deterministic mechanics stay in code

Downloading, probing, cropping, frame-change detection, OCR inference, temporal
clustering, string similarity, ASR alignment, validation, caching, and report
generation must be implemented as deterministic local code.

### 4.3 Language judgment stays in Codex

When OCR and ASR disagree, code must prepare evidence and candidates. It must not
call an LLM API to decide which wording is correct. The current Codex task reviews
the prepared windows using subscription-backed Codex capability.

### 4.4 OCR and ASR remain separate evidence

The raw OCR result and raw ASR result must never overwrite one another.

- When speech and burned-in subtitles are in the same language, ASR may suggest
  repairs for low-confidence OCR tokens.
- When they are in different languages, the burned-in subtitle remains the source
  text. ASR may assist with timing, names, and semantic warnings but must not
  replace the subtitle translation.
- Every accepted repair must retain provenance.

### 4.5 Existing downstream contract remains stable

The existing translation planner and exporter consume `source.json`. New stages
must converge on that interface so the existing translation, validation, resume,
and SRT export services do not need a second hard-subtitle-specific path.

### 4.6 No paid API by default

The Codex-native package must not import the OpenAI SDK or a hosted OCR/ASR client.
Model weights may be downloaded once from their distribution host after the user
approves dependency installation. Inference must run locally.

## 5. Planned source modes and routing

Add a source-mode enum with these values:

| Mode | Meaning |
| --- | --- |
| `auto` | Prefer YouTube captions; if absent, detect hard subtitles; if no usable hard subtitles are found, fall back to local ASR. |
| `youtube-caption` | Use only a downloadable YouTube caption track; fail clearly if unavailable. |
| `hard-subtitle` | Download/accept video and force the hard-subtitle workflow. |
| `local-asr` | Ignore captions and hard subtitles and use local audio transcription. |

`auto` must not classify a video as hard-subtitled from one isolated text frame.
It must require a stable subtitle-like region across multiple sampled timestamps.
The detection decision, score, sampled timestamps, and rejected alternatives must
be recorded in the job artifacts.

## 6. Planned modules

### 6.1 `codex_subtitles/hard_subtitle_models.py`

Purpose:

- Define typed internal records for normalized regions, frame observations, OCR
  observations, cue candidates, ASR matches, provenance, and quality issues.
- Validate normalized coordinates and timestamp invariants at construction time.
- Serialize records into versioned JSON-compatible dictionaries.

Important invariants:

- Time is represented in seconds as non-negative floats.
- Regions use normalized coordinates in `[0, 1]` and are independent of video
  resolution.
- An observation points to an existing evidence image and a video timestamp.
- A cue has `end > start` and observations in chronological order.
- Provenance identifies OCR observations, optional ASR segments, and Codex review
  status separately.

### 6.2 Extend `codex_subtitles/video_service.py`

Purpose:

- Add YouTube video download in a bounded OCR-friendly format.
- Prefer MP4-compatible video/audio streams up to 1080p and ask FFmpeg to merge
  them into an MP4 container when those streams are available.
- Accept an FFmpeg-readable WebM or MKV result when YouTube does not expose a
  suitable MP4 combination; OCR success must not depend on the container suffix.
- Remux without re-encoding when possible. Do not spend time transcoding the full
  video merely to force an MP4 suffix.
- Preserve the current caption and audio download behavior.
- Record source URL, yt-dlp metadata, selected format, duration, dimensions, frame
  rate, and local file checksum.
- Accept an existing local video without copying it unnecessarily.

The downloader must be dependency-injectable like the current caption downloader
so unit tests do not contact YouTube. Download failures must distinguish an
outdated/unsupported extractor, authentication or age restriction, a temporary
network failure, unavailable formats, and current YouTube token enforcement.

### 6.3 `codex_subtitles/video_frame_service.py`

Purpose:

- Probe video metadata with FFprobe.
- Generate representative samples for subtitle-region detection.
- Support `auto`, `bottom`, `top`, and explicit normalized-region modes.
- Crop before image export so full frames are not stored by default.
- Compare adjacent cropped frames and retain only likely text changes.
- Add dense sampling around detected transitions to refine cue boundaries.
- Cache its output using a fingerprint of the video checksum and extraction
  settings.

Default starting parameters, subject to evaluation:

```text
coarse sample rate: 3 frames/second
transition refinement: up to 10 frames/second near changes
default bottom candidate region: bottom 25% of the video
maximum default input height: 1080p
```

These are configuration defaults, not hard-coded universal assumptions.

### 6.4 `codex_subtitles/hard_subtitle_detection_service.py`

Purpose:

- Decide whether persistent subtitle-like text exists.
- Rank candidate top/bottom regions.
- Reject isolated titles, logos, watermarks, and scene text when possible.
- Emit an explicit confidence score and reasons.

Detection signals may include:

- Repeated text detections in a consistent horizontal band.
- Multiple distinct phrases appearing sequentially in the same band.
- Centered line geometry and subtitle-like line heights.
- Persistence for several adjacent frames followed by replacement or disappearance.
- Penalties for unchanged watermarks or text covering most of the video duration.

The first implementation should favor precision over recall in `auto` mode. A
user-forced `hard-subtitle` mode may proceed with a warning when detection
confidence is low.

### 6.5 `codex_subtitles/ocr_backend.py`

Purpose:

- Define a small backend protocol independent of any OCR package.
- Report backend availability without importing heavyweight runtimes eagerly.
- Select an installed local backend in `auto` mode.
- Normalize backend-specific boxes, text, and confidence values.

Planned backend contract:

```text
available_backends() -> list[str]
choose_backend(requested="auto") -> str
recognize(image_path, language, options) -> list[OCRLine]
```

The initial backend must be selected after a short Apple Silicon benchmark using
the supplied test videos. PaddleOCR and a macOS-native Vision adapter are
candidates; the rest of the system must not depend on which one wins. No hosted
OCR backend will be included.

### 6.6 `codex_subtitles/hard_subtitle_ocr_service.py`

Purpose:

- Run preprocessing variants on each selected crop.
- Invoke the chosen local OCR backend.
- Normalize punctuation and whitespace without changing semantic text.
- Keep all useful candidates rather than only the first OCR output.
- Store confidence, bounding boxes, preprocessing variant, and evidence frame.
- Resume without re-running successful observations.

Initial preprocessing candidates:

- Original crop.
- Two-times upscale.
- Grayscale with contrast normalization.
- Optional thresholded variant for outlined text.

Preprocessing selection must be evidence-driven. Running every variant on every
frame is not the default if one stable variant is already producing high
confidence.

### 6.7 `codex_subtitles/subtitle_timeline_service.py`

Purpose:

- Cluster repeated or near-identical OCR observations across adjacent frames.
- Choose a stable text candidate using confidence and temporal consensus.
- Recover cue start/end boundaries from first/last stable appearance plus refined
  transition samples.
- Handle one-line/two-line transitions and rolling captions.
- Reject very short flashes and persistent watermarks.
- Produce non-overlapping, monotonic cue candidates.

The service must expose thresholds as named configuration values and record them
in the output artifact. Tests must cover punctuation-only changes, single-token
OCR errors, line-wrap changes, and a subtitle that disappears briefly between
scenes.

### 6.8 Extend `codex_subtitles/local_asr_service.py`

Purpose:

- Preserve raw local-ASR output as a separate evidence artifact.
- Allow ASR to run in parallel with hard-subtitle extraction after video/audio are
  available.
- Record spoken language independently of burned-subtitle language.
- Never write ASR directly to `source.json` during a fusion job.

Existing pure local-ASR jobs must continue to write `source.json` as they do now.

### 6.9 `codex_subtitles/source_fusion_service.py`

Purpose:

- Align OCR cues and ASR segments by time.
- Compute normalized text similarity when languages match.
- Create warnings rather than text substitutions when languages differ.
- Generate bounded review windows containing OCR text, confidence, evidence-frame
  paths, aligned ASR text, language metadata, and proposed alternatives.
- Validate Codex-reviewed windows before materializing `source.json`.

The service has two deterministic modes:

- `ocr-only`: materialize high-confidence OCR cues and route uncertain cues for
  review.
- `ocr-asr`: prepare aligned evidence and route disagreements for review.

It must not choose a linguistically different ASR sentence merely because its
confidence is higher.

### 6.10 `codex_subtitles/hard_subtitle_validation.py`

Purpose:

- Validate artifact schemas and referential integrity.
- Enforce monotonic timestamps and non-empty evidence.
- Detect implausible cue durations, duplicate adjacent cues, excessive overlap,
  and likely watermark contamination.
- Require every low-confidence cue to be reviewed, explicitly accepted, corrected,
  or dropped with a reason before `source.json` is finalized.
- Produce machine-readable errors with cue and frame references.

### 6.11 `codex_subtitles/hard_subtitle_evaluation.py`

Purpose:

- Discover YouTube URL test cases from the file interface in section 9.
- Run selected pipeline stages without paid APIs.
- Compare generated cues with optional reference SRT files.
- Produce per-case and aggregate JSON/Markdown reports.
- Preserve representative false-positive, false-negative, and low-confidence
  evidence images for human review.

### 6.12 Extend `codex_subtitles/storage.py`

Add stable job paths for:

```text
artifacts/video/
artifacts/hard_subtitles/frames/
artifacts/hard_subtitles/frames.index.json
artifacts/hard_subtitles/detection.json
artifacts/hard_subtitles/ocr.observations.json
artifacts/hard_subtitles/ocr.cues.json
artifacts/asr.raw.json
fusion/index.json
fusion/*.source.json
fusion/*.target.json
reports/hard_subtitle_quality.json
```

Large evidence collections may later use JSON Lines, but the first schema must be
chosen before implementation and read through one storage abstraction so the
physical representation can change without affecting services.

### 6.13 Extend `codex_subtitles/workflow_service.py`

Purpose:

- Route the four source modes.
- Add local-video job preparation.
- Track explicit state transitions.
- Resume from the last valid artifact.
- Avoid overwriting accepted Codex review windows.
- Fall back from hard-subtitle detection to local ASR only in `auto` mode.

Planned states:

```text
created
video_ready
needs_hard_subtitle_detection
hard_subtitle_region_ready
hard_subtitle_frames_ready
hard_subtitle_ocr_ready
needs_local_asr
needs_source_fusion
needs_codex_review
source_ready
translation_planned
complete
failed
```

Every state transition must record the responsible stage, configuration
fingerprint, timestamps, and the last successful artifact.

### 6.14 Extend `codex_subtitles/cli.py`

Planned commands:

```text
prepare URL --source-mode ...
prepare-local VIDEO --source-mode ...
detect-hard-subs JOB_DIR
extract-hard-sub-frames JOB_DIR
ocr-hard-subs JOB_DIR
transcribe-local JOB_DIR
align-sources JOB_DIR
validate-hard-subs JOB_DIR
materialize-hard-subs JOB_DIR
evaluate-hard-subs TEST_ROOT
status JOB_DIR
doctor
```

Important planned options:

```text
--hard-subtitle-language en
--spoken-language th
--subtitle-region auto|bottom|top|x,y,w,h
--ocr-backend auto|...
--ocr-fps 3
--source-fusion auto|ocr-only|ocr-asr
--force-recompute STAGE
```

CLI commands must print structured JSON, use non-zero exit codes on failure, and
never print secrets or require API keys.

### 6.15 Extend `doctor`

Report independently:

- FFmpeg and FFprobe availability and versions.
- yt-dlp availability.
- Installed local OCR backends.
- Installed local ASR backends.
- Apple Silicon status where relevant.
- Whether required model weights are already cached.
- Whether paid API access is required (`false` for this workflow).

`doctor` must not download models, contact APIs, or import unavailable heavyweight
packages merely to report status.

## 7. Data contracts

### 7.1 Frame index

Each selected frame record will contain at least:

```json
{
  "frame_id": "f000001",
  "timestamp": 101.52,
  "image": "frames/f000001.png",
  "region": {"x": 0.0, "y": 0.75, "width": 1.0, "height": 0.25},
  "selection_reason": "subtitle_region_changed",
  "change_score": 0.41
}
```

### 7.2 OCR observation

```json
{
  "observation_id": "o000001",
  "frame_id": "f000001",
  "timestamp": 101.52,
  "language": "en",
  "text": "it is therefore free from ignorance,",
  "confidence": 0.96,
  "boxes": [],
  "backend": "local-backend-name",
  "preprocessing": "upscale-2x"
}
```

### 7.3 OCR cue candidate

```json
{
  "candidate_id": "hc000001",
  "start": 101.52,
  "end": 104.86,
  "text": "it is therefore free from ignorance,",
  "confidence": 0.94,
  "observation_ids": ["o000001", "o000002"],
  "issues": [],
  "review_status": "not_required"
}
```

### 7.4 Fusion review window

```json
{
  "window_id": "0001",
  "subtitle_language": "en",
  "spoken_language": "en",
  "same_language": true,
  "cues": [
    {
      "candidate_id": "hc000001",
      "ocr_text": "it is therefore free from ignorance,",
      "ocr_confidence": 0.94,
      "aligned_asr_text": "it is therefore free from ignorance",
      "evidence_images": ["frames/f000001.png"],
      "issues": []
    }
  ]
}
```

Codex writes reviewed text and decisions to a separate target file. Raw evidence
files remain immutable.

### 7.5 Final `source.json`

Keep the current required fields and add optional provenance without breaking
existing consumers:

```json
{
  "schema_version": 1,
  "language": "en",
  "source_kind": "hard_subtitle_ocr_asr_reviewed",
  "segments": [
    {
      "id": "c000001",
      "index": 0,
      "start": 101.52,
      "end": 104.86,
      "text": "it is therefore free from ignorance,",
      "provenance": {
        "ocr_candidate_id": "hc000001",
        "asr_segment_ids": [],
        "reviewed_by_codex": false
      }
    }
  ]
}
```

Before adding optional cue fields, verify that existing normalization does not
discard them. If it does, keep provenance in a parallel source-provenance artifact
instead of changing downstream behavior silently.

## 8. Cache and resume design

Each stage gets a fingerprint derived from:

- Input video checksum.
- Relevant executable/backend version.
- Model identifier.
- Region and sampling configuration.
- Preprocessing and clustering configuration.
- Input artifact checksum from the previous stage.

A stage may reuse output only when its fingerprint and artifact validation both
pass. `--force-recompute STAGE` invalidates the named stage and all dependent
stages, but preserves unrelated raw downloads and user/Codex review output until
the user explicitly authorizes replacement.

Partial files must be written atomically or removed after failure. The status
command must distinguish `missing`, `partial`, `stale`, `valid`, and `review
required` artifacts.

## 9. YouTube test-case file interface reserved for the user

Acceptance inputs are YouTube addresses, not manually downloaded video files. The
local case file and a tracked example schema are reserved alongside this plan.
Case parsing and evaluation behavior remain unimplemented until Step 1.

```text
testdata/hard_subtitles/
|-- youtube_cases.example.json          tracked schema example
|-- youtube_cases.local.json            user-owned URL list; Git-ignored
`-- references/
    |-- clear-bottom-en.srt              optional
    `-- translated-speech.srt            optional

output/hard_subtitle_evaluation/         generated and Git-ignored
`-- <case-id>/
    |-- artifacts/video.<mp4|webm|mkv>   downloaded cache
    |-- artifacts/hard_subtitles/...
    `-- reports/...
```

The evaluator owns downloading and caching. The user never has to convert a URL
to MP4 or capture frames manually.

### 9.1 Minimal URL-only interface

The only required field in a case is `url`:

```json
{
  "schema_version": 1,
  "cases": [
    {
      "url": "https://www.youtube.com/watch?v=VIDEO_ID"
    }
  ]
}
```

When `id` is omitted, the evaluator derives it from the YouTube video ID after
metadata inspection. A minimal URL-only case runs an unscored end-to-end smoke
test with automatic caption/hard-subtitle routing.

An unscored case can verify:

- YouTube metadata can be inspected.
- The required video and audio streams can be downloaded and merged or retained
  in another FFmpeg-readable container.
- A region decision is produced.
- Evidence frames and OCR observations are produced when subtitles are detected.
- Timestamps are monotonic and inside video duration.
- A second run reuses the downloaded and processed artifacts.
- No paid API client is imported or called.

### 9.2 Optional per-case settings

Add fields only when automatic assumptions need help or a case is being promoted
to a scored regression test:

```json
{
  "schema_version": 1,
  "cases": [
    {
      "id": "clear-bottom-en",
      "url": "https://www.youtube.com/watch?v=VIDEO_ID",
      "source_mode": "hard-subtitle",
      "subtitle_language": "en",
      "spoken_language": "en",
      "expected_hard_subtitles": true,
      "region": {"mode": "auto"},
      "fusion": "ocr-asr",
      "reference_srt": "references/clear-bottom-en.srt",
      "tags": ["fixed-bottom", "white-on-dark", "one-line"]
    }
  ]
}
```

URLs must use an allowed YouTube host and HTTP(S). Duplicate video IDs, duplicate
case IDs, unknown fields, conflicting modes, malformed URLs, and reference paths
outside the test root fail schema validation. Case values are test data, never
executable instructions.

### 9.3 Optional reference SRT

When a reference SRT is present, the evaluator computes scored metrics. The
reference should reproduce the visible burned-in wording, not an independent ASR
transcript. It may cover the full video or a declared time range.

### 9.4 Download/container behavior

The test runner should prefer MP4-compatible streams and an MP4 merged output,
because that is convenient to inspect. YouTube may expose separate audio/video
streams or formats whose most natural container is WebM/MKV. Therefore:

- `video.mp4` is preferred, not mandatory.
- A successful FFmpeg-readable WebM/MKV cache is accepted.
- Remuxing is allowed when codecs are compatible.
- Full-video transcoding solely to change the extension is not the default.
- The actual stream IDs, codecs, container, merge/remux action, and downloader
  version are recorded for reproducibility.

### 9.5 Repository hygiene

The user edits this ignored file:

```text
testdata/hard_subtitles/youtube_cases.local.json
```

The tracked `youtube_cases.example.json` documents the schema. URLs may be kept
local when they are private or temporary. Downloaded videos, extracted frames,
and generated reports stay under `output/hard_subtitle_evaluation/`, which is
already ignored.

## 10. Evaluation metrics

### 10.1 With reference SRT

Measure:

- Cue detection precision, recall, and F1 after time-overlap matching.
- Character error rate (CER).
- Word error rate (WER) for languages with meaningful whitespace tokenization.
- Start-time, end-time, and duration absolute errors.
- Median and 95th-percentile boundary error.
- Duplicate-cue rate.
- False-positive text outside the subtitle region.
- Percentage of cues requiring Codex review.
- End-to-end processing time and peak artifact size.

### 10.2 Without reference SRT

Measure structural and operational quality:

- Non-empty detection/OCR output when subtitles are detected.
- Timestamp monotonicity and video-duration bounds.
- Overlap and implausible-duration counts.
- Confidence distribution.
- Percentage of retained versus inspected frames.
- Cache hit rate on a second identical run.
- Artifact schema validity and evidence-file existence.
- Whether any prohibited API module was imported.

### 10.3 Initial acceptance tiers

For clear, fixed, bottom-centered subtitles similar to the supplied screenshot:

- Cue recall at least 95%.
- Cue precision at least 98%.
- CER no more than 5% before Codex review.
- Median start/end boundary error no more than 300 ms.
- No invalid or non-monotonic timestamps.
- A second run reuses valid video, frame, and OCR artifacts.

Complex styles such as animated text, multiple speakers with overlapping on-screen
lines, or moving subtitle regions will initially be reported separately. Their
gates will be set after baseline measurements rather than weakening the clean-case
gate.

## 11. Implementation sequence with verification gates

### Step 0: Freeze the baseline

Changes:

- Record the current Codex-native unit-test result.
- Record the current `doctor` output.
- Confirm existing caption, imported-subtitle, local-ASR, translation planning,
  validation, and export contracts.

Verification:

```bash
.venv/bin/python -m unittest tests.test_codex_subtitles_services -v
.venv/bin/python -m codex_subtitles doctor
git diff --check
```

Exit gate:

- Baseline failures are documented and distinguished from new work.
- No existing user changes are overwritten.

### Step 1: Create the test-input contract and schema tests

Changes:

- Formalize the already reserved `youtube_cases.local.json` URL interface and add
  `references/` when a reference SRT is first supplied.
- Test that the local URL file is ignored and the example schema is trackable.
- Implement URL case parsing and schema validation only.
- Reject duplicate IDs/video IDs, unsupported hosts, malformed URLs, conflicting
  modes, missing references, and paths that escape the test root.

Verification:

- Unit tests use a temporary URL case file and do not contact YouTube.
- A case containing only `url` is discovered as an unscored case.
- A fully configured URL plus reference SRT is discovered as a scored case.
- Malformed cases fail with actionable field-level errors.
- Parsing never inspects or downloads a YouTube video in this step.

Exit gate:

- The user can paste multiple YouTube addresses into one local file without
  modifying Python code or downloading videos manually.

### Step 2: Add core hard-subtitle types and schemas

Changes:

- Implement normalized-region, frame, observation, cue, issue, and provenance
  records.
- Add versioned serializers and validators.
- Add stable job paths for hard-subtitle artifacts.

Verification:

- Round-trip serialization tests for every record.
- Property tests for invalid coordinates, negative timestamps, reversed cues,
  missing evidence, and unsorted observations.
- Existing `source.json` and translation tests remain green.

Exit gate:

- No downstream service accepts structurally invalid hard-subtitle artifacts.

### Step 3: Add video input, probing, and download

Changes:

- Add bounded YouTube video download as the acceptance-test and normal-workflow
  input path.
- Keep local video input as a secondary developer/unit-test adapter, not as a
  requirement for the user.
- Prefer MP4-compatible streams and MP4 merge output while accepting another
  FFmpeg-readable container when necessary.
- Add FFprobe metadata extraction and checksums.
- Extend `doctor` with FFmpeg/FFprobe and video capability reporting.

Verification:

- Unit tests inject a fake yt-dlp implementation and assert format selection,
  MP4 preference, merge behavior, and container fallback.
- Probe tests use a generated short local test video.
- Missing/corrupt video and missing FFprobe produce clear failures.
- YouTube network tests use URLs from `youtube_cases.local.json`, are opt-in, and
  are not part of the default unit suite.
- Existing caption/audio download tests remain unchanged and green.

Exit gate:

- The job contains a validated local video and reproducible media metadata without
  performing OCR.

### Step 4: Add crop and frame extraction

Changes:

- Implement normalized crop conversion.
- Implement coarse sampling, change scoring, and transition refinement.
- Store cropped evidence frames and an index.
- Add cache fingerprints and atomic stage completion.

Verification:

- Generate synthetic videos with static backgrounds and known subtitle changes.
- Assert selected timestamps bracket the known changes.
- Assert crops have expected pixel dimensions.
- Assert unchanged spans do not create redundant evidence frames.
- Kill/restart simulation leaves no valid-looking partial index.
- Second identical run reports a cache hit and does not rewrite frames.

Exit gate:

- Frame extraction is automatic and no manual screenshots are needed.

### Step 5: Add hard-subtitle presence and region detection

Changes:

- Rank top/bottom candidate bands.
- Add persistence, phrase-change, geometry, and watermark penalties.
- Support forced and explicit regions.

Verification:

- Synthetic positive: fixed bottom subtitles with multiple phrases.
- Synthetic negative: no text.
- Synthetic negative: persistent corner watermark.
- Synthetic positive: top subtitles.
- Forced mode proceeds with a warning when confidence is low.
- Auto mode falls back rather than pretending uncertain detection is reliable.

Exit gate:

- Each case has a reproducible region decision with evidence and a confidence
  score.

### Step 6: Add the OCR backend abstraction and one local backend

Changes:

- Implement backend discovery and selection.
- Add one on-device OCR adapter after benchmarking candidate runtimes on Apple
  Silicon.
- Extend `doctor` with backend/model-cache status.
- Add dependency as optional or scoped so existing caption workflows remain light.

Verification:

- Backend-independent contract tests use a fake OCR backend.
- Adapter tests recognize generated English subtitle crops with known text.
- Missing backend produces installation guidance and never falls back to a hosted
  service.
- Static import test proves the Codex-native package does not import the OpenAI SDK
  or hosted OCR clients.
- First-run model download and later offline inference are tested separately.

Exit gate:

- A local crop produces normalized OCR observations and no paid API is required.

### Step 7: Add preprocessing and OCR checkpointing

Changes:

- Add original, upscale, contrast, and optional threshold variants.
- Select useful variants based on confidence and consistency.
- Persist observation-level checkpoints.

Verification:

- Fixed image fixtures cover white-on-black, outlined white text, two lines, and a
  bright changing background.
- Tests confirm punctuation and line order are preserved.
- A failed frame can be retried without re-running completed observations.
- Changing OCR configuration invalidates only OCR and dependent stages, not video
  download or frame extraction.

Exit gate:

- Every retained frame has either valid OCR observations or an explicit failure
  record.

### Step 8: Reconstruct the subtitle timeline

Changes:

- Implement observation clustering, temporal consensus, cue boundaries, duplicate
  removal, and rolling-line handling.
- Emit cue candidates with issue flags.

Verification:

- Table-driven tests cover exact repeats, minor OCR variation, punctuation-only
  variation, one-to-two-line changes, short blank gaps, rolling captions, and a
  permanent watermark.
- Property tests enforce sorted, non-empty, non-negative, non-overlapping cues.
- Synthetic-video expected boundaries are compared within a fixed tolerance.

Exit gate:

- OCR observations become a valid candidate subtitle timeline independent of ASR
  or Codex.

### Step 9: Preserve ASR evidence and align sources

Changes:

- Store `asr.raw.json` separately for hard-subtitle jobs.
- Implement time-overlap alignment and same-language text similarity.
- Generate fusion review windows.
- Add different-language safeguards.

Verification:

- Same-language test aligns matching OCR/ASR cues and highlights one misspelling.
- Different-language test emits semantic/timing evidence without proposing ASR text
  as the visible subtitle.
- Missing ASR remains a valid `ocr-only` job.
- Empty/silent audio does not destroy valid OCR results.
- Raw OCR and ASR artifacts remain byte-for-byte unchanged after alignment.

Exit gate:

- Review windows contain enough evidence for Codex to decide, but code has made no
  language-model call.

### Step 10: Add Codex review materialization and validation

Changes:

- Define review target files and allowed decisions.
- Validate coverage, order, candidate IDs, accepted corrections, and drop reasons.
- Materialize a downstream-compatible `source.json` only after required reviews
  are complete.

Verification:

- High-confidence OCR-only cues can pass without unnecessary manual review.
- Low-confidence/disputed cues block materialization until resolved.
- Invalid IDs, missing decisions, reordered cues, empty corrections, and silent
  drops fail validation.
- Existing translation planning and SRT export tests pass with a materialized
  hard-subtitle `source.json`.

Exit gate:

- Hard-subtitle jobs join the existing translation pipeline through one trusted
  `source.json` contract.

### Step 11: Wire workflow states and CLI commands

Changes:

- Add source routing, local-video preparation, stage commands, resume behavior,
  status reporting, and structured exit codes.
- Preserve all existing command semantics by default.

Verification:

- CLI parser tests cover every new mode and reject incompatible combinations.
- Workflow tests cover caption success, forced hard subtitle, auto hard subtitle,
  hard-subtitle miss with ASR fallback, OCR-only, OCR+ASR, and local video.
- Resume tests begin from each intermediate state.
- Existing CLI and service tests stay green.

Exit gate:

- A complete hard-subtitle job can be driven entirely by documented CLI commands
  and safely resumed.

### Step 12: Add evaluation runner and reports

Changes:

- Implement case discovery, selective stage execution, reference matching,
  metrics, and JSON/Markdown reports.
- Add a `--case`, `--tag`, and `--time-range` filter for fast iterations.

Verification:

- Metric functions use fixed artificial cue sets with exactly known results.
- An unscored bare-video case produces structural metrics.
- A scored case produces text and timing metrics.
- One broken case does not discard reports for other cases.
- Report paths are stable and generated files remain under ignored output.

Exit gate:

- Every supplied user video produces a pass/fail/unsupported result with evidence,
  not an anecdotal success claim.

### Step 13: Update the `youtube-subtitles` Skill

Changes:

- Expand the Skill description to include burned-in subtitle extraction.
- Update source priority and state routing.
- Add a focused `references/hard-subtitles.md` containing mode selection, OCR/ASR
  precedence, review rules, and CLI details.
- Keep `SKILL.md` concise and route to the new reference only when hard subtitles
  are detected or explicitly requested.
- Update workflow and local-ASR references without duplicating instructions.
- Preserve the explicit no-paid-API boundary.

Verification:

- Run the Skill quick validator.
- Check that every linked reference exists.
- Test realistic invocations with URL-only, forced hard-subtitle, local video,
  OCR-only, and OCR+ASR prompts.
- Confirm the Skill stops for missing local dependencies rather than silently using
  a paid API.
- Confirm an ordinary YouTube-caption job does not load hard-subtitle details
  unnecessarily.

Exit gate:

- Another Codex task can operate the complete flow from the Skill instructions
  without undocumented decisions.

### Step 14: Run user-URL acceptance tests

Changes:

- Add the user's YouTube addresses to the ignored local case file.
- Add optional case settings only where the automatic assumptions need help.
- Add reference SRTs gradually for cases that should become regression gates.
- Tune only named configuration defaults supported by aggregate evidence.

Verification:

- Run each case independently first, then the full suite.
- Review the generated low-confidence evidence gallery.
- Compare first-run and cached-run duration.
- Confirm clean fixed-bottom cases meet section 10.3.
- Categorize unsupported cases instead of weakening all thresholds.

Exit gate:

- The supported case classes and known limitations are explicit.
- At least one suitable public URL case is promoted to a reproducible regression
  case, or the local case produces a retained acceptance report when the URL should
  not be committed.
- The full Codex-native unit suite and hard-subtitle acceptance suite pass.

## 12. Planned test files

The implementation is expected to add focused tests such as:

```text
tests/test_hard_subtitle_models.py
tests/test_hard_subtitle_case_discovery.py
tests/test_video_frame_service.py
tests/test_hard_subtitle_detection_service.py
tests/test_ocr_backend.py
tests/test_hard_subtitle_ocr_service.py
tests/test_subtitle_timeline_service.py
tests/test_source_fusion_service.py
tests/test_hard_subtitle_validation.py
tests/test_hard_subtitle_workflow.py
tests/test_hard_subtitle_evaluation.py
```

Large external video samples are never required for the default unit suite.
Synthetic video/image fixtures should be generated during tests or kept very small.
User-supplied videos belong to the opt-in acceptance suite.

## 13. Compatibility and regression requirements

Throughout implementation:

- Existing caption-selection priority must remain unchanged unless `source-mode`
  explicitly changes it.
- Existing imported SRT/VTT/JSON jobs must behave identically.
- Existing local-ASR-only jobs must behave identically.
- Existing translation window IDs, validation rules, and final SRT generation must
  remain deterministic.
- The Codex-native package must continue to pass the test that forbids importing
  the OpenAI SDK.
- Legacy paid-API scripts must not be called by the Skill or new services.
- Existing user modifications in the dirty worktree must be preserved.

## 14. Failure behavior

Each stage must fail with an actionable, structured error containing:

- Stage name.
- Job ID and relevant artifact.
- Whether the operation is safe to retry.
- Whether prior artifacts remain valid.
- Available local backends when a dependency is missing.
- Suggested local-only next action.

Examples of explicit outcomes:

```text
no_hard_subtitles_detected
hard_subtitle_detection_uncertain
subtitle_region_invalid
video_decode_failed
ocr_backend_missing
ocr_model_missing
ocr_returned_no_text
timeline_validation_failed
asr_unavailable_ocr_only_possible
codex_review_required
```

In `auto` mode, only `no_hard_subtitles_detected` may route to local ASR without
user intervention. Decode failures, corrupted artifacts, and validation errors
must not be disguised as “no subtitles.”

## 15. Security, privacy, and cost checks

- Test sidecars are data and cannot contain shell commands or arbitrary output
  paths.
- External tool commands use argument arrays, validated paths, bounded timeouts,
  and no shell interpolation.
- Evidence paths cannot escape the job/test root.
- Logs never expose environment variables or secret files.
- OCR and ASR inputs remain local.
- Static tests reject imports of known hosted model clients from
  `codex_subtitles`.
- Network access is limited to explicit YouTube download and one-time local model
  acquisition; model inference is offline-capable afterward.

## 16. Definition of done

The feature is complete only when all of the following are true:

1. The normal user and acceptance-test path requires only a YouTube URL; local
   video input remains an optional developer adapter.
2. No manual screenshots are required.
3. The source router can select or force the hard-subtitle path.
4. Local extraction creates inspectable frame, OCR, timeline, and quality artifacts.
5. Local ASR is optional evidence and cannot silently replace translated
   burned-in subtitles.
6. Codex resolves language ambiguity from review windows in the current task,
   without a paid API call from code.
7. A valid `source.json` enters the existing translation/export workflow.
8. Interrupted jobs resume without repeating valid expensive stages.
9. User YouTube test addresses can be added through the reserved local case file
   without code edits or manual downloads.
10. Each test case receives measurable results and review evidence.
11. Clean fixed-bottom acceptance cases meet the initial quality gates.
12. Existing caption, ASR, translation, validation, and export tests remain green.
13. The updated Skill validates and correctly routes realistic prompts.

## 17. Recommended implementation order summary

```text
test interface
-> schemas
-> video input/probe
-> automatic crop/frame extraction
-> hard-subtitle detection
-> local OCR backend
-> OCR preprocessing/checkpointing
-> timeline reconstruction
-> optional ASR alignment
-> Codex review materialization
-> workflow/CLI
-> evaluation reports
-> Skill update
-> user-video acceptance and tuning
```

This order creates a verifiable vertical slice before integration and prevents OCR
or prompt tuning from hiding defects in video timing, caching, or data contracts.
