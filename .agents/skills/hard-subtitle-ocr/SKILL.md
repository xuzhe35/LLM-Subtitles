---
name: hard-subtitle-ocr
description: Extract visible burned-in subtitles from a YouTube source video or an existing local recording into auditable OCR SRT evidence. Use only when the user has explicitly decided that hard subtitles are present and invokes this skill; do not use it for ordinary caption, transcription, translation, or videos without hard subtitles.
---

# Hard Subtitle OCR

Extract only the text visibly burned into video frames. This is a separate,
explicit preprocessing step for `youtube-subtitles`; it does not translate,
transcribe speech, or decide whether a video has hard subtitles for the user.

## Invocation boundary

- Run this workflow only when the user explicitly invokes `$hard-subtitle-ocr`
  or unmistakably asks to extract visible hard subtitles.
- Never start OCR merely because a project contains an MP4 or lacks captions.
- The user decides whether hard subtitles exist. If that decision is not stated,
  explain what this skill does and wait rather than scanning the whole video.
- Never call a paid OCR, transcription, or language-model API. The repository's
  OCR path uses local FFmpeg processing and macOS Apple Vision.

## Inputs

Accept one of:

- a child directory under `Subtitle Projects` containing `URL.md` and optionally
  a local recording;
- a YouTube URL;
- an existing local video file.

Also accept visible subtitle language, crop region, and an optional time range.
The language means the language printed on screen, not necessarily the spoken
language. Read [ocr-workflow.md](references/ocr-workflow.md) for acquisition,
commands, timing alignment, outputs, and validation.

## Required outcome

Return an original-language `*.ocr.<language>.srt`, its `.quality.json` sidecar,
the OCR job directory, and a timing-basis/alignment report. Preserve frame crops
and raw OCR observations so questionable cues can be inspected later.

Prefer OCR directly from the YouTube source video when it can be acquired. Its
timestamps already use the source timeline. A screen recording is a fallback;
its timestamps remain recording-relative unless a verified mapping to the source
timeline is produced.

Never overwrite the input video, an existing OCR SRT, or another evidence file.
Use the canonical `.ocr.` spelling for new artifacts, while accepting `.orc.` as
a legacy input spelling.
