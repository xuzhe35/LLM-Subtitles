# Recorded-video OCR subtitles

The scope changed on 2026-09-06: accept the user's local screen recording, recover
visible original-language subtitles, align them to the recording timeline and
write an SRT intermediate artifact for cross-checking speech.

## Usage

```bash
.venv/bin/python -m codex_subtitles ocr-video "/path/to/recording.mov" --language en
```

Outputs under `output/recorded_subtitles/<recording>.<settings-id>/`:

- `<recording>.ocr.en.srt`: English visible wording with recording timestamps.
- `<recording>.ocr.en.quality.json`: confidence, issues, raw timing and cue-to-image links.
- `artifacts/frames.index.json` and cropped PNG images.
- `artifacts/ocr.observations.json`: raw local OCR candidates and checkpoints.
- `artifacts/ocr.cues.json`: the reconstructed candidate timeline.
- `ocr-job.json`: progress, source checksum, settings and result paths.

Low-confidence text remains in this intermediate SRT and is flagged separately.
No ASR text replaces the OCR wording. Use `--region bottom` or a normalized
`--region x,y,width,height` for recordings containing player chrome or desktop UI.
`--start/--end` retain recording-relative times. `--time-offset SECONDS` optionally
adds a known constant alignment offset without changing the raw evidence.
Re-running the same command resumes successful frame/OCR checkpoints. Native
Vision uses three bounded workers; checkpoints are still written in frame order.
Brief corrupt OCR flashes between matching stable captions are merged, with the
intervening observations retained and flagged for review.

## Scope retained and rolled back

Retained: video probing, local cropping, frame-change/transition sampling, Apple
Vision adapter, adaptive preprocessing, OCR checkpoints, timeline clustering,
SRT export and cue evidence reports.

Rolled back from this task: new YouTube video downloader, URL acceptance runner,
source-mode routing, OCR/ASR fusion, Codex fusion review materialization,
translation source/provenance integration, expanded doctor, and their tests.
The project's pre-existing caption/audio/ASR/translation behavior and user changes
are preserved. The earlier HARD_SUBTITLE_CODE_PLAN.md remains a historical plan;
its broader workflow is no longer the implementation target.

## Verification

Run the focused tests and the original seven Codex-native regression tests:

```bash
.venv/bin/python -m unittest tests.test_recorded_video_ocr \
  tests.test_hard_subtitle_models tests.test_hard_subtitle_detection_service \
  tests.test_hard_subtitle_ocr_service tests.test_subtitle_timeline_service \
  tests.test_video_frame_service tests.test_ocr_backend \
  tests.test_codex_subtitles_services -v
```

Native integration tests are opt-in through `HARD_SUBTITLE_VISION_EXECUTABLE`.
They generate a two-phrase video, run actual local Vision OCR, parse the exported
SRT, check original text and boundaries within 300 ms, and verify second-run cache
hits.

Validation result (2026-09-06): 27 default tests passed; the 2 opt-in native Vision
tests also passed, for 29 distinct tests. This includes ordered parallel OCR,
cache compatibility, transient-corruption merging and preserving genuine caption
changes. Skill validation and reference-link checks previously passed.
The task-only yt-dlp upgrade and PyYAML validator dependency were reverted from
the virtual environment.

## Real video acceptance (2026-09-06)

Processed `testdata/Video Downloaded/Practice Radiating Metta in 10 Directions.mp4`
(Thai speech, burned-in English captions), duration 522.367 seconds. All 6,212
retained frames completed local OCR. The final English SRT contains 161 cues,
from 0.000 to 518.333 seconds; 51 cues are flagged for visual review. Parsing,
chronological ordering, nonoverlap, duration bounds and file checksums passed.
A second run reused both frame and OCR caches.

Output directory: `output/recorded_subtitles/Practice Radiating Metta in 10 Directions.5c8bfd6ae596/`.
`OCR_TEST_REPORT.zh-CN.md` records visual checks and links to evidence. Known
limitations include lost em dashes, Nibbāna diacritic errors, and background Thai
signage contaminating and fragmenting cues around 06:16–06:21. The SRT retains
machine text; it is an intermediate evidence artifact, not a fully proofread
transcript. No complete human reference exists for measuring real-video CER or
timing accuracy.
