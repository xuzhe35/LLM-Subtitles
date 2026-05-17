import argparse
import json
import os
import re
from openai import OpenAI
from utils import downloader, transcriber, translator, subtitle_formatter

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

# Characters that are unsafe in filenames on common filesystems (Windows is the strictest).
_FS_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_FILENAME_LEN = 100

# Strings commonly found in unfilled config templates / placeholders.
_API_KEY_PLACEHOLDER_MARKERS = (
    "YOUR_OPENAI_API_KEY",
    "YOUR_API_KEY",
    "YOUR-KEY",
    "REPLACE_ME",
    "<YOUR_KEY>",
    "EXAMPLE",
)


def validate_openai_api_key(api_key):
    """
    Validate an OpenAI API key value pulled from config or env.

    Returns (is_valid, normalized_key_or_None, reason).
    - Strips surrounding whitespace and stray quotes (a common config.json mistake).
    - Rejects empty / placeholder values with a specific reason.
    - Warns (but does not reject) keys missing the standard 'sk-' prefix so
      self-hosted proxies and Azure-style endpoints still work.
    """
    if api_key is None:
        return False, None, (
            "Missing OpenAI API key. Set OPENAI_API_KEY or add 'openai_api_key' to config.json."
        )

    key = str(api_key).strip().strip("'\"")
    if not key:
        return False, None, "OpenAI API key is empty after trimming whitespace/quotes."

    upper = key.upper()
    for marker in _API_KEY_PLACEHOLDER_MARKERS:
        if marker in upper:
            return False, None, (
                f"OpenAI API key looks like an unfilled placeholder ({marker!r}). "
                "Replace it with a real key in config.json or OPENAI_API_KEY."
            )

    # Deliberately not enforcing a minimum length / sk- prefix here: self-hosted
    # proxies and Azure deployments can ship keys in arbitrary formats. We let
    # the actual API call surface "invalid_api_key" errors for those cases.
    return True, key, None


def sanitize_filename(title, fallback="video"):
    """
    Produce a filesystem-safe filename from a (possibly Unicode) title.

    - Unicode letters/digits (CJK, Cyrillic, etc.) are preserved.
    - Path-unsafe characters (<>:"/\\|?*, control chars) are replaced with '_'.
    - Leading/trailing whitespace, dots and underscores are stripped (Windows
      forbids trailing dots/spaces in file names).
    - If the result is empty, returns `fallback`.
    - Length is capped to avoid hitting filesystem limits.
    """
    if not title:
        return fallback

    # Normalize whitespace, then replace unsafe characters.
    cleaned = _FS_UNSAFE_CHARS.sub("_", str(title))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Drop leading/trailing dots, spaces and underscores.
    cleaned = cleaned.strip(" ._")

    if not cleaned:
        return fallback

    # Require at least one alphanumeric character. This rejects titles that
    # are entirely emoji or punctuation (e.g. "!?!?", "😀🎉"), which would
    # otherwise produce filenames that are technically valid but unreadable
    # and prone to overwriting each other across runs.
    if not any(c.isalnum() for c in cleaned):
        return fallback

    if len(cleaned) > _MAX_FILENAME_LEN:
        cleaned = cleaned[:_MAX_FILENAME_LEN].rstrip(" ._")
        if not cleaned or not any(c.isalnum() for c in cleaned):
            return fallback

    return cleaned

def load_config():
    config_path = os.path.join(PROJECT_ROOT, "config.json")
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r") as f:
        return json.load(f)

def resolve_output_dir(config, explicit=None):
    """
    Resolve the output directory. Priority: explicit arg > env > config > default.
    Relative paths are resolved against PROJECT_ROOT, not CWD, so the location
    is stable regardless of how the app is launched.
    """
    candidate = explicit or get_config_value(
        config,
        env_keys=["LLM_SUBTITLES_OUTPUT_DIR"],
        config_keys=["output_dir"],
        default=DEFAULT_OUTPUT_DIR,
    )
    if not os.path.isabs(candidate):
        candidate = os.path.join(PROJECT_ROOT, candidate)
    return os.path.abspath(candidate)

