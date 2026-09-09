# LLM Subtitles

A powerful tool to automatically download, transcribe, and translate YouTube videos into bilingual subtitles using OpenAI's GPT models.

## 项目代码、工具与 Skill 导航

本仓库包含字幕应用源码、命令行工具、测试、项目 Skill 及其完整说明。

| 入口 | 内容 |
| --- | --- |
| [字幕翻译 Skill](.agents/skills/youtube-subtitles/SKILL.md) | 字幕获取、证据融合、翻译、润色与双语 SRT 工作流 |
| [硬字幕 OCR Skill](.agents/skills/hard-subtitle-ocr/SKILL.md) | 从本地视频提取画面字幕并复核时间轴 |
| [字幕翻译参考说明](.agents/skills/youtube-subtitles/references/) | 工作流、翻译约定、本地转录及参考文档处理 |
| [OCR 参考说明](.agents/skills/hard-subtitle-ocr/references/ocr-workflow.md) | OCR 提取、复核及交付步骤 |
| [项目执行约定](AGENTS.md) | 本地视频时间轴优先等项目要求 |
| [字幕服务源码](codex_subtitles/) | 字幕来源、OCR、本地 ASR、翻译任务管理、时间轴与导出 |
| [辅助工具](tools/) | 音频评估、转录路线比较及翻译润色工具 |
| [通用处理模块](utils/) | 音频处理、转录、翻译、对齐与字幕格式化 |
| [测试](tests/) | 单元测试和可选的本地集成测试 |
| [项目辅助脚本](Subtitle%20Projects/) | 各字幕项目保留的处理、复核和交付脚本 |

两个 Skill 的 `agents/openai.yaml` 和 `references/` 说明均随源码提交。
默认字幕 Skill 使用 Codex 订阅工作流；下文介绍的传统 GUI/API 路线需要单独配置，
只有明确选择该路线时才使用相应 API。

项目辅助脚本保留原目录，部分依赖对应项目的本地素材或本机路径，使用前需按实际环境调整。
视频、下载音频、字幕成品、中间证据、参考原稿、虚拟环境、模型缓存、密钥和本机配置
不纳入此次源码同步；历史上已跟踪的测试音频保持原状。

## Features

-   **YouTube Downloader**: Automatically extracts audio and metadata from YouTube links.
-   **Smart Transcription**: Uses **OpenAI Whisper**, **Google Speech-to-Text**, or **gpt-4o-transcribe-diarize**.
-   **LLM Translation**: Translates subtitles into your target language (e.g., Simplified Chinese) using **GPT-4o**, preserving context and nuance.
-   **Audio-native Realtime Translation**: Streams the downloaded soundtrack directly to **gpt-realtime-translate**, without an intermediate Whisper/GPT-4o translation pass.
-   **Whole-video Context Polish**: Uses a resumable GPT text pass to merge fragmented cues, repair sentence boundaries, and keep terminology, verbal tics, and onomatopoeia consistent across the full video.
-   **High Quality Transcribe + LLM route (opt-in)**: A dual-branch offline pipeline — `gpt-transcribe` owns the canonical transcript, Whisper/diarize own trusted timing, deterministic alignment joins them, and GPT-5.6 translates with whole-program context and selective escalation. Every stage is checkpointed and reusable across target languages.
-   **Bilingual Output**: Generates bilingual SRT files (Target Language + Original) for learning and verification.
-   **VAD Support**: Built-in Voice Activity Detection to filter silence and noise.
-   **GUI Interface**: Easy-to-use graphical interface built with Tkinter.

## Prerequisites

