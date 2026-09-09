# Local ASR fallback

Use this only when `prepare` returns `needs_local_asr` or the user explicitly forces audio.

In a `Subtitle Projects` evidence workflow, the existing project MP4 is the
preferred audio source. Read [project-evidence.md](project-evidence.md); do not
redownload the soundtrack merely because the ordinary job-oriented CLI expects a
downloaded audio path. If no documented direct-media adapter is present, report
that interface gap rather than inventing a command or silently using an API.

Run:

```bash
python -m codex_subtitles doctor
```

Supported on-device backends:

- `mlx-whisper`: preferred automatically on Apple Silicon when installed. The default model is `mlx-community/whisper-large-v3-turbo`.
- `openai-whisper-local`: the open-source Whisper Python package running locally. The default model is `medium`.

Neither backend sends audio to the OpenAI API. A model may be downloaded from its distribution host on first use.

Example:

```bash
python -m codex_subtitles transcribe-local "JOB_DIR" \
  --backend auto \
  --language th \
  --enhance mild
```

Enhancement choices are `off`, `mild`, and `strong_ffmpeg`. Start with `off` or `mild`; strong filtering can damage quiet speech. Transcription and enhanced audio are cached in the job directory.

If no backend is installed, explain the one-time dependency and ask before installing it. Never fall back to Whisper API, Realtime API, Google Speech, or another paid service without explicit user authorization.