def get_config_value(config, env_keys, config_keys=None, default=None):
    """
    Resolve config from environment variables first, then config.json keys.
    """
    for env_key in env_keys:
        value = os.getenv(env_key)
        if value:
            return value

    if config_keys:
        for key in config_keys:
            value = config.get(key)
            if value:
                return value

    return default

# The OpenAI SDK's default request timeout is 600s (10 min). That works fine
# for short whisper-1 chunks, but gpt-4o-transcribe-diarize regularly takes
# longer on multi-minute audio because diarization runs on top of the ASR
# pass. A single ReadTimeout aborts the whole job (combined with the SDK's
# default `max_retries=2`, we previously *disabled* retries entirely, so any
# transient hiccup also killed the run). We pin both knobs here:
#
#   - timeout = 30 min   → covers diarize on chunks up to ~5–10 min audio.
#                          Pair with utils.transcriber.DEFAULT_DIARIZE_MAX_SEGMENT_MS
#                          which keeps each chunk well below this ceiling.
#   - max_retries = 3    → SDK does exponential backoff on ReadTimeout / 5xx /
#                          429. Three attempts is enough to ride out the
#                          typical transient.
OPENAI_REQUEST_TIMEOUT_SEC = 30 * 60  # 30 minutes
OPENAI_MAX_RETRIES = 3


def _build_openai_client(api_key):
    """
    Construct the OpenAI SDK client used for transcription + translation.

    Pulled out into its own helper so the timeout/retry policy is testable
    without having to mock the rest of process_video().
    """
    return OpenAI(
        api_key=api_key,
        timeout=float(OPENAI_REQUEST_TIMEOUT_SEC),
        max_retries=OPENAI_MAX_RETRIES,
    )


def ensure_dirs(base_path):
    dirs = {
        'original': os.path.join(base_path, 'original'),
        'translated': os.path.join(base_path, 'translated')
    }
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    return dirs

