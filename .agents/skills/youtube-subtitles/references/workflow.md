# Local service workflow

All commands run from the repository root. Prefer `.venv/bin/python`; the examples use `python` for readability.

For a child directory under `Subtitle Projects` containing `URL.md`, a local MP4,
or OCR SRT evidence, use [project-evidence.md](project-evidence.md) before choosing
a source. The table below is the ordinary single-source workflow.

## Services

| Service | Command | Responsibility |
| --- | --- | --- |
| Runtime doctor | `python -m codex_subtitles doctor` | Report available local ASR backends; never tests an API key. |
| Video source | `python -m codex_subtitles prepare URL` | Inspect YouTube, prefer target/manual/automatic captions, otherwise download audio. |
| Source import | `python -m codex_subtitles import-source FILE` | Create a job from SRT, VTT, or timestamped JSON. |
| Local ASR | `python -m codex_subtitles transcribe-local JOB_DIR` | Transcribe downloaded audio on-device. |
| Window planner | `python -m codex_subtitles plan JOB_DIR` | Create stable owned windows with surrounding context. |
| Translation checkpoint | `windows/*.target.json` | Store Codex-authored translation and polishing results. |
| Validator | `python -m codex_subtitles validate JOB_DIR` | Enforce coverage, order, adjacency, timing, and merge constraints. |
| Exporter | `python -m codex_subtitles finalize JOB_DIR` | Rebuild trusted timings and generate translated/bilingual SRT. |

These are composable local services, not network microservices. They communicate through versioned JSON artifacts so each stage can be inspected, resumed, or replaced independently.

Hard-subtitle extraction is intentionally outside this main workflow. The user
may explicitly invoke `$hard-subtitle-ocr`; this skill consumes its resulting SRT
and sidecars but never starts OCR itself.

## Prepare a YouTube job

```bash
python -m codex_subtitles prepare "YOUTUBE_URL" \
  --target-language "Simplified Chinese" \
  --source-language th
```

Source priority is target-language manual captions, target-language automatic captions, requested/fallback manual captions, requested/fallback automatic captions, then downloaded audio. Use `--force-audio` only when the user explicitly wants local ASR instead of available captions.

The command creates `output/codex_native/<job>/` containing:

- `job.json`: workflow state and paths.
- `artifacts/`: downloaded caption or audio.
- `source.json`: normalized source cues when available.
- `context.json`: Codex-authored whole-video context.
- `windows/`: source windows and resumable target files.
- `final/`: verified output files.

## Import an existing transcript

```bash
python -m codex_subtitles import-source "path/to/source.vtt" \
  --target-language "Simplified Chinese" \
  --source-language en
```

Supported inputs are SRT, VTT, and JSON with a `segments` or `source_segments` array. Each segment needs `start`, `end`, and `text`.

## Resume

Run `python -m codex_subtitles status JOB_DIR`. It lists pending and invalid window IDs. Existing non-empty target windows are never overwritten by `plan`; finish only pending/invalid windows and rerun validation.

## Exit meanings

- `validate` exits 0 only when every window is valid and complete.
- It exits 2 for an incomplete or invalid translation plan.
- Other command failures exit 1 and print a JSON error object.