1.  **Python 3.8+**
2.  **FFmpeg**: Required for audio extraction and processing.
    -   **Windows**: [Download FFmpeg](https://ffmpeg.org/download.html), extract it, and add the `bin` folder to your System PATH.
    -   **Mac/Linux**: Install via `brew install ffmpeg` or `sudo apt install ffmpeg`.
3.  **API Keys**:
    -   **OpenAI API Key**: Required for translation and Whisper.
    -   **Google Cloud API Key** (Optional): Required if using Google Speech engine.

## Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/xuzhe35/LLM-Subtitles.git
    cd LLM-Subtitles
    ```

2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
    This installs only the downloader, API, and realtime networking packages;
    no local machine-learning model runtime is required.

## Configuration

For security, this project uses **Environment Variables** to manage API keys. Do not hardcode keys in files.

The app automatically loads an ignored `.env.local` file from the project root:

```dotenv
OPENAI_API_KEY=your-sk-proj-key
```

An already-exported environment variable takes precedence over `.env.local`.

### Windows (PowerShell)
```powershell
$env:OPENAI_API_KEY="your-sk-..."
$env:GOOGLE_API_KEY="your-google-key" # Optional
```

### Mac/Linux
```bash
export OPENAI_API_KEY="your-sk-..."
export GOOGLE_API_KEY="your-google-key" # Optional
```

*Note: You can also set these in your IDE configurations.*

## Usage

1.  **Run the application**:
    ```bash
    python main.py
    ```

    Or launch it by double-clicking the platform-specific script in the project
    folder:

    - macOS: `Start LLM Subtitles.command`
    - Windows: `Start LLM Subtitles.bat`

    Each launcher changes to the project directory, activates `.venv`, and
    starts `main.py`. A virtual environment must be created separately on each
    operating system because macOS and Windows environments are not portable.

2.  **Using the GUI**:
    -   **YouTube URL**: Paste the video link.
    -   **Processing Mode**: `Fast / Realtime Translation` (default, current behavior), `High Quality / Transcribe + LLM` (dual ASR passes + context-aware LLM translation with reusable intermediate files), or `Legacy` (manual subs / ASR + text translation). Controls that do not apply to the selected mode are disabled.
    -   **Translation Model**: `gpt-realtime-translate` is the default and sends the soundtrack directly to the realtime translation endpoint. Choose `gpt-4o` to use the existing ASR-then-text-translation pipeline.
    -   **Engine**: Choose `Whisper`, `Google`, or `gpt-4o-transcribe-diarize` for the non-Realtime pipeline.
    -   **GPT-4o Diarize**: Select `gpt-4o-transcribe-diarize` to use OpenAI's newer diarized transcription model. The Whisper prompt field is ignored for this model.
    -   **Audio Enhance**: Choose `Off`, `Mild`, or `Strong FFmpeg` before transcription. Start with `Mild` for noisy Thai speech.
    -   **Global Context Polish**: Enabled by default for Realtime Translation. It keeps the raw JSON/SRT, then writes separate polished artifacts. `gpt-5.6` is the quality-first default; the field remains editable.
    -   **Start Processing**: Click to begin. The logs will show progress.

### CLI: direct realtime audio translation

```bash
python youtube_subtitle_trans.py "YOUTUBE_URL" \
  --model gpt-realtime-translate \
  --lang "Simplified Chinese"
```

This mode always downloads the source audio, converts it to 24 kHz mono PCM16,
and streams it at realtime speed to `/v1/realtime/translations`. Audio is sent
in 200 ms frames. It skips manual subtitles, the selected ASR engine, and GPT-4o
text translation.

Long audio uses resumable 10-minute translation sessions with a 10-second
overlap. Each successfully closed session is atomically saved to a
`*.resume.json` checkpoint. A failed session is retried with exponential
backoff; running the same video/model/language again skips completed sessions
and resumes at the first incomplete one. `--max-segment-sec` (or the GUI Chunk
Size control) changes the recovery interval. Keep the checkpoint until the
final SRT has been generated.

The run also saves a raw metadata JSON alongside model-specific SRT files. New
runs request a `gpt-realtime-whisper` source transcript in the same Realtime
session so the text-polishing pass can compare source and translation. Older
JSON files without source text are still supported in target-language-only
editing mode.

Global polishing is enabled by default. It first derives a compact whole-video
style/terminology context, then edits punctuation-aware windows with surrounding
read-only context. The model returns only raw cue IDs and polished text; the
application rebuilds timestamps from the original cues and rejects skipped,
duplicated, reordered, or non-adjacent IDs. Each completed window is saved to a
`*.polish.resume.json` checkpoint.

Disable the extra text-model pass when you only want the raw interpretation:

```bash
python youtube_subtitle_trans.py "YOUTUBE_URL" \
  --model gpt-realtime-translate \
  --no-polish-realtime
```

### CLI: High Quality Transcribe + LLM route

The high-quality route is opt-in and never replaces or reroutes Realtime jobs:

```bash
python youtube_subtitle_trans.py "YOUTUBE_URL" \
  --pipeline transcribe-llm \
  --lang "Simplified Chinese" \
  --source-languages th,en \
  --transcription-prompt "Bangkok street-food tour, casual vlog" \
  --transcription-keyword "ACME" \
  --timing-model auto
```

How it works:

1. **Semantic branch** — `gpt-transcribe` produces one canonical whole-program
   transcript (whole file at ≤24 MB, natural-boundary chunks above it, with
   context handoff and overlap deduplication).
2. **Timing branch** — `whisper-1` word timestamps (or
   `gpt-4o-transcribe-diarize` when speaker labels are needed; `--timing-model
   auto` picks for you) remain the only source of truth for time. No LLM ever
   creates or edits a timestamp.
3. **Deterministic alignment** — anchors + bounded sequence alignment map the
   canonical text onto trusted timing, with per-cue confidence. Low-confidence
   spans fall back to the timing transcript text and the disagreement is
   recorded in the quality report.
4. **Whole-program context** — GPT-5.6 Terra builds a compact source context
   pack and a per-language target policy before any translation starts.
5. **Windowed translation** — strict cue-ID coverage, adjacent-only merges,
   15-second merge limit, no cross-speaker merges, and deterministic
   number/identifier checks. Difficult windows selectively escalate to
   GPT-5.6 Sol (`--no-translation-escalation` disables this; escalations are
   capped and recorded).

Every stage writes a hash-identified, atomically saved artifact, so reruns
resume where they stopped and a second target language reuses the semantic
transcript, timing, alignment, and source context. Add `--strict-high-quality`
(default) to stop with a resumable error instead of degrading when the
semantic transcript fails.

Compare route outputs structurally before changing any default:

```bash
python tools/evaluate_transcription_routes.py \
  --arm realtime="output/translated/VIDEO.gpt-realtime-translate.LANG.polished.json" \
  --arm transcribe_llm="output/translated/VIDEO.LANG.translated.json"
```

### Polish an existing Realtime JSON

You can improve a completed JSON without downloading or translating its audio
again:

```bash
python tools/polish_realtime_json.py \
  "output/translated/VIDEO.gpt-realtime-translate.Simplified Chinese.json"
```

Before spending API tokens, validate the file, window plan, source transcript
availability, and fragmentation metrics offline:

```bash
python tools/polish_realtime_json.py "PATH_TO_JSON" --dry-run
```

The raw JSON is never modified. Interrupted polishing resumes from its first
incomplete window.

### CLI: noisy Thai audio

For noisy Thai talks, start with conservative FFmpeg enhancement and shorter
chunks:

```bash
python youtube_subtitle_trans.py "YOUTUBE_URL" \
  --force-audio \
  --source-lang th \
  --engine gpt-4o-transcribe-diarize \
  --max-segment-sec 90 \
  --enhance-audio \
  --enhance-mode mild
```

`mild` keeps denoise conservative. `strong_ffmpeg` is available for difficult
samples with construction noise, children shouting, or heavy background noise,
but it can damage weak speech details. Compare outputs before using it for a
full long video.

### Audio evaluation

You can compare transcript JSON files without re-running ASR:

```bash
python tools/evaluate_audio_pipeline.py \
  --transcript raw=eval_results/sample.raw.json \
  --transcript mild=eval_results/sample.mild.json \
  --transcript strong=eval_results/sample.strong.json \
  --output eval_results/sample.report.md
```

The report summarizes segment count, empty segments, repeated text, unique text,
speech seconds, and timeline span. These metrics are not a replacement for
listening, but they make raw/mild/strong comparisons repeatable.

## Architecture

The processing flow is organized as a small pipeline:

1.  Resolve configuration, API clients, and output directories.
2.  Fetch YouTube metadata and choose a source: manual subtitles when useful,
    otherwise downloaded audio. `gpt-realtime-translate` always chooses audio.
3.  Optionally enhance audio with FFmpeg speech-prep filters.
4.  Either transcribe through the selected ASR backend and translate normalized
    segments, or stream PCM audio directly through `gpt-realtime-translate`.
5.  Save raw Realtime JSON/SRT artifacts before any optional post-processing.
6.  For Realtime mode, optionally analyze whole-video context and polish
    resumable subtitle windows with strict cue/timeline validation.
7.  Write polished JSON, translated SRT, and bilingual SRT files.

`youtube_subtitle_trans.py` owns pipeline routing (`realtime`,
`transcribe_llm`, `legacy`). `utils/transcriber.py` owns legacy ASR routing,
chunk planning, and transcription execution. Shared segment normalization
lives in `utils/segments.py`. Audio enhancement lives in
`utils/audio_enhancer.py`; the dedicated WebSocket audio path lives in
`utils/realtime_translator.py`. Context analysis, window planning, Structured
Outputs validation, quality metrics, and polish checkpoints live in
`utils/subtitle_polisher.py`, with the deterministic window primitives shared
through `utils/subtitle_windows.py`.

The high-quality route is composed of `utils/semantic_transcriber.py`
(canonical `gpt-transcribe` transcript), `utils/transcript_aligner.py`
(deterministic monotonic alignment), `utils/contextual_translator.py`
(context pack, target policy, windowed translation with selective
escalation), and `utils/high_quality_pipeline.py` (stage orchestration,
checkpoints, and artifacts). The orchestrator is imported lazily so Realtime
jobs never touch it.

## Testing

Run the full unit suite from the project root:

```bash
python -m unittest discover -v
```

## Output

All generated files are saved in the `output/` directory, organized by:

-   `output/original/`: Raw audio and original subtitles.
-   `output/translated/`: Final subtitle files.

### Final Files
-   **`[Title].lang.srt`**: The translated subtitles only.
-   **`[Title].lang.bilingual.srt`**: Dual-language subtitles (Translated line first, Original line second).
-   **`[Title].gpt-realtime-translate.lang.srt`**: Audio-native realtime translation output.
-   **`[Title].gpt-realtime-translate.lang.json`**: Source/translated transcripts, heuristic cue timing, and non-audio transcript events retained for later comparison and fusion.
-   **`[Title].gpt-realtime-translate.lang.resume.json`**: Per-session recovery checkpoint used to resume interrupted long-video translations.
-   **`[Title].gpt-realtime-translate.lang.polish.resume.json`**: Whole-video context and per-window polishing checkpoint.
-   **`[Title].gpt-realtime-translate.lang.polished.json`**: Polished segments, global context pack, and before/after quality metrics.
-   **`[Title].gpt-realtime-translate.polished.lang.srt`**: Context-polished translated subtitles.
-   **`[Title].gpt-realtime-translate.polished.lang.bilingual.srt`**: Context-polished bilingual subtitles when source transcript text is available.

### High Quality (Transcribe + LLM) files

-   **`original/[Title].transcribe.semantic.json`**: Canonical `gpt-transcribe` transcript with chunk statuses (also the resume checkpoint).
-   **`original/[Title].timing.whisper.json`** / **`original/[Title].timing.diarize.json`**: Trusted timing transcript with word timestamps or speakers.
-   **`original/[Title].aligned.json`**: Confidence-scored timed source cues plus unresolved spans.
-   **`original/[Title].source-context.json`**: Target-independent whole-program context pack (reused across target languages).
-   **`translated/[Title].lang.target-policy.json`**: Target-language subtitle policy.
-   **`translated/[Title].lang.translation.resume.json`**: Per-window translation checkpoint.
-   **`translated/[Title].lang.translated.json`**: Final cues with source IDs, speakers, issues, and escalation records.
-   **`translated/[Title].lang.srt`** / **`translated/[Title].lang.bilingual.srt`**: Final subtitles.
-   **`translated/[Title].lang.quality.json`**: Alignment stats, structural gates, readability metrics, and every recorded fallback.

## License

MIT