def process_video(url, lang=None, model=None, force_audio=False, source_lang=None, use_vad=False, whisper_prompt=None, max_segment_sec=None, engine='whisper', progress_callback=print, download_progress_callback=None, output_dir=None):
    """
    Main processing logic, callable by UI.
    progress_callback: function to receive log strings.
    download_progress_callback: function to receive yt-dlp percent string (e.g. "45.0%").
    """
    config = load_config()

    raw_api_key = get_config_value(
        config,
        env_keys=["OPENAI_API_KEY"],
        config_keys=["openai_api_key"]
    )
    is_valid, api_key, reason = validate_openai_api_key(raw_api_key)
    if not is_valid:
        progress_callback(f"Error: {reason}")
        return
    if not api_key.startswith("sk-"):
        progress_callback(
            f"Warning: OpenAI API key does not start with 'sk-' (got prefix {api_key[:4]!r}). "
            "Proceeding anyway — this is OK for self-hosted proxies / Azure endpoints."
        )

    client = _build_openai_client(api_key)
    target_lang = lang if lang else get_config_value(
        config,
        env_keys=["DEFAULT_TARGET_LANGUAGE"],
        config_keys=["default_target_language"],
        default="Simplified Chinese"
    )
    Model = model if model else get_config_value(
        config,
        env_keys=["OPENAI_MODEL"],
        config_keys=["model"],
        default="gpt-4o"
    )

    progress_callback(f"Processing URL: {url} | Target: {target_lang}")

    # Setup Output Directories
    output_root = resolve_output_dir(config, explicit=output_dir)
    progress_callback(f"Output directory: {output_root}")
    dirs = ensure_dirs(output_root)
    
    # 1. Get Video Info
    progress_callback("Fetching video info...")
    info = downloader.get_video_info(url)
    if not info:
        progress_callback("Error: Failed to get video info.")
        return

    video_title = info.get('title', 'video')
    # Fall back to the video id (yt-dlp always provides it) so non-ASCII or
    # punctuation-only titles never collapse to an empty filename.
    fallback_name = info.get('id') or 'video'
    safe_title = sanitize_filename(video_title, fallback=fallback_name)
    if safe_title != video_title:
        progress_callback(f"Sanitized title for filenames: {safe_title!r} (from {video_title!r})")
    
    # Variables to track
    original_segments = []
    translated_segments = []
    
    # 2. Check for Manual Subtitles (unless forced audio)
    manual_subs = info.get('subtitles', {})
    found_manual_code = None
    
    if force_audio:
        progress_callback("Force Audio Source enabled: Skipping manual subtitles check.")
    else:
        progress_callback("Checking for manual subtitles...")
        
        target_lang_codes = ['zh-Hans', 'zh-CN', 'zh-SG', 'zh-Hans-CN'] if 'Chinese' in target_lang else []
        
        for code in manual_subs:
            if code in target_lang_codes or (target_lang.lower() in code.lower()):
                found_manual_code = code
                progress_callback(f"Found manual subtitle for target language: {code}")
                break
                
        if not found_manual_code and manual_subs:
            progress_callback("No manual subtitle in target language found, using fallback manual subtitle.")
            priority_langs = ['en', 'en-US', 'ja', 'ko']
            for l in priority_langs:
                if l in manual_subs:
                    found_manual_code = l
                    break
            if not found_manual_code:
                found_manual_code = list(manual_subs.keys())[0]

    if found_manual_code:
        progress_callback(f"Downloading manual subtitle: {found_manual_code}")
        original_sub_path_base = os.path.join(dirs['original'], safe_title)
        expected_filename = downloader.download_manual_subtitle(url, found_manual_code, original_sub_path_base, progress_hook=download_progress_callback)
        
        if not expected_filename or not os.path.exists(expected_filename):
             potential = f"{original_sub_path_base}.{found_manual_code}.vtt"
             if os.path.exists(potential):
                 expected_filename = potential
             else:
                 progress_callback(f"Error: Could not find downloaded subtitle file.")
                 return
                 
        progress_callback(f"Original subtitle saved to: {expected_filename}")

        # Parse original
        if expected_filename.endswith('.vtt'):
            original_segments = subtitle_formatter.parse_vtt(expected_filename)
        else:
             # Assume SRT or try parsing VTT logic?
             # For now assume VTT as yt-dlp default
             original_segments = subtitle_formatter.parse_vtt(expected_filename)

        # Logic for fallback translation vs direct use
        # If we found EXACT match manual sub, we might want to just output it.
        # But user might still want "translation" if the manual sub is not in target lang.
        
        # If found_manual_code is in target_lang_codes, then it is already translated.
        is_target = False
        if 'Chinese' in target_lang:
             if found_manual_code in ['zh-Hans', 'zh-CN', 'zh-SG', 'zh-Hans-CN', 'zh']:
                 is_target = True
        elif target_lang.lower() in found_manual_code.lower():
             is_target = True

        if is_target:
            # Matches target language. 
            progress_callback("Manual subtitle matches target language. Generating SRTs...")
            translated_segments = original_segments # It IS the translated one
        else:
            # Fallback manual. Needs translation.
            progress_callback(f"Translating {len(original_segments)} segments to {target_lang}...")
            translated_segments = translator.translate_segments(client, original_segments, target_lang, Model, progress_callback=progress_callback)

    else:
        # 3. Audio Extraction & AI Flow
        if not force_audio:
            progress_callback("No manual subtitles found. Proceeding with AI extraction...")
        else:
             progress_callback("Proceeding with AI extraction (Forced)...")
        
        audio_file_path = os.path.join(dirs['original'], f"{safe_title}_audio")
        
        # Check if audio already exists (try common extensions)
        audio_path = None
        for ext in ['.mp3', '.m4a', '.wav', '.opus', '.webm']:
            candidate = audio_file_path + ext
            if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                audio_path = candidate
                break
        
        if audio_path:
            progress_callback(f"Audio already exists: {os.path.basename(audio_path)}, skipping download.")
        else:
            progress_callback("Downloading audio...")
            audio_path = downloader.download_audio(url, audio_file_path, progress_hook=download_progress_callback)
            if not audio_path:
                 progress_callback("Error: Audio download failed.")
                 return

        transcription_model = transcriber.resolve_transcription_model(engine, source_lang)
        progress_callback(f"Resolved transcription model: {transcription_model}")
        progress_callback(f"Transcribing audio ({transcription_model})...")

        google_api_key = None
        if transcription_model == transcriber.TRANSCRIPTION_MODEL_GOOGLE:
            google_api_key = get_config_value(
                config,
                env_keys=["GOOGLE_API_KEY"],
                config_keys=["google_api_key", "Google API Key"]
            )
            if not google_api_key:
                 progress_callback("Error: Google engine requires GOOGLE_API_KEY or config.json google_api_key.")
                 return

        transcript = transcriber.transcribe_audio(
            client, audio_path, source_lang=source_lang, use_vad=use_vad, 
            whisper_prompt=whisper_prompt, max_segment_sec=max_segment_sec,
            engine=engine, google_api_key=google_api_key,
            transcription_model=transcription_model, progress_callback=progress_callback
        )
        if not transcript:
             progress_callback("Error: Transcription failed.")
             return

        original_segments_raw = transcript.segments if hasattr(transcript, 'segments') else []
        # Standardize structure from whisper object to dict
        for s in original_segments_raw:
             if isinstance(s, dict):
                 start = s['start']
                 end = s['end']
                 text = s['text']
             else:
                 start = s.start
                 end = s.end
                 text = s.text

             original_segments.append({
                 'start': start,
                 'end': end,
                 'text': text
             })

        progress_callback("Translating segments (LLM)...")
        translated_segments = translator.translate_segments(client, original_segments, target_lang, Model, progress_callback=progress_callback)
    
    # Generate Outputs
    progress_callback("Generating final files...")
    
    # 1. Translated SRT
    srt_path = os.path.join(dirs['translated'], f"{safe_title}.{target_lang}.srt")
    subtitle_formatter.generate_srt(translated_segments, srt_path)
    progress_callback(f"Translated SRT saved: {srt_path}")
    
    # 2. Bilingual SRT
    bilingual_path = os.path.join(dirs['translated'], f"{safe_title}.{target_lang}.bilingual.srt")
    subtitle_formatter.generate_bilingual_srt(original_segments, translated_segments, bilingual_path, progress_callback=progress_callback)
    progress_callback(f"Bilingual SRT saved: {bilingual_path}")
    
    progress_callback("Done!")

