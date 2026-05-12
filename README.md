# LLM Subtitles

A powerful tool to automatically download, transcribe, and translate YouTube videos into bilingual subtitles using OpenAI's GPT models.

## Features

-   **YouTube Downloader**: Automatically extracts audio and metadata from YouTube links.
-   **Smart Transcription**: Uses **OpenAI Whisper**, **Google Speech-to-Text**, or optional **Typhoon Whisper Large v3** for Thai audio.
-   **LLM Translation**: Translates subtitles into your target language (e.g., Simplified Chinese) using **GPT-4o**, preserving context and nuance.
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
    This installs the core downloader/OpenAI packages plus the local Typhoon Whisper runtime.

3.  **GPU note for local Typhoon ASR**:
    `requirements.txt` includes `torch` for [`typhoon-ai/typhoon-whisper-large-v3`](https://huggingface.co/typhoon-ai/typhoon-whisper-large-v3). If you need a specific CUDA build, install the PyTorch wheel that matches your GPU/driver after installing the requirements.

## Configuration

For security, this project uses **Environment Variables** to manage API keys. Do not hardcode keys in files.

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

2.  **Using the GUI**:
    -   **YouTube URL**: Paste the video link.
    -   **Settings**: Select Source/Target languages and Model (e.g., `gpt-4o`).
    -   **Engine**: Choose `Whisper`, `Google`, `Typhoon`, or `gpt-4o-transcribe-diarize`.
    -   **Typhoon**: Select `Typhoon` to use the local Typhoon Whisper Large v3 model for transcription.
    -   **GPT-4o Diarize**: Select `gpt-4o-transcribe-diarize` to use OpenAI's newer diarized transcription model. The Whisper prompt field is ignored for this model.
    -   **Start Processing**: Click to begin. The logs will show progress.

## Output

All generated files are saved in the `output/` directory, organized by:

-   `output/original/`: Raw audio and original subtitles.
-   `output/translated/`: Final subtitle files.

### Final Files
-   **`[Title].lang.srt`**: The translated subtitles only.
-   **`[Title].lang.bilingual.srt`**: Dual-language subtitles (Translated line first, Original line second).

## License

MIT
