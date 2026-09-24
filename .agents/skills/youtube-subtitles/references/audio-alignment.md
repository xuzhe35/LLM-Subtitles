# Local audio timing calibration

Use when the user requests audio-based timing verification, reports late or
drifting subtitles, or asks to change timestamps while retaining reviewed text.
This is forced alignment of existing spoken-language text, separate from fresh
ASR and translation. Do not restart translation jobs for a timing-only request.

## Select and preserve the timing basis

- Follow the user's explicit target. Otherwise follow repository `AGENTS.md`:
  a supplied local video uses that exact file's elapsed time; YouTube is a
  reference unless its timeline is requested. Record media path, SHA-256,
  duration and whether it is a recording or direct source media.
- For a YouTube target, reuse matching direct source audio or download the
  audio-only track. Do not substitute a screen recording with an assumed offset.
  Preserve audio from time zero; record any actual start-time normalization.
- Back up the existing translated and bilingual SRTs and hash both whole files
  and their content excluding timestamp rows. Lock cue IDs, wording, punctuation,
  line breaks and order when the request is timing-only.
- Keep raw captions, OCR, ASR, translation windows and prior deliveries immutable.
  Write alignment evidence under `PROJECT/work/audio-alignment/` and new output
  names containing the actual target and `audio-aligned`.

## Available local runtime

The repository `.venv` has a verified Apple Silicon alignment route:
`stable-ts` 2.19.1 + `mlx-whisper` 0.4.3 + `mlx` 0.32.2. These are the versions
used successfully on 2026-09-24, not a requirement to downgrade newer compatible
installations. Check package availability without importing MLX before loading
the model:

```bash
.venv/bin/python - <<'PY'
from importlib.metadata import version, PackageNotFoundError
for package in ('stable-ts', 'mlx-whisper', 'mlx', 'yt-dlp'):
    try:
        print(package, version(package))
    except PackageNotFoundError:
        print(package, 'not installed')
PY
```

Reuse a complete local model before downloading one. This workspace currently
contains an English model at:

`Subtitle Projects/Everything You See Is a Lie/work/audio-alignment/local-small-en/`

It contains `config.json` and `weights.npz` (481306200 bytes; SHA-256
`1bb29b030aca711a035f7a084a0eefac6251ecc2bdd356fa748858fbad082f5a`).
Treat this as a discoverable cache location, not a required project dependency;
verify it still exists and pass its resolved path. For another spoken language,
use an appropriate multilingual model; `small.en` is English-only.

No install or download permission is needed merely to reuse installed components.
If missing, explain the one-time local dependency/model download and obtain
authorization unless already granted in the active conversation, then install
`stable-ts[mlx]` into the repository environment. This uses no paid API. Do not
read API keys. Do not automatically replace the larger model used for normal ASR
just because a small alignment model is available.

MLX needs Metal access. If the sandbox cannot access the GPU, request the normal
tool execution permission for that local process. Never treat a sandbox Metal
error as proof the model is missing or repeatedly reinstall packages.

## Acquire audio and align through the Python API

There is no `codex_subtitles align-audio` CLI command. Use the installed Python
API in a project-local helper. A working acquisition pattern is:

```bash
.venv/bin/python -m yt_dlp -f bestaudio \
  -o 'PROJECT/work/audio-alignment/youtube-source.%(ext)s' 'YOUTUBE_URL'
ffprobe -v error -show_format -show_streams -of json 'SOURCE_AUDIO'
ffmpeg -nostdin -i 'SOURCE_AUDIO' -vn -ac 1 -ar 16000 -c:a pcm_s16le \
  'PROJECT/work/audio-alignment/source16k.wav'
```

Substitute verified paths, use the environment's available ffmpeg executable,
and avoid overwriting existing evidence. For local delivery, extract from the
selected local video instead. If YouTube returns 403, inspect the installed
downloader and JavaScript runtime before retrying. In the verified run,
`yt-dlp[default]` 2026.8.19 with EJS and an explicit installed Node path resolved
the problem. Preserve logs; do not retry indefinitely or install unrelated tools.

For English on the verified MLX route:

```python
import mlx.core as mx
import stable_whisper

model = stable_whisper.load_mlx_whisper(local_model_path, dtype=mx.float32)
# chunk: mono 16 kHz float32 numpy samples; one source-language cue per line.
result = model.align(
    chunk, '\n'.join(cue_texts), language='en',
    original_split=True, regroup=False, verbose=None, fast_mode=False,
    suppress_silence=True, vad=False, token_step=100,
    remove_instant_words=False,
)
data = result.to_dict()
assert len(data['segments']) == len(cue_texts)
```

Build alignment text from the reviewed spoken-language source corresponding to
each delivery cue. Never align Chinese translations directly to English audio.
Normalize spelled-out URLs or numbers only in alignment input when needed, while
keeping delivery text locked. Save that normalization and the cue/source-ID map.

Process bounded chunks with neighboring cue context. The verified starting point
was 25 owned cues plus 3 neighbors on each side and modest audio padding, using
approximate existing times to select crops. These are tunable values, not fixed
limits; split long spans further. Add each crop's exact offset to returned word
times, emit owned cues once, and persist raw results for inspection/resume.
Invalidate cached results if the media hash, text, crop, model or options change.

## Review acoustic failures before exporting

Treat model timestamps as estimates. Flag large shifts, collapsed cues or
boundary words, low-confidence words, nonmonotonic results and first words
stretched across pauses/music. Useful initial review thresholds are shifts over
0.4 seconds, boundary probability below 0.1 and first-word duration above one
second; tune them to the material. Do not automatically accept a high mean word
probability as proof that a cue boundary is correct.

- Re-align questionable regions with shorter context and different crop edges.
  Repeated clauses can make a long-context alignment skip to the next repetition.
- Cross-check disputed boundaries using independent local ASR with word times,
  e.g. `mlx_whisper.transcribe(chunk, path_or_hf_repo=local_model_path,
  language='en', word_timestamps=True, condition_on_previous_text=False)`.
  Preserve its output as timing evidence; do not overwrite the reviewed text.
- Use waveform/speech-energy evidence where helpful. Background music is not
  silence and can stretch short first words. Independent ASR can share this
  error, so disagreement is not solved by blindly choosing the earlier result.
- When a brief response cannot be located reliably, keep a conservative original
  boundary and disclose it. Record overrides with cue ID, old/new times, reason
  and evidence. Do not claim an unresolved boundary was acoustically verified.
- Check distributed beginning/middle/end samples as well as disagreement clusters.
  Do not impose a whole-video shift without distributed evidence of that shift.

## Export and report

Apply modest reading lead/hold to reviewed acoustic boundaries. The verified run
used 120 ms lead, 180 ms hold and a 700 ms minimum display goal where a gap permits.
These are display choices, not acoustic truth or universal requirements. Clip at
the next cue onset and media end; do not create overlap or change text/segmentation
to conceal timing failures. Use integer milliseconds to avoid float truncation.

Replace only timestamp rows in the locked delivery files. Confirm:

- all cues present exactly once, same IDs/order and positive monotonic times;
- no overlaps or times outside the declared media timeline;
- translated and bilingual files have identical corresponding timestamps;
- non-timestamp content is byte-identical to each backup (including line breaks);
- original evidence and prior delivery hashes remain unchanged.

Report the target media identity/hash, model/backend, evidence actually used,
review/fallback decisions, start/end change statistics and any playback checks.
Distinguish automated analysis and ASR spot checks from actual manual listening.
Name exactly which project and outputs were calibrated; do not imply another
video was calibrated merely because it shares a speaker or conversation.
