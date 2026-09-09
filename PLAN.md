# Audio Noise-Handling Plan

## Goal

Add a testable audio noise-handling layer for difficult subtitle translation jobs:
Thai Dharma talks with background noise, children shouting, construction noise,
moving people, wind, room echo, or mixed speech.

The design principle is conservative: keep the original audio, make enhancement
optional, cache enhanced audio, and make every mode easy to compare. Speech
enhancement can improve ASR, but aggressive processing can also erase weak Thai
tones, endings, breathy consonants, or short discourse particles.

## Target Pipeline

```text
download raw audio
-> optional audio enhancement
-> VAD / chunk planning
-> ASR transcription
-> transcript cleanup
-> LLM translation
-> SRT output
```

Audio enhancement must stay outside `utils/transcriber.py`. The transcriber
should receive a prepared audio path and remain focused on chunking and ASR.

## Technical Route

### Phase 1: FFmpeg speech-prep layer

Use FFmpeg filters first because they are deterministic, fast, already required
by the project, and easy to unit-test by inspecting generated commands.

Modes:

- `off`: no enhancement, use original audio.
- `mild`: conservative speech preparation.
- `strong_ffmpeg`: more aggressive denoise for noisy samples, not default.

Recommended `mild` filter chain:

```text
highpass=f=80,
lowpass=f=7600,
afftdn=nf=-25,
loudnorm=I=-23:LRA=7:TP=-2
```

Recommended `strong_ffmpeg` filter chain:

```text
highpass=f=100,
lowpass=f=6800,
anlmdn=s=0.00003,
afftdn=nf=-30,
loudnorm=I=-23:LRA=7:TP=-2
```

All enhanced audio should be normalized to mono 16 kHz WAV for ASR stability.

### Phase 2: Evaluation before stronger models

Add a local evaluation script that can compare raw/mild/strong outputs on the
same sample. Do not adopt heavier models until raw-vs-FFmpeg comparisons show
clear limits.

### Phase 3: Optional model-based speech enhancement

Later, add an optional mode such as `deepfilter` behind an optional dependency.
This should not be part of the default install or default test suite.

## Execution Plan

### Step 1: Establish Plan And Baseline

Add this `PLAN.md`, then run the full unit test suite:

```bash
python -m unittest discover -v
```

Validation:

- Test suite is green before feature work continues.

### Step 2: Add `utils/audio_enhancer.py`

Implement a small, independent module:

```python
enhance_audio(input_path, output_path=None, mode="mild", progress_callback=print)
```

Validation:

- `mode="off"` returns the original path without calling FFmpeg.
- Missing input raises `FileNotFoundError`.
- Invalid mode raises `ValueError`.
- `mild` and `strong_ffmpeg` produce the expected FFmpeg command.
- Existing non-empty enhanced file is reused unless overwrite is requested.
- FFmpeg failure removes partial output and raises a clear `RuntimeError`.
- Full unit test suite passes.

### Step 3: Connect Enhancement To The Main Pipeline

Extend `ProcessingRequest` and `process_video()` with:

```python
enhance_audio=False
enhance_mode="mild"
```

Insert enhancement after audio download/reuse and before
`transcriber.transcribe_audio()`.

Validation:

- With enhancement off, transcriber receives the raw audio path.
- With enhancement on, transcriber receives the enhanced path.
- Enhancement failure logs a warning and falls back to raw audio.
- Full unit test suite passes.

### Step 4: Add CLI And GUI Controls

CLI:

```bash
--enhance-audio
--enhance-mode mild
```

GUI:

```text
Audio Enhance: Off / Mild / Strong FFmpeg
```

Validation:

- Argument parsing maps to the new `process_video()` parameters.
- GUI selection maps to the same parameters.
- Logs show the chosen enhancement mode.
- Full unit test suite passes.

### Step 5: Add Evaluation Script

Create:

```text
tools/evaluate_audio_pipeline.py
```

The first version should be offline-friendly and focus on comparing enhancement
artifacts and transcript metrics. It should be able to generate a Markdown
report from supplied transcript segment JSON files without calling paid APIs.

Validation:

- Report generation is unit-tested with fixed fake segments.
- Metrics include segment count, empty segment count, repeated text count, and
  duration coverage.
- Full unit test suite passes.

### Step 6: Update Documentation

Update README with:

- New enhancement options.
- Recommended first run for noisy Thai audio.
- Warning that stronger denoise can damage speech detail.
- Evaluation workflow.

Validation:

- `python -m unittest discover -v` passes.
- `git diff --check` passes.

## Recommended Noisy Thai Workflow

Start conservative:

```bash
python youtube_subtitle_trans.py URL \
  --force-audio \
  --source-lang th \
  --engine gpt-4o-transcribe-diarize \
  --max-segment-sec 90 \
  --enhance-audio \
  --enhance-mode mild
```

Then compare raw vs mild vs strong on short representative samples before
processing a full long video.