def main():
    parser = argparse.ArgumentParser(description="YouTube Subtitle Generator")
    parser.add_argument("url", help="YouTube Video URL")
    parser.add_argument("--lang", help="Target Language (overrides config)")
    parser.add_argument("--model", help="OpenAI Model (overrides config)")
    parser.add_argument("--force-audio", action="store_true", help="Force audio extraction even if manual subtitles exist")
    parser.add_argument("--source-lang", help="Source audio language ISO code (e.g. en, ja, th)", default=None)
    parser.add_argument("--use-vad", action="store_true", help="Enable Voice Activity Detection to filter silence/noise")
    parser.add_argument("--whisper-prompt", help="Prompt to guide Whisper transcription", default=None)
    parser.add_argument("--max-segment-sec", type=int, help="Max segment duration in seconds (default: 600)", default=None)
    parser.add_argument("--engine", help="Transcription engine/model: 'whisper', 'google', 'typhoon', 'gpt-4o-transcribe-diarize', or 'auto'", default='whisper')
    parser.add_argument("--output-dir", help="Output directory (overrides config). Relative paths resolve against the project root.", default=None)
    args = parser.parse_args()

    process_video(
        args.url, args.lang, args.model, args.force_audio,
        source_lang=args.source_lang, use_vad=args.use_vad,
        whisper_prompt=args.whisper_prompt, max_segment_sec=args.max_segment_sec,
        engine=args.engine, output_dir=args.output_dir
    )

if __name__ == "__main__":
    main()
